package tools;

import com.cburch.logisim.circuit.Circuit;
import com.cburch.logisim.circuit.CircuitState;
import com.cburch.logisim.comp.Component;
import com.cburch.logisim.data.BitWidth;
import com.cburch.logisim.data.Value;
import com.cburch.logisim.file.Loader;
import com.cburch.logisim.file.LogisimFile;
import com.cburch.logisim.instance.Instance;
import com.cburch.logisim.instance.StdAttr;
import com.cburch.logisim.proj.Project;
import com.cburch.logisim.std.wiring.Pin;

import java.io.File;
import java.util.LinkedHashMap;
import java.util.Map;

/** Native Logisim unit checks for ALU, rename/PRF, store buffer, and data RAM. */
public final class CoreUnitTests {
  private static int assertions;
  private static int failures;

  private static final class Sim {
    final String name;
    final CircuitState state;
    final Map<String, Component> pins = new LinkedHashMap<>();

    Sim(Project project, LogisimFile file, String name) {
      this.name = name;
      Circuit circuit = file.getCircuit(name);
      if (circuit == null) throw new AssertionError("missing circuit " + name);
      state = CircuitState.createRootState(project, circuit, Thread.currentThread());
      for (Component component : circuit.getNonWires()) {
        if (component.getFactory() instanceof Pin pin) {
          String label = component.getAttributeSet().getValue(StdAttr.LABEL);
          if (pins.put(label, component) != null) throw new AssertionError("duplicate pin " + label);
          if (pin.isInputPin(Instance.getInstanceFor(component))) set(label, 0);
        }
      }
      settle();
      var errors = circuit.getWidthIncompatibilityData();
      if (errors != null && !errors.isEmpty()) {
        throw new AssertionError(name + " has " + errors.size() + " width errors");
      }
    }

    Sim set(String label, long value) {
      Component component = pins.get(label);
      if (component == null) throw new AssertionError(name + ": missing input " + label);
      Instance instance = Instance.getInstanceFor(component);
      Pin pin = (Pin) component.getFactory();
      BitWidth width = pin.getWidth(instance);
      pin.driveInputPin(state.getInstanceState(instance), Value.createKnown(width, value));
      state.markComponentAsDirty(component);
      return this;
    }

    Sim settle() {
      state.getPropagator().propagate();
      if (state.getPropagator().isOscillating()) throw new AssertionError(name + ": oscillating");
      return this;
    }

    Sim tick() {
      set("clk", 0).settle();
      set("clk", 1).settle();
      return set("clk", 0).settle();
    }

    Sim reset() {
      set("clk", 0).set("rst", 1).settle();
      tick();
      return set("rst", 0).settle();
    }

    Sim expect(String label, long expected) {
      Component component = pins.get(label);
      if (component == null) throw new AssertionError(name + ": missing output " + label);
      Pin pin = (Pin) component.getFactory();
      Value value = pin.getValue(state.getInstanceState(Instance.getInstanceFor(component)));
      long mask = value.getWidth() == 64 ? -1L : (1L << value.getWidth()) - 1;
      assertions++;
      if (!value.isFullyDefined() || value.toLongValue() != (expected & mask)) {
        throw new AssertionError(name + "." + label + ": expected 0x"
            + Long.toHexString(expected & mask) + ", got " + value.toHexString());
      }
      return this;
    }
  }

  private static void run(String label, Runnable test) {
    try {
      test.run();
      System.out.println("PASS " + label);
    } catch (Throwable failure) {
      failures++;
      System.out.println("FAIL " + label + ": " + failure.getMessage());
      failure.printStackTrace(System.out);
    }
  }

