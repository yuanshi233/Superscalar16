"""Native dual-fetch, branch prediction, fetch queue, and ISA decode circuits."""

import xml.etree.ElementTree as ET

from circuit import Circuit


def _one_of(c, value, choices):
    return c.lor(*(c.is_(value, choice) for choice in choices))


def build_decode():
    c = Circuit("DecodeOne", "16-bit ISA decode / 57-bit control packet")
    instr = c.input("instr", 16)
    pc = c.input("pc", 16)
    pred = c.input("pred")
    opcode = c.bits(instr, 12, 4)
    r_type = _one_of(c, opcode, range(1, 7))
    branch = _one_of(c, opcode, (10, 11))
    jump = _one_of(c, opcode, (12, 13))
    store = c.is_(opcode, 9)
    load = c.is_(opcode, 8)
    mem = c.lor(load, store)
    halt = c.is_(opcode, 15)
    rw = _one_of(c, opcode, (1, 2, 3, 4, 5, 6, 7, 8, 12, 13, 14))
    base_from_rd = c.lor(branch, store)
    regular_src = _one_of(c, opcode, (1, 2, 3, 4, 5, 6, 7, 8, 13, 14))
    rd = c.mux(rw, c.const(0, 3), c.bits(instr, 9, 3))
    rs1 = c.mux(
        base_from_rd,
        c.mux(regular_src, c.const(0, 3), c.bits(instr, 6, 3)),
        c.bits(instr, 9, 3),
    )
    rs2 = c.mux(
        base_from_rd,
        c.mux(r_type, c.const(0, 3), c.bits(instr, 3, 3)),
        c.bits(instr, 6, 3),
    )
    immediate6 = _one_of(c, opcode, (7, 8, 9, 10, 11, 13, 14))
    imm = c.mux(
        c.is_(opcode, 12),
        c.mux(immediate6, c.const(0, 16), c.extend(c.bits(instr, 0, 6), 16, True)),
        c.extend(c.bits(instr, 0, 9), 16, True),
    )
    alu_op = c.choose(opcode, [c.const(v, 3) for v in (0, 0, 1, 2, 3, 4, 5, 0, 0, 0, 1, 1, 0, 0, 5, 0)])
    alu_src = _one_of(c, opcode, (7, 8, 9, 14))
    # Document field order, from least significant to most significant bit.
    packet = c.pack(opcode, rd, rs1, rs2, imm, rw, load, store, branch,
                    jump, alu_op, alu_src, load, halt, pc, pred)
    for name, value in (("opcode", opcode), ("rd", rd), ("rs1", rs1),
                        ("rs2", rs2), ("imm", imm), ("rw", rw), ("mem", mem),
                        ("store", store), ("branch", branch), ("jump", jump),
                        ("halt", halt), ("packet", packet)):
        c.output(name, value)
    return c


def build_btb():
    c = Circuit("BTB64", "64-entry direct-mapped BTB / two simultaneous queries")
    c.input("clk")
    rst = c.input("rst")
    pc0 = c.input("pc0", 16)
    pc1 = c.input("pc1", 16)
    we = c.input("we")
    update_pc = c.input("update_pc", 16)
    target = c.input("target", 16)
    taken = c.input("taken")
    entries = [c.ref(f"entry{i:02d}", 29) for i in range(64)]
    index = c.bits(update_pc, 0, 6)
    tag = c.bits(update_pc, 6, 10)
    old = c.choose(index, entries)
    old_counter = c.bits(old, 26, 2)
    same = c.land(c.bits(old, 28, 1), c.eq(c.bits(old, 0, 10), tag))
    up = c.mux(c.is_(old_counter, 3), c.inc(old_counter), old_counter)
    down = c.mux(c.is_(old_counter, 0), c.inc(old_counter, -1), old_counter)
    counter = c.mux(same, c.const(2, 2), c.mux(taken, down, up))
    update = c.pack(tag, target, counter, c.const(1))
    for i, entry in enumerate(entries):
        c.reg(entry.name, 29, update, c.land(we, c.is_(index, i)), rst)
    for lane, pc in enumerate((pc0, pc1)):
        entry = c.choose(c.bits(pc, 0, 6), entries)
        hit = c.land(c.bits(entry, 28, 1), c.eq(c.bits(entry, 0, 10), c.bits(pc, 6, 10)))
        c.output(f"pred{lane}", c.land(hit, c.bits(entry, 27, 1)))
        c.output(f"target{lane}", c.bits(entry, 10, 16))
    return c


def build_imem(program):
    if len(program) > 65536 or any(not 0 <= word <= 65535 for word in program):
        raise ValueError("program must contain at most 65536 unsigned 16-bit words")
    c = Circuit("InstructionMemory", "64K x 16 instruction ROM / two read ports")
    pc = c.input("pc", 16)
    addresses = (pc, c.inc(pc))
    contents = "addr/data: 16 16\n" + " ".join(f"{word:04x}" for word in program) + "\n"
    for lane, address in enumerate(addresses):
        x, y = c.loc(height=260)
        rom = c.comp(4, "ROM", x, y, addrWidth=16, dataWidth=16,
                     appearance="classic", label=f"IMEM{lane}")
        ET.SubElement(rom, "a", name="contents").text = contents
        result = c.new(16, f"instruction{lane}")
        # Logisim-evolution 5 classic ROM: address (0,10), output (240,60).
        c.port(x, y + 10, address)
        c.port(x + 240, y + 60, result, True)
        c.output(f"instr{lane}", result)
    return c


