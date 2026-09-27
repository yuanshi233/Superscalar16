"""Generate a ready-to-run Logisim project from assembly or raw memory images."""
import argparse
from pathlib import Path
from assembler import AssemblyError, assemble_file
from circuit import write_project
from frontend import build_frontend
from backend import build_backend
from core import (build_alu, build_physical_cell, build_rename, build_store_buffer,
                  build_memory, build_cpu, build_dashboard)


DEMO = [0x7205,0x7443,0x1650,0x90c0,0x8800,0xf000]


def build(program=DEMO, data=None):
    front=build_frontend(program)
    back=build_backend()
    alu=build_alu()
    cell=build_physical_cell()
    rename=build_rename(cell)
    sb=build_store_buffer()
    mem=build_memory(data)
    cpu=build_cpu(front[-1],front[0],back[-2],back[-1],back[2],alu,rename,sb,mem)
    dashboard=build_dashboard(cpu)
    return [dashboard,cpu,*front,*back,alu,cell,rename,sb,mem]


def read_raw(path):
    """Read a 16-bit Logisim v2.0 raw image (or plain hex word stream)."""
    tokens=path.read_text(encoding='utf-8').split()
    if tokens[:2]==['v2.0','raw']:
        tokens=tokens[2:]
    try:
        words=[int(token,16) for token in tokens]
    except ValueError as error:
        raise ValueError(f'{path}: invalid raw hex word: {error}') from error
    if len(words)>65536 or any(not 0<=word<=65535 for word in words):
        raise ValueError(f'{path}: image must contain at most 65536 unsigned 16-bit words')
    return words


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    source=p.add_mutually_exclusive_group()
    source.add_argument('--source',type=Path,help='assembly source (.text and .data)')
    source.add_argument('--program',type=Path,help='instruction ROM v2.0 raw image')
    p.add_argument('--data',type=Path,help='data RAM v2.0 raw image (with --program)')
    p.add_argument('--output',type=Path,default=Path(__file__).resolve().parent.parent/'Superscalar16_impl.circ')
    args=p.parse_args(argv)
    if args.source and args.data:
        p.error('--data cannot be combined with --source; use a .data section')
    if args.data and not args.program:
        p.error('--data requires --program')
    inputs=[path for path in (args.source,args.program,args.data) if path is not None]
    if any(path.resolve()==args.output.resolve() for path in inputs):
        p.error('output would overwrite an input source or image')
    try:
        if args.source:
            assembled=assemble_file(args.source)
            words=assembled.text
            data=assembled.data or None
        else:
            words=read_raw(args.program) if args.program else DEMO
            data=read_raw(args.data) if args.data else None
        circuits=build(words,data)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_project(args.output,circuits)
    except (AssemblyError,OSError,ValueError) as error:
        p.error(str(error))
    print(f'{args.output}: {len(circuits)} native subcircuits; '
          f'{len(words)} program word(s); {len(data) if data else 0} initial data word(s)')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