  private static void alu(Project project, LogisimFile file) {
    Sim sim = new Sim(project, file, "ALU");
    int[][] cases = {
        {0, 0, 0, 0}, {0xffff, 1, 0xffff, 0xffff},
        {0x8000, 0x7fff, 0x8000, 0x1234}, {0x7fff, 0x8000, 0x7fff, 0x8000},
        {0xaa55, 0x55aa, 31, 100}, {7, 7, 0xffe0, 16},
        {0xfffe, 0xffff, 3, 0xfffe}, {0x1234, 0x5678, 0x1234, 1}
    };
    for (int op = 0; op < 16; op++) {
      for (int[] values : cases) {
        int a = values[0], b = values[1], imm = values[2], pc = values[3];
        int pc1 = (pc + 1) & 0xffff;
        int result = switch (op) {
          case 1 -> a + b;
          case 2 -> a - b;
          case 3 -> a & b;
          case 4 -> a | b;
          case 5 -> a ^ b;
          case 6 -> (short) a < (short) b ? 1 : 0;
          case 7 -> a + imm;
          case 12, 13 -> pc1;
          case 14 -> (short) a < (short) imm ? 1 : 0;
          default -> 0;
        };
        boolean taken = op == 10 && a == b || op == 11 && a != b || op == 12 || op == 13;
        int target = op == 13 ? a + imm : pc1 + imm;
        sim.set("op", op).set("a", a).set("b", b).set("imm", imm).set("pc", pc)
            .settle().expect("result", result).expect("addr", a + imm)
            .expect("target", target).expect("taken", taken ? 1 : 0)
            .expect("next", taken ? target : pc1);
      }
    }
  }

  private static void physical(Project project, LogisimFile file) {
    Sim sim = new Sim(project, file, "PhysicalRegister");
    sim.set("id", 8).set("initial_free", 1).reset()
        .expect("value", 0).expect("ready", 1).expect("free", 1);
    sim.set("enable", 1).set("use0", 1).set("alloc0", 8).tick()
        .expect("ready", 0).expect("free", 0);
    sim.set("use0", 0).set("c0_v", 1).set("c0_tag", 9).set("c0_data", 0xaaaa)
        .tick().expect("value", 0).expect("ready", 0);
    sim.set("c0_tag", 8).set("c0_data", 0x1234).tick()
        .expect("value", 0x1234).expect("ready", 1).expect("free", 0);
    sim.set("c0_v", 0).set("c1_v", 1).set("c1_tag", 8).set("c1_data", 0xabcd)
        .tick().expect("value", 0xabcd).expect("ready", 1);
    sim.set("c0_v", 1).set("c0_data", 0x1111).set("c1_data", 0x2222).tick()
        .expect("value", 0x2222);
    sim.set("use1", 1).set("alloc1", 8).tick().expect("ready", 0).expect("free", 0);
    sim.set("c0_v", 0).set("c1_v", 0).set("use1", 0)
        .set("free_v", 1).set("free_tag", 8).tick().expect("free", 1);
    sim.set("use0", 1).tick().expect("free", 0).expect("ready", 0);
    sim.set("use0", 0).set("enable", 0).set("c0_v", 1).set("c0_data", 0xbeef)
        .tick().expect("value", 0x2222).expect("ready", 0).expect("free", 0);
    sim.set("clear", 1).set("rebuild_free", 1).tick()
        .expect("value", 0x2222).expect("ready", 1).expect("free", 1);
    sim.set("rebuild_free", 0).tick().expect("free", 0);
    sim.set("clear", 0).set("initial_free", 0).reset()
        .expect("value", 0).expect("ready", 1).expect("free", 0);
  }

  private static Sim renameBase(Project project, LogisimFile file) {
    return new Sim(project, file, "Rename_PRF").set("enable", 1).reset();
  }