def build_fifo():
    c = Circuit("FetchFIFO8", "8-pair fetch FIFO / retains a partially accepted pair")
    c.input("clk")
    rst = c.input("rst")
    enable = c.input("enable")
    flush = c.input("flush")
    consume = c.input("consume", 2)
    write_en = c.input("write_en")
    instr0 = c.input("in_instr0", 16)
    instr1 = c.input("in_instr1", 16)
    pc = c.input("in_pc", 16)
    pred0 = c.input("in_pred0")
    pred1 = c.input("in_pred1")
    next0 = c.input("in_next0", 16)
    next1 = c.input("in_next1", 16)
    head = c.ref("head", 3)
    tail = c.ref("tail", 3)
    count = c.ref("pair_count", 4)
    half = c.ref("half")
    instruction_pairs = [c.ref(f"instructions{i}", 32) for i in range(8)]
    metadata = [c.ref(f"metadata{i}", 50) for i in range(8)]
    words = c.choose(head, instruction_pairs)
    meta = c.choose(head, metadata)
    active = c.land(enable, c.inv(rst), c.inv(flush))
    valid0 = c.land(active, c.nonzero(count))
    valid1 = c.land(valid0, c.inv(half), c.inv(c.bits(meta, 16, 1)))
    full = c.is_(count, 8)
    accepted = c.land(valid0, c.nonzero(consume))
    pop = c.land(accepted, c.lor(c.inv(valid1), c.bits(consume, 1, 1)))
    partial = c.land(valid1, c.is_(consume, 1))
    push = c.land(active, write_en, c.inv(full))
    clear = c.lor(rst, flush)
    c.reg("head", 3, c.inc(head), pop, clear)
    c.reg("tail", 3, c.inc(tail), push, clear)
    c.reg("pair_count", 4, c.sub(c.add(count, c.extend(push, 4)), c.extend(pop, 4)),
          c.lor(push, pop), clear)
    c.reg("half", 1, c.mux(pop, c.const(1), c.const(0)), c.lor(pop, partial), clear)
    write_words = c.pack(instr0, instr1)
    write_meta = c.pack(pc, pred0, pred1, next0, next1)
    for i in range(8):
        entry_we = c.land(push, c.is_(tail, i))
        c.reg(instruction_pairs[i].name, 32, write_words, entry_we, rst)
        c.reg(metadata[i].name, 50, write_meta, entry_we, rst)
    first_pc = c.bits(meta, 0, 16)
    second_pc = c.inc(first_pc)
    first_word = c.bits(words, 0, 16)
    second_word = c.bits(words, 16, 16)
    first_pred = c.bits(meta, 16, 1)
    second_pred = c.bits(meta, 17, 1)
    first_next = c.bits(meta, 18, 16)
    second_next = c.bits(meta, 34, 16)
    for name, value in (
        ("instr0", c.mux(half, first_word, second_word)), ("instr1", second_word),
        ("pc0", c.mux(half, first_pc, second_pc)), ("pc1", second_pc),
        ("pred0", c.mux(half, first_pred, second_pred)), ("pred1", second_pred),
        ("next0", c.mux(half, first_next, second_next)), ("next1", second_next),
        ("valid0", valid0), ("valid1", valid1), ("full", full), ("count", count),
    ):
        c.output(name, value)
    return c


def build_frontend(program: list[int]) -> list[Circuit]:
    decoder = build_decode()
    btb = build_btb()
    imem = build_imem(program)
    fifo = build_fifo()
    c = Circuit("FetchUnit", "Dual fetch / BTB64 / 8-pair FIFO")
    clk = c.input("clk")
    rst = c.input("rst")
    enable = c.input("enable")
    flush = c.input("flush")
    recover_pc = c.input("recover_pc", 16)
    consume = c.input("consume", 2)
    bp_we = c.input("bp_we")
    bp_pc = c.input("bp_pc", 16)
    bp_target = c.input("bp_target", 16)
    bp_taken = c.input("bp_taken")
    pc = c.ref("fetch_pc_reg", 16)
    pc1 = c.inc(pc)
    pc2 = c.inc(pc, 2)
    prediction = c.instance(btb, {
        "clk": clk, "rst": rst, "pc0": pc, "pc1": pc1, "we": bp_we,
        "update_pc": bp_pc, "target": bp_target, "taken": bp_taken,
    }, label="BTB")
    instructions = c.instance(imem, {"pc": pc}, label="IMEM")
    next0 = c.mux(prediction["pred0"], pc1, prediction["target0"])
    next1 = c.mux(prediction["pred1"], pc2, prediction["target1"])
    selected_pc = c.mux(prediction["pred0"], next1, prediction["target0"])
    buffer = c.instance(fifo, {
        "clk": clk, "rst": rst, "enable": enable, "flush": flush,
        "consume": consume, "write_en": c.const(1),
        "in_instr0": instructions["instr0"], "in_instr1": instructions["instr1"],
        "in_pc": pc, "in_pred0": prediction["pred0"], "in_pred1": prediction["pred1"],
        "in_next0": next0, "in_next1": next1,
    }, label="FQ")
    fetch = c.land(enable, c.inv(buffer["full"]), c.inv(flush), c.inv(rst))
    c.reg("fetch_pc_reg", 16, c.mux(flush, selected_pc, recover_pc),
          c.lor(flush, fetch), rst)
    for name in ("instr0", "instr1", "pc0", "pc1", "pred0", "pred1",
                 "next0", "next1", "valid0", "valid1"):
        c.output(name, buffer[name])
    c.output("fetch_pc", pc)
    c.output("fifo_count", buffer["count"])
    return [decoder, btb, imem, fifo, c]
