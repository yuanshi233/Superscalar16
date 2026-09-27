"""Assembler and independent sequential oracle for the documented 16-bit ISA.

Addresses count 16-bit words. Branches and JAL are relative to PC + 1;
JALR uses the old source value plus its signed six-bit immediate.
"""

from dataclasses import dataclass, field
import re


OPCODES = {
    "NOP": 0, "ADD": 1, "SUB": 2, "AND": 3, "OR": 4, "XOR": 5,
    "SLT": 6, "ADDI": 7, "LW": 8, "SW": 9, "BEQ": 10, "BNE": 11,
    "JAL": 12, "JALR": 13, "SLTI": 14, "HALT": 15,
}
MNEMONICS = {value: name for name, value in OPCODES.items()}
R_OPS = {"ADD", "SUB", "AND", "OR", "XOR", "SLT"}
I_OPS = {"ADDI", "LW", "SLTI", "JALR"}


def unsigned16(value):
    return int(value) & 0xFFFF


def signed(value, width=16):
    value &= (1 << width) - 1
    return value - (1 << width) if value & (1 << (width - 1)) else value


def _number(value):
    if isinstance(value, int):
        return value
    text = value.strip()
    try:
        return int(text, 0)
    except ValueError:
        return int(text, 10)


def _register(value):
    number = _number(value[1:] if isinstance(value, str) and value.lower().startswith("r") else value)
    if not 0 <= number < 8:
        raise ValueError(f"register out of range: {value}")
    return number


def _immediate(value, width):
    number = _number(value)
    if not -(1 << (width - 1)) <= number < (1 << (width - 1)):
        raise ValueError(f"signed {width}-bit immediate out of range: {number}")
    return number & ((1 << width) - 1)


def encode(mnemonic, *operands):
    """Encode operands in ISA order; LW/SW use (data, base, offset)."""
    mnemonic = mnemonic.upper()
    if mnemonic not in OPCODES:
        raise ValueError(f"unknown instruction: {mnemonic}")
    word = OPCODES[mnemonic] << 12
    arity = 0 if mnemonic in ("NOP", "HALT") else 2 if mnemonic == "JAL" else 3
    if len(operands) != arity:
        raise ValueError(f"{mnemonic} expects {arity} operands, got {len(operands)}")
    if mnemonic in ("NOP", "HALT"):
        return word
    if mnemonic in R_OPS:
        rd, rs1, rs2 = map(_register, operands)
        return word | (rd << 9) | (rs1 << 6) | (rs2 << 3)
    if mnemonic in I_OPS:
        rd, rs1 = map(_register, operands[:2])
        return word | (rd << 9) | (rs1 << 6) | _immediate(operands[2], 6)
    if mnemonic == "SW":
        data, base = map(_register, operands[:2])
        return word | (base << 9) | (data << 6) | _immediate(operands[2], 6)
    if mnemonic in ("BEQ", "BNE"):
        rs1, rs2 = map(_register, operands[:2])
        return word | (rs1 << 9) | (rs2 << 6) | _immediate(operands[2], 6)
    return word | (_register(operands[0]) << 9) | _immediate(operands[1], 9)


def _source_lines(source):
    return source.splitlines() if isinstance(source, str) else list(source)


def assemble(source):
    """Assemble text/lines with labels, .word, .org and base+offset memory syntax."""
    labels = {}
    statements = []
    pc = 0
    for line_number, original in enumerate(_source_lines(source), 1):
        line = re.split(r"[;#]", original, maxsplit=1)[0].strip()
        while ":" in line:
            label, line = line.split(":", 1)
            label, line = label.strip(), line.strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", label):
                raise ValueError(f"line {line_number}: invalid label {label!r}")
            if label in labels:
                raise ValueError(f"line {line_number}: duplicate label {label}")
            labels[label] = pc
        if not line:
            continue
        parts = line.split(None, 1)
        operation = parts[0].upper()
        operand_text = parts[1] if len(parts) > 1 else ""
        if operation == ".ORG":
            new_pc = _number(operand_text)
            if not pc <= new_pc < 65536:
                raise ValueError(f"line {line_number}: .org must move forward within 64K")
            pc = new_pc
            continue
        if pc >= 65536:
            raise ValueError("program exceeds 64K words")
        statements.append((pc, operation, operand_text, line_number))
        pc += 1
    program = [0] * pc
    for pc, operation, operand_text, line_number in statements:
        try:
            if operation == ".WORD":
                value = _number(operand_text)
                if not -32768 <= value <= 65535:
                    raise ValueError(".word exceeds 16 bits")
                program[pc] = unsigned16(value)
                continue
            operands = [operand.strip() for operand in operand_text.split(",")] if operand_text else []
            if operation in ("LW", "SW") and len(operands) == 2:
                memory = re.fullmatch(r"\s*([^()]*)\(\s*(r[0-7])\s*\)\s*", operands[1], re.IGNORECASE)
                if memory is None:
                    raise ValueError("expected offset(base) memory operand")
                operands = [operands[0], memory[2], memory[1].strip() or "0"]
            if operation in ("BEQ", "BNE", "JAL") and operands:
                target = operands[-1]
                if target in labels:
                    displacement = labels[target] - (pc + 1)
                    operands[-1] = str(displacement)
            program[pc] = encode(operation, *operands)
        except (ValueError, KeyError) as error:
            raise ValueError(f"line {line_number}: {error}") from error
    return program


