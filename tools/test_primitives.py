"""Generate native primitive regression circuits for the Java Logisim harness."""
from pathlib import Path
from circuit import Circuit, write_project


def main():
    combinational = Circuit('PrimitiveCombinational')
    a = combinational.input('a', 16)
    b = combinational.input('b', 16)
    sel = combinational.input('sel')
    combinational.output('add', combinational.add(a, b))
    combinational.output('sub', combinational.sub(a, b))
    combinational.output('and', combinational.land(a, b))
    combinational.output('or', combinational.lor(a, b))
    combinational.output('xor', combinational.xor(a, b))
    combinational.output('not', combinational.inv(a))
    combinational.output('mux', combinational.mux(sel, a, b))
    combinational.output('eq', combinational.eq(a, b))
    combinational.output('lt', combinational.compare(a, b, 'lt'))
    combinational.output('signed_lt', combinational.compare(a, b, 'lt', signed=True))
    low = combinational.bits(a, 0, 4)
    middle = combinational.bits(a, 4, 8)
    high = combinational.bits(a, 12, 4)
    combinational.output('low', low)
    combinational.output('middle', middle)
    combinational.output('high', high)
    combinational.output('pack', combinational.pack(low, middle, high))
    combinational.output('extend', combinational.extend(high, 16))
    combinational.output('sign_extend', combinational.extend(high, 16, signed=True))
    combinational.output('const', combinational.const(0x1234, 16))
    packed17 = combinational.pack(*[combinational.bits(a, i, 1) for i in range(16)], sel)
    combinational.output('packed17', packed17)
    combinational.output('after_pack17', combinational.add(a, b))

    sequential = Circuit('PrimitiveSequential')
    sequential.input('clk')
    reset = sequential.input('rst')
    enable = sequential.input('en')
    data = sequential.input('data', 16)
    q = sequential.reg('state', 16, data, enable, reset)
    sequential.output('q', q)
    init = sequential.reg('init_state', 16, data, enable, reset, init=0x1234)
    sequential.output('init_q', init)

    wrapper = Circuit('PrimitiveWrapper')
    wrapped_inputs = {net.name: wrapper.input(net.name, net.width) for net in combinational.inputs}
    wrapped_outputs = wrapper.instance(combinational, wrapped_inputs)
    for label, net in wrapped_outputs.items():
        wrapper.output(label, net)

    write_project(Path(__file__).parent / 'primitive-tests.circ',
                  [combinational, sequential, wrapper], main='PrimitiveCombinational')


if __name__ == '__main__':
    main()
