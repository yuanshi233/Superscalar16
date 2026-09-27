"""Native 16-entry reservation stations and distributed retirement queues."""

from circuit import Circuit


RS_FIELDS = {
    "op": 4, "rd": 6, "qj": 6, "qk": 6, "imm": 16,
    "pc": 16, "sn": 8, "vj": 1, "vk": 1, "dj": 16, "dk": 16,
}
ROB_FIELDS = {
    "op": 4, "rd_new": 6, "rd_old": 6, "rd_logic": 3, "rw": 1,
    "pc": 16, "sn": 8, "pred_next": 16,
}


def _inputs(circuit, fields):
    return {name: circuit.input(name, width) for name, width in fields.items()}


def _rs_cdb_inputs(circuit):
    return _inputs(circuit, {
        f"c{k}_{field}": width
        for k in range(2)
        for field, width in (("valid", 1), ("rd", 6), ("data", 16))
    })


def _rob_cdb_inputs(circuit):
    return _inputs(circuit, {
        f"c{k}_{field}": width
        for k in range(2)
        for field, width in (
            ("valid", 1), ("sn", 8), ("result", 16),
            ("next", 16), ("taken", 1), ("target", 16),
        )
    })


def _sum_bits(circuit, values, width):
    level = [circuit.extend(value, width) for value in values]
    while len(level) > 1:
        level = [circuit.add(level[i], level[i + 1])
                 for i in range(0, len(level), 2)]
    return level[0]


def _rs_entry():
    c = Circuit("RS_Entry", "Reservation entry | two tagged wakeup buses")
    control = _inputs(c, {name: 1 for name in (
        "clk", "rst", "clear", "enable", "wr", "erase",
    )})
    incoming = _inputs(c, RS_FIELDS)
    bus = _rs_cdb_inputs(c)
    reset = c.lor(control["rst"], control["clear"])
    busy = c.ref("Q_busy")
    write = c.land(control["enable"], control["wr"], c.inv(busy))
    erase = c.land(control["enable"], control["erase"], busy)
    c.reg("Q_busy", 1, write, c.lor(write, erase), reset)
    state = {}
    for name, width in RS_FIELDS.items():
        if name not in ("vj", "vk", "dj", "dk"):
            state[name] = c.reg("Q_" + name, width, incoming[name], write, reset)

    def hits(tag):
        return [c.land(bus[f"c{k}_valid"], c.nonzero(bus[f"c{k}_rd"]),
                       c.eq(tag, bus[f"c{k}_rd"])) for k in range(2)]

    def forwarded(match, fallback):
        return c.mux(match[0], c.mux(match[1], fallback, bus["c1_data"]),
                     bus["c0_data"])

    for suffix in ("j", "k"):
        ready_name = "v" + suffix
        data_name = "d" + suffix
        tag_name = "q" + suffix
        ready = c.ref("Q_" + ready_name)
        data = c.ref("Q_" + data_name, 16)
        live_match = hits(state[tag_name])
        wake = c.land(control["enable"], busy, c.inv(ready),
                      c.lor(*live_match))
        incoming_match = hits(incoming[tag_name])
        zero_source = c.is_(incoming[tag_name], 0)
        new_ready = c.lor(incoming[ready_name], zero_source,
                          *incoming_match)
        new_data = c.mux(incoming[ready_name],
                         forwarded(incoming_match, incoming[data_name]),
                         incoming[data_name])
        new_data = c.mux(zero_source, new_data, c.const(0, 16))
        change = c.lor(write, wake)
        state[ready_name] = c.reg(
            "Q_" + ready_name, 1,
            c.mux(write, c.const(1), new_ready), change, reset,
        )
        state[data_name] = c.reg(
            "Q_" + data_name, 16,
            c.mux(write, forwarded(live_match, data), new_data), change, reset,
        )

    c.output("busy", busy)
    c.output("ready", c.land(busy, state["vj"], state["vk"]))
    for name in ("op", "rd", "imm", "pc", "sn"):
        c.output(name, state[name])
    c.output("a", state["dj"])
    c.output("b", state["dk"])
    return c


def _oldest(c, entries, ready_only):
    # The maximum live sequence span is 48, below the modular half-range.
    level = [{"valid": entry["ready" if ready_only else "busy"],
              "sn": entry["sn"], "index": c.const(i, 4)}
             for i, entry in enumerate(entries)]
    while len(level) > 1:
        next_level = []
        for i in range(0, len(level), 2):
            left, right = level[i:i + 2]
            right_older = c.bits(c.sub(right["sn"], left["sn"]), 7, 1)
            pick_right = c.land(right["valid"],
                                c.lor(c.inv(left["valid"]), right_older))
            next_level.append({
                "valid": c.lor(left["valid"], right["valid"]),
                "sn": c.mux(pick_right, left["sn"], right["sn"]),
                "index": c.mux(pick_right, left["index"], right["index"]),
            })
        level = next_level
    return level[0]