def disassemble(word):
    word = unsigned16(word)
    operation = MNEMONICS[word >> 12]
    rd, rs1, rs2 = (word >> 9) & 7, (word >> 6) & 7, (word >> 3) & 7
    if operation in ("NOP", "HALT"):
        return operation
    if operation in R_OPS:
        return f"{operation} r{rd},r{rs1},r{rs2}"
    if operation in ("ADDI", "SLTI", "JALR"):
        return f"{operation} r{rd},r{rs1},{signed(word, 6)}"
    if operation == "LW":
        return f"LW r{rd},{signed(word, 6)}(r{rs1})"
    if operation == "SW":
        return f"SW r{rs1},{signed(word, 6)}(r{rd})"
    if operation in ("BEQ", "BNE"):
        return f"{operation} r{rd},r{rs1},{signed(word, 6)}"
    return f"JAL r{rd},{signed(word, 9)}"


@dataclass
class State:
    registers: list[int] = field(default_factory=lambda: [0] * 8)
    memory: dict[int, int] = field(default_factory=dict)
    pc: int = 0
    retired: int = 0
    halted: bool = False


class ReferenceCPU:
    """Architectural oracle; deliberately independent of pipeline/rename code."""

    def __init__(self, program, initial_memory=None):
        self.program = list(program)
        if len(self.program) > 65536 or any(not 0 <= word <= 65535 for word in self.program):
            raise ValueError("program must contain unsigned 16-bit words")
        self.initial_memory = {
            unsigned16(address): unsigned16(value)
            for address, value in (initial_memory or {}).items()
        }
        self.reset()

    def reset(self):
        self.state = State(memory=dict(self.initial_memory))

    def step(self, run=True, rst=False):
        if rst:
            self.reset()
            return False
        s = self.state
        if not run or s.halted:
            return False
        word = self.program[s.pc] if s.pc < len(self.program) else 0
        op = word >> 12
        rd, rs1, rs2 = (word >> 9) & 7, (word >> 6) & 7, (word >> 3) & 7
        immediate = signed(word, 6)
        next_pc = unsigned16(s.pc + 1)
        a, b = s.registers[rs1], s.registers[rs2]
        result = None
        if op == 1:
            result = a + b
        elif op == 2:
            result = a - b
        elif op == 3:
            result = a & b
        elif op == 4:
            result = a | b
        elif op == 5:
            result = a ^ b
        elif op == 6:
            result = int(signed(a) < signed(b))
        elif op == 7:
            result = a + immediate
        elif op == 8:
            result = s.memory.get(unsigned16(a + immediate), 0)
        elif op == 9:
            s.memory[unsigned16(s.registers[rd] + immediate)] = s.registers[rs1]
        elif op in (10, 11):
            equal = s.registers[rd] == s.registers[rs1]
            if equal == (op == 10):
                next_pc = unsigned16(next_pc + immediate)
        elif op == 12:
            result = next_pc
            next_pc = unsigned16(next_pc + signed(word, 9))
        elif op == 13:
            result = next_pc
            next_pc = unsigned16(a + immediate)
        elif op == 14:
            result = int(signed(a) < immediate)
        elif op == 15:
            s.halted = True
        if result is not None and rd != 0:
            s.registers[rd] = unsigned16(result)
        s.registers[0] = 0
        s.pc = next_pc
        s.retired += 1
        return True

    def run(self, max_steps=100000):
        for _ in range(max_steps):
            if self.state.halted:
                return self.state
            self.step()
        if self.state.halted:
            return self.state
        raise RuntimeError(f"program did not halt within {max_steps} instructions; pc={self.state.pc:#06x}")


def execute(program, max_steps=100000, initial_memory=None):
    return ReferenceCPU(program, initial_memory).run(max_steps)


def expected_state(program, memory_addresses=(), max_steps=100000):
    state = execute(program, max_steps=max_steps)
    addresses = set(map(unsigned16, memory_addresses)) | set(state.memory)
    return {
        "registers": state.registers,
        "memory": {str(address): state.memory.get(address, 0) for address in sorted(addresses)},
        "retired": state.retired,
        "halted": state.halted,
        "architectural_next_pc": state.pc,
    }