  private static void renameRawWaw(Project project, LogisimFile file) {
    Sim sim = renameBase(project, file);
    sim.set("rw0", 1).set("rd0", 1).set("rs10", 1).set("rs20", 2)
        .set("rw1", 1).set("rd1", 2).set("rs11", 1).set("rs21", 0).settle()
        .expect("new0", 8).expect("new1", 9).expect("available0", 1)
        .expect("available1", 1).expect("old0", 1).expect("old1", 2)
        .expect("qj0", 1).expect("qk0", 2).expect("vj0", 1).expect("vk0", 1)
        .expect("qj1", 8).expect("vj1", 0).expect("qk1", 0).expect("dk1", 0)
        .expect("vk1", 1);
    sim.set("accept0", 1).set("accept1", 1).tick();
    sim.set("accept0", 0).set("accept1", 0).set("rw0", 0).set("rw1", 0)
        .settle().expect("qj0", 8).expect("qk0", 9).expect("vj0", 0).expect("vk0", 0);
    sim.set("c0_v", 1).set("c0_tag", 8).set("c0_data", 55)
        .set("c1_v", 1).set("c1_tag", 9).set("c1_data", 77).settle()
        .expect("vj0", 1).expect("vk0", 1).expect("dj0", 55).expect("dk0", 77);
    sim.tick().set("c0_v", 0).set("c1_v", 0).settle()
        .expect("vj0", 1).expect("vk0", 1).expect("dj0", 55).expect("dk0", 77);
    sim.set("commit_v", 1).set("commit_rw", 1).set("commit_rd", 1)
        .set("commit_new", 8).set("commit_old", 1).set("commit_data", 55).tick()
        .expect("r1", 55).expect("r2", 0);
    sim.set("commit_v", 0).set("rw0", 1).set("rw1", 1).set("rd0", 3).set("rd1", 3)
        .set("rs11", 3).set("rs21", 3).settle()
        .expect("new0", 1).expect("new1", 10).expect("old0", 3).expect("old1", 1)
        .expect("qj1", 1).expect("qk1", 1).expect("vj1", 0).expect("vk1", 0);
    sim.set("accept0", 1).set("accept1", 1).tick();
    sim.set("accept0", 0).set("accept1", 0).set("rw0", 0).set("rw1", 0)
        .set("rs10", 3).settle().expect("qj0", 10).expect("vj0", 0);
    sim.set("rw1", 1).set("rd1", 4).settle().expect("new0", 0).expect("new1", 11);
    sim.set("accept0", 1).set("accept1", 1).tick();
    sim.set("accept0", 0).set("accept1", 0).set("rw1", 0).set("rs10", 4)
        .settle().expect("qj0", 11).expect("vj0", 0);
    sim.set("rs10", 0).set("rs20", 0).set("c0_v", 1).set("c0_tag", 0)
        .set("c0_data", 0xffff).settle().expect("qj0", 0).expect("dj0", 0)
        .expect("dk0", 0).expect("vj0", 1).expect("r0", 0);
    sim.tick().expect("dj0", 0).expect("r0", 0);
  }

  private static void renameRecovery(Project project, LogisimFile file) {
    Sim sim = renameBase(project, file);
    sim.set("rw0", 1).set("rw1", 1).set("rd0", 1).set("rd1", 2)
        .set("accept0", 1).set("accept1", 1).tick();
    sim.set("accept0", 0).set("accept1", 0).set("rw0", 0).set("rw1", 0)
        .set("c0_v", 1).set("c0_tag", 8).set("c0_data", 10)
        .set("c1_v", 1).set("c1_tag", 9).set("c1_data", 20).tick();
    sim.set("c0_v", 0).set("c1_v", 0).set("commit_v", 1).set("commit_rw", 1)
        .set("commit_rd", 1).set("commit_new", 8).set("commit_old", 1)
        .set("commit_data", 10).tick().expect("r1", 10).expect("r2", 0);
    sim.set("commit_v", 0).set("clear", 1).set("enable", 0).tick();
    sim.set("clear", 0).set("rs10", 1).set("rs20", 2).set("rw0", 1)
        .set("rw1", 1).set("rd0", 5).set("rd1", 6).settle()
        .expect("qj0", 8).expect("qk0", 2).expect("dj0", 10).expect("dk0", 0)
        .expect("vj0", 1).expect("vk0", 1).expect("new0", 1).expect("new1", 9)
        .expect("r1", 10).expect("r2", 0);
    sim.set("enable", 1).set("rw1", 0).set("rd0", 1).set("accept0", 1).tick();
    sim.set("accept0", 0).set("rw0", 0).settle().expect("qj0", 1).expect("vj0", 0);
    sim.set("clear", 1).tick().set("clear", 0).settle()
        .expect("qj0", 8).expect("vj0", 1).expect("dj0", 10).expect("r1", 10);
    sim.reset().set("rw0", 1).set("rw1", 1).settle()
        .expect("qj0", 1).expect("qk0", 2).expect("dj0", 0).expect("dk0", 0)
        .expect("vj0", 1).expect("vk0", 1).expect("new0", 8).expect("new1", 9)
        .expect("r1", 0).expect("r2", 0);
  }

