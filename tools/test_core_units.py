"""Generate native module fixtures exercised by tools.CoreUnitTests."""
from pathlib import Path

from circuit import write_project
from core import (
    build_alu,
    build_memory,
    build_physical_cell,
    build_rename,
    build_store_buffer,
)


if __name__ == "__main__":
    path = Path(__file__).with_name("core-unit-tests.circ")
    cell = build_physical_cell()
    modules = [build_alu(), cell, build_rename(cell), build_store_buffer(), build_memory()]
    write_project(path, modules, main="ALU")
    print(path)
