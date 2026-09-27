"""Generate the native circuit fixture exercised by tools.FrontendTests."""
from pathlib import Path

from circuit import write_project
from frontend import build_frontend


if __name__ == "__main__":
    path = Path(__file__).with_name("frontend-tests.circ")
    program = [0x7205, 0x7443, 0x1650, 0x90C0, 0x8800, 0xF000]
    write_project(path, build_frontend(program), main="FetchUnit")
    print(path)