  private static void renameCapacity(Project project, LogisimFile file) {
    Sim sim = renameBase(project, file);
    sim.set("rw0", 1).set("rw1", 1).set("rd0", 1).set("rd1", 2)
        .set("accept0", 1).set("accept1", 1);
    for (int i = 0; i < 28; i++) {
      sim.settle().expect("available0", 1).expect("available1", 1)
          .expect("new0", 8 + 2 * i).expect("new1", 9 + 2 * i).tick();
    }
    sim.set("accept0", 0).set("accept1", 0).settle()
        .expect("available0", 0).expect("available1", 0);
    sim.set("commit_v", 1).set("commit_rw", 1).set("commit_rd", 1)
        .set("commit_new", 8).set("commit_old", 1).set("commit_data", 1).tick();
    sim.set("commit_v", 0).settle().expect("available0", 1).expect("available1", 0)
        .expect("new0", 1);
    sim.set("rw0", 0).settle().expect("available0", 1).expect("available1", 1)
        .expect("new1", 1);
  }

  private static void stores(Project project, LogisimFile file) {
    Sim sim = new Sim(project, file, "StoreBuffer").set("enable", 1).reset();
    sim.expect("count", 0).expect("full", 0).expect("forward", 0).expect("commit_found", 0);
    sim.set("push", 1).set("addr", 0x20).set("data", 0x1111).set("sn", 250).tick();
    sim.set("push", 0).set("load_addr", 0x20).set("load_sn", 251).settle()
        .expect("count", 1).expect("forward", 1).expect("forward_data", 0x1111);
    sim.set("push", 1).set("data", 0x2222).set("sn", 252).tick();
    sim.set("push", 0).settle().expect("forward_data", 0x1111);
    sim.set("load_sn", 253).settle().expect("forward_data", 0x2222);
    sim.set("push", 1).set("data", 0x3333).set("sn", 1).tick();
    sim.set("push", 0).set("load_sn", 2).settle().expect("forward_data", 0x3333);
    sim.set("load_sn", 1).settle().expect("forward_data", 0x2222);
    sim.set("load_sn", 250).settle().expect("forward", 0);
    sim.set("load_addr", 0x21).set("load_sn", 2).settle().expect("forward", 0);
    sim.set("commit_sn", 252).settle().expect("commit_found", 1)
        .expect("commit_addr", 0x20).expect("commit_data", 0x2222);
    sim.set("commit", 1).tick().expect("count", 2).expect("commit_found", 0);
    sim.set("commit", 0).set("load_addr", 0x20).set("load_sn", 253)
        .settle().expect("forward_data", 0x1111);
    sim.set("commit", 1).set("commit_sn", 250).set("push", 1).set("data", 0x4444)
        .set("sn", 2).tick().expect("count", 2);
    sim.set("commit", 0).set("push", 0).set("load_sn", 3).settle()
        .expect("forward", 1).expect("forward_data", 0x4444);
    sim.set("clear", 1).settle().expect("count", 0).expect("forward", 0);
    sim.set("clear", 0).set("push", 1);
    for (int i = 0; i < 8; i++) {
      sim.set("addr", 0x100 + i).set("data", 0xa000 + i).set("sn", 10 + i)
          .tick().expect("count", i + 1);
    }
    sim.expect("full", 1).set("sn", 99).tick().expect("count", 8);
    sim.set("commit", 1).set("commit_sn", 13).tick().expect("count", 7);
    sim.set("commit", 0).set("enable", 0).tick().expect("count", 7).expect("full", 0);
    sim.set("enable", 1).tick().expect("count", 8).expect("full", 1);
    sim.set("push", 0).reset().expect("count", 0).expect("forward", 0)
        .expect("commit_found", 0).expect("full", 0);
  }

