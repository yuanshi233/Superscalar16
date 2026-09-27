"""Generate the synchronous 16-by-8 LED column buffer used by the Fibonacci demo."""
from pathlib import Path
import xml.etree.ElementTree as ET

from circuit import Circuit


ROOT = Path(__file__).resolve().parents[1]


def build_led_control():
    c = Circuit("LedControl", "Synchronous 16 x 8 LED column buffer")
    data = c.input("data", 8)
    c.input("clk")
    reset = c.input("reset")
    write_enable = c.input("write_enable")

    # The CPU clock is shared by every state element.  The accepted F000
    # transaction is an enable, never a derived or decoder-generated clock.
    index_ref = c.ref("column_index", 4)
    index = c.reg(
        "column_index",
        4,
        c.inc(index_ref),
        en=write_enable,
        rst=reset,
    )

    decoder_x, decoder_y = c.loc(height=280)
    c.comp(2, "Decoder", decoder_x, decoder_y, select=4)
    c.wire((decoder_x, decoder_y), (decoder_x, decoder_y + 20))
    c.port(decoder_x, decoder_y + 20, index)
    c.port(decoder_x - 10, decoder_y, c.const(1))

    write_enables = []
    for i in range(16):
        decoded = c.new(1, f"selected_column{i}")
        output_y = decoder_y - 160 + 10 * i
        c.port(decoder_x + 20, output_y, decoded, output=True, reach=100)
        write_enables.append(c.land(write_enable, decoded))

    for i in range(16):
        column = c.reg(
            f"column{i}",
            8,
            data,
            en=write_enables[i],
            rst=reset,
        )
        c.output(f"column{i}", column)

    circuit = c.finish()
    circuit.remove(circuit.find("appear"))
    appearance = ET.SubElement(circuit, "appear")
    ET.SubElement(
        appearance,
        "rect",
        fill="none",
        height="168",
        stroke="#000000",
        width="170",
        x="54",
        y="56",
    )
    ET.SubElement(appearance, "circ-anchor", facing="east", x="200", y="70")

    # At the dashboard instance these become y=1460, 1500, 1540 and 1580.
    input_positions = {
        "data": (50, 60),
        "clk": (50, 100),
        "reset": (50, 140),
        "write_enable": (50, 180),
    }
    for name, (x, y) in input_positions.items():
        pin_x, pin_y = c._pin_locations[name, False]
        ET.SubElement(
            appearance,
            "circ-port",
            dir="in",
            pin=f"{pin_x},{pin_y}",
            x=str(x),
            y=str(y),
        )

    for i in range(16):
        pin_x, pin_y = c._pin_locations[f"column{i}", True]
        ET.SubElement(
            appearance,
            "circ-port",
            dir="out",
            pin=f"{pin_x},{pin_y}",
            x=str(60 + 10 * i),
            y="220",
        )
    return c


def write_project(path, circuit):
    root = ET.Element("project", source="5.0.0", version="1.0")
    for i, name in enumerate(
        ["Wiring", "Gates", "Plexers", "Arithmetic", "Memory", "I/O", "Base"]
    ):
        ET.SubElement(root, "lib", name=str(i), desc="#" + name)
    ET.SubElement(root, "main", name="LedControl")
    options = ET.SubElement(root, "options")
    Circuit.attr(options, "simlimit", 100000)
    ET.SubElement(root, "mappings")
    ET.SubElement(root, "toolbar")
    root.append(circuit.xml)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    output = ROOT / "build" / "LedControl_fixed.circ"
    write_project(output, build_led_control())
    print(output)
