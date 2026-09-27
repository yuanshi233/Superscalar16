"""Command-line assembler for the Superscalar16 Logisim CPU.

The CPU is Harvard-style: ``.text`` becomes an instruction-ROM image and
``.data`` becomes a separate data-RAM image.  Both images use Logisim's
``v2.0 raw`` format and contain 16-bit words addressed from zero.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass, field
from pathlib import Path
import re
import sys
from typing import Callable, Iterable, Sequence

try:  # Works both as ``python tools/assembler.py`` and ``import tools.assembler``.
    from .isa import I_OPS, OPCODES, R_OPS, encode
except ImportError:  # pragma: no cover - script mode
    from isa import I_OPS, OPCODES, R_OPS, encode


SECTIONS = {"text", "data"}
CONTROL_FLOW = {"BEQ", "BNE", "JAL"}
DIRECTIVES = {
    ".TEXT", ".DATA", ".SECTION", ".ORG", ".WORD", ".FILL", ".ZERO",
    ".SPACE", ".ALIGN", ".ASCII", ".ASCIZ", ".EQU", ".INCLUDE",
}
PSEUDO_ARITY = {
    "B": 1, "BEQZ": 2, "BNEZ": 2, "CALL": 1, "J": 1, "JR": 1,
    "LI": 2, "MOV": 2, "RET": 0,
}
LABEL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
REGISTER_RE = re.compile(r"(?:r([0-7])|zero)\Z", re.IGNORECASE)


@dataclass(frozen=True)
class SourceLine:
    path: str
    number: int
    text: str


class AssemblyError(ValueError):
    """A source diagnostic with a stable, compiler-style representation."""

    def __init__(self, message: str, source: SourceLine | None = None, column: int | None = None):
        super().__init__(message)
        self.message = message
        self.source = source
        self.column = column

    def __str__(self) -> str:
        if self.source is None:
            return self.message
        location = f"{self.source.path}:{self.source.number}"
        if self.column is not None:
            location += f":{self.column}"
        return f"{location}: error: {self.message}\n    {self.source.text.rstrip()}"


@dataclass(frozen=True)
class Symbol:
    name: str
    value: int
    section: str | None
    source: SourceLine


@dataclass
class Statement:
    section: str
    address: int
    operation: str
    operands: list[str]
    source: SourceLine
    size: int = 1


@dataclass(frozen=True)
class ListingEntry:
    section: str
    address: int
    words: tuple[int, ...]
    source: SourceLine


@dataclass
class AssemblyResult:
    text: list[int]
    data: list[int]
    symbols: dict[str, Symbol]
    listing: list[ListingEntry] = field(default_factory=list)


def _strip_comment(text: str) -> str:
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
        elif char in ";#":
            return text[:index]
        elif char == "/" and index + 1 < len(text) and text[index + 1] == "/":
            return text[:index]
        index += 1
    return text


def _split_operands(text: str, source: SourceLine) -> list[str]:
    if not text.strip():
        return []
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise AssemblyError("unmatched ')'", source)
        elif char == "," and depth == 0:
            part = text[start:index].strip()
            if not part:
                raise AssemblyError("empty operand", source)
            parts.append(part)
            start = index + 1
    if quote:
        raise AssemblyError("unterminated string literal", source)
    if depth:
        raise AssemblyError("unmatched '('", source)
    final = text[start:].strip()
    if not final:
        raise AssemblyError("empty operand", source)
    parts.append(final)
    return parts


class _ExpressionEvaluator(ast.NodeVisitor):
    _binary: dict[type[ast.operator], Callable[[int, int], int]] = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a // b,
        ast.FloorDiv: lambda a, b: a // b,
        ast.Mod: lambda a, b: a % b,
        ast.LShift: lambda a, b: a << b,
        ast.RShift: lambda a, b: a >> b,
        ast.BitAnd: lambda a, b: a & b,
        ast.BitOr: lambda a, b: a | b,
        ast.BitXor: lambda a, b: a ^ b,
    }
    _unary: dict[type[ast.unaryop], Callable[[int], int]] = {
        ast.UAdd: lambda a: a,
        ast.USub: lambda a: -a,
        ast.Invert: lambda a: ~a,
    }

    def __init__(self, resolve: Callable[[str], int], pc: int):
        self.resolve = resolve
        self.pc = pc
        self.names: set[str] = set()

    def visit_Expression(self, node: ast.Expression) -> int:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> int:
        if isinstance(node.value, bool) or not isinstance(node.value, int):
            raise ValueError("only integer constants are allowed")
        return node.value

    def visit_Name(self, node: ast.Name) -> int:
        name = node.id.upper()
        if name == "__CURRENT_ADDRESS__":
            return self.pc
        self.names.add(name)
        return self.resolve(name)

    def visit_BinOp(self, node: ast.BinOp) -> int:
        operation = self._binary.get(type(node.op))
        if operation is None:
            raise ValueError("unsupported expression operator")
        return operation(self.visit(node.left), self.visit(node.right))

    def visit_UnaryOp(self, node: ast.UnaryOp) -> int:
        operation = self._unary.get(type(node.op))
        if operation is None:
            raise ValueError("unsupported unary operator")
        return operation(self.visit(node.operand))

    def generic_visit(self, node: ast.AST) -> int:
        raise ValueError(f"unsupported expression element: {type(node).__name__}")


class Assembler:
    """Two-pass assembler with independent text and data address spaces."""

    def __init__(self, lines: Sequence[SourceLine]):
        self.lines = list(lines)
        self.labels: dict[str, Symbol] = {}
        self.constant_defs: dict[str, tuple[str, SourceLine, int]] = {}
        self.constants: dict[str, int] = {}
        self.statements: list[Statement] = []
        self.pc = {"text": 0, "data": 0}
        self.high_water = {"text": 0, "data": 0}
        self.current_section = "text"

    def _define_label(self, name: str, source: SourceLine) -> None:
        key = name.upper()
        if key == "__CURRENT_ADDRESS__":
            raise AssemblyError("reserved symbol name", source)
        if key in self.labels or key in self.constant_defs:
            raise AssemblyError(f"duplicate symbol '{name}'", source)
        self.labels[key] = Symbol(name, self.pc[self.current_section], self.current_section, source)

    def _resolve_constant(self, key: str, stack: tuple[str, ...] = ()) -> int:
        key = key.upper()
        if key in self.constants:
            return self.constants[key]
        if key in self.labels:
            return self.labels[key].value
        if key not in self.constant_defs:
            raise KeyError(key)
        if key in stack:
            chain = " -> ".join((*stack, key))
            raise ValueError(f"cyclic constant definition: {chain}")
        expression, source, pc = self.constant_defs[key]
        value, _ = self._expression(expression, source, pc, (*stack, key))
        self.constants[key] = value
        return value

    def _expression(
        self, text: str, source: SourceLine, pc: int, stack: tuple[str, ...] = ()
    ) -> tuple[int, set[str]]:
        expression = re.sub(r"(?<![A-Za-z0-9_])\$(?![A-Za-z0-9_])", "__CURRENT_ADDRESS__", text.strip())
        if not expression:
            raise AssemblyError("expected expression", source)
        try:
            tree = ast.parse(expression, mode="eval")
            evaluator = _ExpressionEvaluator(lambda name: self._resolve_constant(name, stack), pc)
            value = evaluator.visit(tree)
            return value, evaluator.names
        except AssemblyError:
            raise
        except KeyError as error:
            raise AssemblyError(f"unknown symbol '{error.args[0]}'", source) from None
        except (SyntaxError, TypeError, ValueError, ZeroDivisionError, OverflowError) as error:
            raise AssemblyError(f"invalid expression '{text}': {error}", source) from None

    def _layout_value(self, text: str, source: SourceLine) -> int:
        return self._expression(text, source, self.pc[self.current_section])[0]

    @staticmethod
    def _string_words(text: str, source: SourceLine, terminated: bool) -> list[int]:
        try:
            value = ast.literal_eval(text)
        except (SyntaxError, ValueError) as error:
            raise AssemblyError(f"invalid string literal: {error}", source) from None
        if not isinstance(value, str):
            raise AssemblyError("expected a quoted string", source)
        words = [ord(character) for character in value]
        if any(word > 0xFFFF for word in words):
            raise AssemblyError("string contains a character outside 16-bit range", source)
        if terminated:
            words.append(0)
        return words

    def _reserve(self, operation: str, operands: list[str], source: SourceLine) -> None:
        section = self.current_section
        address = self.pc[section]
        size = 1
        if operation == ".WORD":
            if not operands:
                raise AssemblyError(".word expects at least one value", source)
            size = len(operands)
        elif operation in (".ZERO", ".SPACE"):
            if len(operands) != 1:
                raise AssemblyError(f"{operation.lower()} expects one count", source)
            size = self._layout_value(operands[0], source)
        elif operation == ".FILL":
            if not 1 <= len(operands) <= 2:
                raise AssemblyError(".fill expects count[, value]", source)
            size = self._layout_value(operands[0], source)
        elif operation == ".ALIGN":
            if not 1 <= len(operands) <= 2:
                raise AssemblyError(".align expects boundary[, fill]", source)
            boundary = self._layout_value(operands[0], source)
            if boundary <= 0 or boundary & (boundary - 1):
                raise AssemblyError(".align boundary must be a positive power of two", source)
            size = (-address) % boundary
        elif operation in (".ASCII", ".ASCIZ"):
            if len(operands) != 1:
                raise AssemblyError(f"{operation.lower()} expects one string", source)
            size = len(self._string_words(operands[0], source, operation == ".ASCIZ"))
        elif operation.startswith("."):
            raise AssemblyError(f"unknown directive '{operation.lower()}'", source)
        elif operation not in OPCODES and operation not in PSEUDO_ARITY:
            raise AssemblyError(f"unknown instruction '{operation}'", source)
        elif operation == "LI":
            if len(operands) != 2:
                raise AssemblyError(f"LI expects 2 operands, got {len(operands)}", source)
            self._register(operands[0], source)
            # Short constants stay one word. Unknown forward symbols reserve a
            # fixed-size expansion, so subsequent label addresses never move.
            try:
                value = self._layout_value(operands[1], source)
            except AssemblyError as error:
                if not error.message.startswith("unknown symbol '"):
                    raise
                size = 13
            else:
                if not -0x8000 <= value <= 0xFFFF:
                    raise AssemblyError(f"LI constant {value} does not fit in 16 bits", source)
                size = 1 if -32 <= value <= 31 else 13
        if size < 0:
            raise AssemblyError("word count cannot be negative", source)
        if address + size > 0x10000:
            raise AssemblyError(f"{section} section exceeds 64K words", source)
        if size:
            self.statements.append(Statement(section, address, operation, operands, source, size))
        self.pc[section] += size
        self.high_water[section] = max(self.high_water[section], self.pc[section])

    def first_pass(self) -> None:
        for source in self.lines:
            remaining = _strip_comment(source.text).strip()
            while remaining:
                match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*", remaining)
                if not match:
                    break
                self._define_label(match.group(1), source)
                remaining = remaining[match.end():].strip()
            if not remaining:
                continue

            assignment = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)", remaining)
            if assignment:
                self._define_constant(assignment.group(1), assignment.group(2), source)
                continue

            parts = remaining.split(None, 1)
            operation = parts[0].upper()
            operand_text = parts[1] if len(parts) == 2 else ""
            operands = _split_operands(operand_text, source)
            if operation == ".EQU":
                if len(operands) != 2 or not LABEL_RE.fullmatch(operands[0]):
                    raise AssemblyError(".equ syntax is .equ NAME, expression", source)
                self._define_constant(operands[0], operands[1], source)
            elif operation in (".TEXT", ".DATA"):
                if operands:
                    raise AssemblyError(f"{operation.lower()} takes no operands", source)
                self.current_section = operation[1:].lower()
            elif operation == ".SECTION":
                if len(operands) != 1:
                    raise AssemblyError(".section expects text or data", source)
                name = operands[0].lower().lstrip(".")
                if name not in SECTIONS:
                    raise AssemblyError(".section expects text or data", source)
                self.current_section = name
            elif operation == ".ORG":
                if len(operands) != 1:
                    raise AssemblyError(".org expects one address", source)
                address = self._layout_value(operands[0], source)
                if not 0 <= address <= 0xFFFF:
                    raise AssemblyError(".org address must be in 0..65535", source)
                self.pc[self.current_section] = address
                self.high_water[self.current_section] = max(
                    self.high_water[self.current_section], address
                )
            elif operation == ".INCLUDE":
                raise AssemblyError(".include is only available when assembling a file", source)
            else:
                self._reserve(operation, operands, source)

    def _define_constant(self, name: str, expression: str, source: SourceLine) -> None:
        key = name.upper()
        if key == "__CURRENT_ADDRESS__":
            raise AssemblyError("reserved symbol name", source)
        if key in self.labels or key in self.constant_defs:
            raise AssemblyError(f"duplicate symbol '{name}'", source)
        self.constant_defs[key] = (expression, source, self.pc[self.current_section])

    @staticmethod
    def _register(text: str, source: SourceLine) -> str:
        match = REGISTER_RE.fullmatch(text.strip())
        if not match:
            raise AssemblyError(f"invalid register '{text}'; expected r0..r7", source)
        return f"r{match.group(1) or 0}"

    def _value(self, text: str, statement: Statement) -> tuple[int, set[str]]:
        return self._expression(text, statement.source, statement.address)

    def _relative(self, text: str, statement: Statement) -> int:
        value, names = self._value(text, statement)
        label_refs = [self.labels[name] for name in names if name in self.labels]
        if label_refs:
            wrong = next((symbol for symbol in label_refs if symbol.section != "text"), None)
            if wrong:
                raise AssemblyError(
                    f"control-flow target '{wrong.name}' is in .data, not .text",
                    statement.source,
                )
            return value - (statement.address + 1)
        return value

    def _instruction(self, statement: Statement) -> list[int]:
        operation = statement.operation
        operands = list(statement.operands)
        source = statement.source

        expected = PSEUDO_ARITY.get(operation)
        if expected is not None and len(operands) != expected:
            raise AssemblyError(
                f"{operation} expects {expected} operand{'s' if expected != 1 else ''}, got {len(operands)}",
                source,
            )
        if operation == "B":
            operation, operands = "BEQ", ["r0", "r0", operands[0]]
        elif operation == "BEQZ":
            operation, operands = "BEQ", [operands[0], "r0", operands[1]]
        elif operation == "BNEZ":
            operation, operands = "BNE", [operands[0], "r0", operands[1]]
        elif operation == "CALL":
            operation, operands = "JAL", ["r7", operands[0]]
        elif operation == "J":
            operation, operands = "JAL", ["r0", operands[0]]
        elif operation == "JR":
            operation, operands = "JALR", ["r0", operands[0], "0"]
        elif operation == "RET":
            operation, operands = "JALR", ["r0", "r7", "0"]
        elif operation == "LI":
            rd = self._register(operands[0], source)
            value = self._value(operands[1], statement)[0]
            if not -0x8000 <= value <= 0xFFFF:
                raise AssemblyError(f"LI constant {value} does not fit in 16 bits", source)
            if statement.size == 1:
                if not -32 <= value <= 31:
                    raise AssemblyError("LI changed size after layout; use a defined constant", source)
                return [encode("ADDI", rd, "r0", value)]
            encoded = value & 0xFFFF
            high = (encoded >> 10) - 64 if encoded & 0x8000 else encoded >> 10
            middle = (encoded >> 5) & 31
            low = encoded & 31
            # r = (high << 10) + (middle << 5) + low, modulo 16 bits.
            # Doubling uses the existing ADD instruction; no new ISA opcode.
            words = [encode("ADDI", rd, "r0", high)]
            words.extend(encode("ADD", rd, rd, rd) for _ in range(5))
            words.append(encode("ADDI", rd, rd, middle))
            words.extend(encode("ADD", rd, rd, rd) for _ in range(5))
            words.append(encode("ADDI", rd, rd, low))
            return words
        elif operation == "MOV":
            operation, operands = "ADDI", [operands[0], operands[1], "0"]

        if operation in ("LW", "SW") and len(operands) == 2:
            memory = re.fullmatch(r"(.*)\(\s*([^()]+)\s*\)", operands[1])
            if memory is None:
                raise AssemblyError("expected offset(base), e.g. 4(r2)", source)
            operands = [operands[0], memory.group(2), memory.group(1).strip() or "0"]
        if operation == "JALR" and len(operands) == 2:
            memory = re.fullmatch(r"(.*)\(\s*([^()]+)\s*\)", operands[1])
            if memory is not None:
                operands = [operands[0], memory.group(2), memory.group(1).strip() or "0"]

        arity = 0 if operation in ("NOP", "HALT") else 2 if operation == "JAL" else 3
        if len(operands) != arity:
            raise AssemblyError(
                f"{operation} expects {arity} operand{'s' if arity != 1 else ''}, got {len(operands)}",
                source,
            )

        try:
            if operation in R_OPS:
                encoded = [self._register(value, source) for value in operands]
            elif operation in I_OPS:
                encoded = [self._register(value, source) for value in operands[:2]]
                encoded.append(self._value(operands[2], statement)[0])
            elif operation == "SW":
                encoded = [self._register(value, source) for value in operands[:2]]
                encoded.append(self._value(operands[2], statement)[0])
            elif operation in ("BEQ", "BNE"):
                encoded = [self._register(value, source) for value in operands[:2]]
                encoded.append(self._relative(operands[2], statement))
            elif operation == "JAL":
                encoded = [self._register(operands[0], source), self._relative(operands[1], statement)]
            else:
                encoded = []
            return [encode(operation, *encoded)]
        except ValueError as error:
            raise AssemblyError(str(error), source) from None

    def _directive_words(self, statement: Statement) -> list[int]:
        operation, operands = statement.operation, statement.operands
        if operation == ".WORD":
            values = [self._value(value, statement)[0] for value in operands]
        elif operation in (".ZERO", ".SPACE"):
            values = [0] * statement.size
        elif operation == ".FILL":
            fill = self._value(operands[1], statement)[0] if len(operands) == 2 else 0
            values = [fill] * statement.size
        elif operation == ".ALIGN":
            fill = self._value(operands[1], statement)[0] if len(operands) == 2 else 0
            values = [fill] * statement.size
        elif operation in (".ASCII", ".ASCIZ"):
            values = self._string_words(operands[0], statement.source, operation == ".ASCIZ")
        else:
            raise AssertionError(operation)
        if len(values) != statement.size:
            raise AssemblyError("directive size changed between passes", statement.source)
        for value in values:
            if not -0x8000 <= value <= 0xFFFF:
                raise AssemblyError(f"word value {value} does not fit in 16 bits", statement.source)
        return [value & 0xFFFF for value in values]

    def second_pass(self) -> AssemblyResult:
        for key in self.constant_defs:
            self._resolve_constant(key)
        images = {
            section: [0] * self.high_water[section]
            for section in SECTIONS
        }
        occupied = {section: [False] * self.high_water[section] for section in SECTIONS}
        listing: list[ListingEntry] = []
        for statement in self.statements:
            if statement.operation.startswith("."):
                words = self._directive_words(statement)
            else:
                if statement.section != "text":
                    raise AssemblyError("instructions are only allowed in .text", statement.source)
                words = self._instruction(statement)
            for offset, word in enumerate(words):
                address = statement.address + offset
                if occupied[statement.section][address]:
                    raise AssemblyError(
                        f"address {address:#06x} in .{statement.section} is written more than once",
                        statement.source,
                    )
                occupied[statement.section][address] = True
                images[statement.section][address] = word
            listing.append(ListingEntry(statement.section, statement.address, tuple(words), statement.source))
        symbols = dict(self.labels)
        for key, value in self.constants.items():
            expression, source, _ = self.constant_defs[key]
            del expression
            symbols[key] = Symbol(key, value, None, source)
        return AssemblyResult(images["text"], images["data"], symbols, listing)

    def assemble(self) -> AssemblyResult:
        self.first_pass()
        return self.second_pass()


def _source_lines(source: str, filename: str) -> list[SourceLine]:
    return [SourceLine(filename, number, text) for number, text in enumerate(source.splitlines(), 1)]


def assemble_source(source: str, filename: str = "<string>") -> AssemblyResult:
    """Assemble a string and return text/data images, symbols, and a listing."""
    return Assembler(_source_lines(source, filename)).assemble()


def _read_with_includes(path: Path, stack: tuple[Path, ...] = ()) -> list[SourceLine]:
    path = path.resolve()
    if path in stack:
        chain = " -> ".join(str(item) for item in (*stack, path))
        raise AssemblyError(f"recursive .include: {chain}")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise AssemblyError(f"cannot read {path}: {error}") from None
    result: list[SourceLine] = []
    for number, original in enumerate(text.splitlines(), 1):
        source = SourceLine(str(path), number, original)
        line = _strip_comment(original).strip()
        match = re.fullmatch(r"\.include\s+(.+)", line, re.IGNORECASE)
        if match:
            try:
                included = ast.literal_eval(match.group(1))
            except (SyntaxError, ValueError):
                raise AssemblyError('.include expects a quoted filename', source) from None
            if not isinstance(included, str):
                raise AssemblyError('.include expects a quoted filename', source)
            result.extend(_read_with_includes(path.parent / included, (*stack, path)))
        else:
            result.append(source)
    return result


def assemble_file(path: str | Path) -> AssemblyResult:
    """Assemble a UTF-8 source file, recursively expanding relative includes."""
    return Assembler(_read_with_includes(Path(path))).assemble()


def format_raw(words: Iterable[int], words_per_line: int = 8) -> str:
    """Return a Logisim-compatible 16-bit ``v2.0 raw`` image."""
    values = list(words)
    if any(not 0 <= value <= 0xFFFF for value in values):
        raise ValueError("raw image contains a value outside 16 bits")
    lines = ["v2.0 raw"]
    for offset in range(0, len(values), words_per_line):
        lines.append(" ".join(f"{value:04x}" for value in values[offset:offset + words_per_line]))
    return "\n".join(lines) + "\n"


def format_listing(result: AssemblyResult) -> str:
    lines = ["SECTION  ADDR  WORDS                    SOURCE"]
    for entry in result.listing:
        words = " ".join(f"{word:04x}" for word in entry.words[:4])
        lines.append(
            f"{entry.section:7}  {entry.address:04x}  {words:24} "
            f"{entry.source.path}:{entry.source.number}  {entry.source.text.strip()}"
        )
        for offset in range(4, len(entry.words), 4):
            continuation = " ".join(f"{word:04x}" for word in entry.words[offset:offset + 4])
            lines.append(f"{'':7}  {entry.address + offset:04x}  {continuation}")
    return "\n".join(lines) + "\n"


def format_symbols(result: AssemblyResult) -> str:
    rows = []
    for symbol in sorted(result.symbols.values(), key=lambda item: ((item.section or ""), item.value, item.name.upper())):
        section = f".{symbol.section}" if symbol.section else "absolute"
        rows.append(f"{symbol.value & 0xFFFF:04x}  {section:8}  {symbol.name}")
    return "\n".join(rows) + ("\n" if rows else "")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble Superscalar16 source into Logisim v2.0 raw images.",
        epilog="Branches and JAL use labels relative to PC+1; numeric operands are raw displacements.",
    )
    parser.add_argument("source", type=Path, help="UTF-8 assembly source")
    parser.add_argument("-o", "--output", type=Path, help="instruction ROM image (default: SOURCE.hex)")
    parser.add_argument("-d", "--data-output", type=Path, help="data RAM image (default: SOURCE.data.hex when used)")
    parser.add_argument("-l", "--listing", type=Path, help="write an address/machine-code listing")
    parser.add_argument("--symbols", type=Path, help="write a symbol map")
    parser.add_argument("--quiet", action="store_true", help="suppress the success summary")
    args = parser.parse_args(argv)

    output = args.output or args.source.with_suffix(".hex")
    try:
        result = assemble_file(args.source)
        data_output = args.data_output
        if result.data and data_output is None:
            data_output = args.source.with_name(args.source.stem + ".data.hex")
        destinations = [output]
        if data_output is not None:
            destinations.append(data_output)
        if args.listing:
            destinations.append(args.listing)
        if args.symbols:
            destinations.append(args.symbols)
        resolved = [path.resolve() for path in destinations]
        if args.source.resolve() in resolved:
            raise AssemblyError("output path would overwrite the assembly source")
        if len(resolved) != len(set(resolved)):
            raise AssemblyError("output paths must be distinct")
        _write(output, format_raw(result.text))
        if data_output is not None:
            _write(data_output, format_raw(result.data))
        if args.listing:
            _write(args.listing, format_listing(result))
        if args.symbols:
            _write(args.symbols, format_symbols(result))
    except AssemblyError as error:
        print(error, file=sys.stderr)
        return 2
    except OSError as error:
        print(f"assembler: error: {error}", file=sys.stderr)
        return 2

    if not args.quiet:
        summary = f"assembled {len(result.text)} text word(s) -> {output}"
        if data_output is not None:
            summary += f"; {len(result.data)} data word(s) -> {data_output}"
        print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