  private static void memory(Project project, LogisimFile file) {
    Sim sim = new Sim(project, file, "DataMemory").reset();
    sim.expect("data", 0).expect("debug_data", 0);
    int[][] words = {{0x1234, 0xabcd}, {0xfffe, 0x1357}, {0xffff, 0x2468}, {0, 0xbeef}};
    for (int[] word : words) {
      sim.set("commit", 1).set("write_addr", word[0]).set("write_data", word[1])
          .set("read_addr", 0x7777).set("debug_addr", 0x8888).tick();
      sim.set("commit", 0).set("read_addr", word[0]).set("debug_addr", word[0])
          .settle().expect("data", word[1]).expect("debug_data", word[1]);
    }
    sim.set("write_addr", 0x1234).set("write_data", 0xdead).tick();
    for (int i = 0; i < words.length; i++) {
      int[] read = words[i];
      int[] debug = words[(i + 1) % words.length];
      sim.set("read_addr", read[0]).set("debug_addr", debug[0]).settle()
          .expect("data", read[1]).expect("debug_data", debug[1]);
    }
    sim.set("read_addr", 0x7777).set("debug_addr", 0x8888).settle()
        .expect("data", 0).expect("debug_data", 0);
    sim.reset();
    for (int[] word : words) {
      sim.set("read_addr", word[0]).set("debug_addr", word[0]).settle()
          .expect("data", 0).expect("debug_data", 0);
    }

    // The mapped window is an external combinational read and a retirement-only write.
    sim.set("io_read_data", 0x5a3c).set("read_addr", 0xf000)
        .set("debug_addr", 0xf000).set("read_enable", 1).settle()
        .expect("io_addr", 0xf000).expect("io_read", 1).expect("io_write", 0)
        .expect("data", 0x5a3c).expect("debug_data", 0);
    sim.set("read_enable", 0).settle().expect("io_read", 0);
    sim.set("commit", 1).set("write_addr", 0xf0ff).set("write_data", 0x8123)
        .set("read_enable", 1).tick()
        .expect("io_addr", 0xf0ff).expect("io_write_data", 0x8123)
        .expect("io_write", 1).expect("io_read", 0);
    sim.set("commit", 0).set("debug_addr", 0xf0ff).settle()
        .expect("io_write", 0).expect("debug_data", 0);
    sim.set("commit", 1).set("write_addr", 0xf100).set("write_data", 0x4567).tick()
        .expect("io_write", 0);
    sim.set("commit", 0).set("read_addr", 0xf100).set("debug_addr", 0xf100)
        .settle().expect("io_read", 0).expect("data", 0x4567)
        .expect("debug_data", 0x4567);
  }

  public static void main(String[] args) throws Exception {
    System.setProperty("java.awt.headless", "true");
    com.cburch.logisim.Main.headless = true;
    if (args.length != 1) throw new IllegalArgumentException("provide core-unit-tests.circ");
    Loader loader = new Loader(null) {
      @Override public void showError(String message) {
        throw new IllegalArgumentException("Logisim loader: " + message);
      }
    };
    LogisimFile file = loader.openLogisimFile(new File(args[0]).getAbsoluteFile());
    Project project = new Project(file);
    try {
      project.getSimulator().setAutoTicking(false);
      project.getSimulator().setAutoPropagation(false);
      run("ALU arithmetic, signed compare, branch/jump, wrap", () -> alu(project, file));
      run("PhysicalRegister allocation, dual CDB, free, pause, recovery", () -> physical(project, file));
      run("Rename_PRF RAW, WAW, dual CDB, lane1-only allocation, p0", () -> renameRawWaw(project, file));
      run("Rename_PRF committed recovery, FL rebuild, reset", () -> renameRecovery(project, file));
      run("Rename_PRF all 56 initial free registers and exhaustion", () -> renameCapacity(project, file));
      run("StoreBuffer youngest older forwarding, SN wrap, commit, capacity", () -> stores(project, file));
      run("DataMemory RAM, mapped I/O, debug read, reset", () -> memory(project, file));
      System.out.println("CORE_UNIT_RESULT assertions=" + assertions + " failures=" + failures);
    } finally {
      project.getSimulator().shutDown();
    }
    System.exit(failures == 0 ? 0 : 1);
  }
}