def _reservation_station(entry_module, name, ready_only):
    c = Circuit(name, "16-entry " + (
        "oldest-ready reservation station" if ready_only
        else "ordered memory reservation station"
    ))
    control = _inputs(c, {name: 1 for name in (
        "clk", "rst", "clear", "enable", "wr", "take",
    )})
    incoming = _inputs(c, RS_FIELDS)
    bus = _rs_cdb_inputs(c)
    entries = []
    for i in range(16):
        ports = {name: control[name] for name in (
            "clk", "rst", "clear", "enable",
        )}
        ports.update(incoming)
        ports.update(bus)
        ports["wr"] = c.ref(f"allocate_{i}")
        ports["erase"] = c.ref(f"remove_{i}")
        entries.append(c.instance(entry_module, ports, f"entry{i:02d}"))

    preceding_busy = c.const(1)
    for i, entry in enumerate(entries):
        c.alias(f"allocate_{i}", c.land(control["wr"], preceding_busy,
                                         c.inv(entry["busy"])))
        preceding_busy = c.land(preceding_busy, entry["busy"])
    c.output("full", preceding_busy)
    c.output("count", _sum_bits(c, [entry["busy"] for entry in entries], 5))

    selected = _oldest(c, entries, ready_only)
    index = selected["index"]
    ex_valid = c.land(selected["valid"],
                      c.choose(index, [entry["ready"] for entry in entries]))
    remove = c.land(control["enable"], control["take"], ex_valid)
    for i in range(16):
        c.alias(f"remove_{i}", c.land(remove, c.is_(index, i)))
    c.output("ex_valid", ex_valid)
    for name in ("op", "rd", "imm", "pc", "sn", "a", "b"):
        c.output("ex_" + name, c.choose(index, [entry[name] for entry in entries]))
    return c


def _rob_entry():
    c = Circuit("ROB_Entry", "Retirement entry | sequence-matched completion")
    control = _inputs(c, {name: 1 for name in (
        "clk", "rst", "clear", "enable", "wr", "erase",
    )})
    incoming = _inputs(c, ROB_FIELDS)
    bus = _rob_cdb_inputs(c)
    reset = c.lor(control["rst"], control["clear"])
    valid = c.ref("Q_valid")
    write = c.land(control["enable"], control["wr"], c.inv(valid))
    erase = c.land(control["enable"], control["erase"], valid)
    c.reg("Q_valid", 1, write, c.lor(write, erase), reset)
    state = {name: c.reg("Q_" + name, width, incoming[name], write, reset)
             for name, width in ROB_FIELDS.items()}
    matches = [c.land(valid, bus[f"c{k}_valid"],
                       c.eq(state["sn"], bus[f"c{k}_sn"]))
               for k in range(2)]
    complete = c.land(control["enable"], c.lor(*matches))
    change = c.lor(write, complete)
    done = c.reg("Q_done", 1, c.inv(write), change, reset)
    c.output("valid", valid)
    c.output("done", done)
    for name in ROB_FIELDS:
        c.output(name, state[name])
    for name, width in (("result", 16), ("next", 16), ("taken", 1), ("target", 16)):
        value = c.mux(matches[0], bus[f"c1_{name}"], bus[f"c0_{name}"])
        state[name] = c.reg("Q_" + name, width,
                            c.mux(write, value, c.const(0, width)), change, reset)
        c.output(name, state[name])
    return c


def _rob(entry_module):
    c = Circuit("ROB", "16-entry distributed ROB | stable head, single retire")
    control = _inputs(c, {name: 1 for name in (
        "clk", "rst", "clear", "enable", "wr", "pop",
    )})
    incoming = _inputs(c, ROB_FIELDS)
    bus = _rob_cdb_inputs(c)
    reset = c.lor(control["rst"], control["clear"])
    head = c.ref("Q_head", 4)
    tail = c.ref("Q_tail", 4)
    count = c.ref("Q_count", 5)
    full = c.is_(count, 16)
    nonempty = c.nonzero(count)
    write = c.land(control["enable"], control["wr"], c.inv(full))
    pop = c.land(control["enable"], control["pop"], nonempty)
    c.reg("Q_head", 4, c.inc(head), pop, reset)
    c.reg("Q_tail", 4, c.inc(tail), write, reset)
    c.reg("Q_count", 5, c.mux(write, c.inc(count, -1), c.inc(count)),
          c.xor(write, pop), reset)
    entries = []
    for i in range(16):
        ports = {name: control[name] for name in (
            "clk", "rst", "clear", "enable",
        )}
        ports.update(incoming)
        ports.update(bus)
        ports["wr"] = c.land(write, c.is_(tail, i))
        ports["erase"] = c.land(pop, c.is_(head, i))
        entries.append(c.instance(entry_module, ports, f"entry{i:02d}"))
    c.output("full", full)
    c.output("count", count)
    c.output("head_valid", c.land(nonempty,
                                  c.choose(head, [e["valid"] for e in entries])))
    for name in ("done", *ROB_FIELDS, "result", "next", "taken", "target"):
        c.output("head_" + name, c.choose(head, [e[name] for e in entries]))
    return c


def build_backend():
    """Return helper cells, ROB, ALU_RS and LS_RS as Circuit objects."""
    rs_entry = _rs_entry()
    rob_entry = _rob_entry()
    return [rs_entry, rob_entry, _rob(rob_entry),
            _reservation_station(rs_entry, "ALU_RS", True),
            _reservation_station(rs_entry, "LS_RS", False)]
