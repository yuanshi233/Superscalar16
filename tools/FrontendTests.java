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

/** Native Logisim regression checks for fetch, prediction, FIFO, ROM, and decode. */
public final class FrontendTests {
  private static int assertions;

  private static final class Sim {
    final CircuitState state;
    final Map<String, Component> pins = new LinkedHashMap<>();

    Sim(Project project, LogisimFile file, String name) {
      Circuit circuit = file.getCircuit(name);
      if (circuit == null) throw new AssertionError("missing circuit " + name);
      state = CircuitState.createRootState(project, circuit, Thread.currentThread());
      for (Component component : circuit.getNonWires()) {
        if (component.getFactory() instanceof Pin pin) {
          String label = component.getAttributeSet().getValue(StdAttr.LABEL);
          pins.put(label, component);
          if (pin.isInputPin(Instance.getInstanceFor(component))) set(label, 0);
        }
      }
      settle();
      var errors = circuit.getWidthIncompatibilityData();
      if (errors != null && !errors.isEmpty()) {
        throw new AssertionError(name + " has " + errors.size() + " width errors");
      }
    }

    Sim set(String name, long value) {
      Component component = pins.get(name);
      if (component == null) throw new AssertionError("missing input " + name);
      Instance instance = Instance.getInstanceFor(component);
      Pin pin = (Pin) component.getFactory();
      BitWidth width = pin.getWidth(instance);
      pin.driveInputPin(state.getInstanceState(instance), Value.createKnown(width, value));
      state.markComponentAsDirty(component);
      return this;
    }

    Sim settle() {
      state.getPropagator().propagate();
      if (state.getPropagator().isOscillating()) throw new AssertionError("oscillating");
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

    Sim expect(String name, long expected) {
      Component component = pins.get(name);
      Pin pin = (Pin) component.getFactory();
      Value value = pin.getValue(state.getInstanceState(Instance.getInstanceFor(component)));
      long mask = value.getWidth() == 64 ? -1L : (1L << value.getWidth()) - 1;
      assertions++;
      if (!value.isFullyDefined() || value.toLongValue() != (expected & mask)) {
        throw new AssertionError(name + ": expected 0x" + Long.toHexString(expected & mask)
            + ", got " + value.toHexString());
      }
      return this;
    }
  }

  private static boolean oneOf(int opcode, int... values) {
    for (int value : values) if (opcode == value) return true;
    return false;
  }

  private static long pack(long[] values, int[] widths) {
    long result = 0;
    int shift = 0;
    for (int i = 0; i < values.length; i++) {
      result |= (values[i] & ((1L << widths[i]) - 1)) << shift;
      shift += widths[i];
    }
    return result;
  }

  private static void decode(Project project, LogisimFile file) {
    Sim sim = new Sim(project, file, "DecodeOne");
    int[] samples = {0x000, 0x205, 0x3ff, 0x7c0, 0xa38, 0xfff};
    int[] aluOps = {0, 0, 1, 2, 3, 4, 5, 0, 0, 0, 1, 1, 0, 0, 5, 0};
    for (int opcode = 0; opcode < 16; opcode++) {
      for (int sample : samples) {
        int instr = opcode << 12 | sample;
        int pc = (0xa500 + sample) & 0xffff;
        int pred = sample & 1;
        boolean rw = oneOf(opcode, 1, 2, 3, 4, 5, 6, 7, 8, 12, 13, 14);
        boolean branch = opcode == 10 || opcode == 11;
        boolean jump = opcode == 12 || opcode == 13;
        int rd = rw ? instr >> 9 & 7 : 0;
        int rs1 = branch || opcode == 9 ? instr >> 9 & 7
            : oneOf(opcode, 1, 2, 3, 4, 5, 6, 7, 8, 13, 14) ? instr >> 6 & 7 : 0;
        int rs2 = branch || opcode == 9 ? instr >> 6 & 7
            : opcode >= 1 && opcode <= 6 ? instr >> 3 & 7 : 0;
        int imm = opcode == 12 ? (instr & 511) << 23 >> 23
            : oneOf(opcode, 7, 8, 9, 10, 11, 13, 14) ? (instr & 63) << 26 >> 26 : 0;
        int load = opcode == 8 ? 1 : 0;
        int store = opcode == 9 ? 1 : 0;
        int halt = opcode == 15 ? 1 : 0;
        int aluSrc = oneOf(opcode, 7, 8, 9, 14) ? 1 : 0;
        long packet = pack(new long[] {opcode, rd, rs1, rs2, imm, rw ? 1 : 0,
            load, store, branch ? 1 : 0, jump ? 1 : 0, aluOps[opcode], aluSrc,
            load, halt, pc, pred}, new int[] {4, 3, 3, 3, 16, 1, 1, 1, 1, 1,
            3, 1, 1, 1, 16, 1});
        sim.set("instr", instr).set("pc", pc).set("pred", pred).settle()
            .expect("opcode", opcode).expect("rd", rd).expect("rs1", rs1)
            .expect("rs2", rs2).expect("imm", imm).expect("rw", rw ? 1 : 0)
            .expect("mem", load | store).expect("store", store)
            .expect("branch", branch ? 1 : 0).expect("jump", jump ? 1 : 0)
            .expect("halt", halt).expect("packet", packet);
      }
    }
    System.out.println("PASS DecodeOne (all opcodes, edge fields, packet layout)");
  }

  private static void btb(Project project, LogisimFile file) {
    Sim sim = new Sim(project, file, "BTB64");
    sim.reset().set("pc0", 5).set("pc1", 6).settle()
        .expect("pred0", 0).expect("pred1", 0);
    sim.set("we", 1).set("update_pc", 5).set("target", 0x2345).set("taken", 0)
        .tick().expect("pred0", 1).expect("target0", 0x2345);
    sim.tick().expect("pred0", 0);
    sim.tick().expect("pred0", 0);
    sim.tick().expect("pred0", 0);
    sim.set("taken", 1).tick().expect("pred0", 0);
    sim.tick().expect("pred0", 1);
    sim.tick().expect("pred0", 1);
    sim.tick().expect("pred0", 1);
    sim.set("taken", 0).tick().expect("pred0", 1);
    sim.tick().expect("pred0", 0);
    sim.set("update_pc", 69).set("target", 0x4321).tick().expect("pred0", 0);
    sim.set("pc0", 69).settle().expect("pred0", 1).expect("target0", 0x4321);
    sim.set("update_pc", 6).set("target", 0xbeef).tick()
        .expect("pred0", 1).expect("pred1", 1).expect("target1", 0xbeef);
    sim.set("we", 0).set("taken", 0).tick().expect("pred1", 1);
    sim.reset().expect("pred0", 0).expect("pred1", 0);
    System.out.println("PASS BTB64 (dual query, tag replacement, saturation, reset)");
  }

  private static void fifo(Project project, LogisimFile file) {
    Sim sim = new Sim(project, file, "FetchFIFO8");
    sim.reset().set("enable", 1).set("write_en", 1)
        .set("in_instr0", 0x1234).set("in_instr1", 0x5678).set("in_pc", 0x1000)
        .set("in_next0", 0x1001).set("in_next1", 0x9876).set("in_pred1", 1)
        .tick().expect("count", 1).expect("valid0", 1).expect("valid1", 1)
        .expect("instr0", 0x1234).expect("instr1", 0x5678).expect("pc0", 0x1000)
        .expect("pc1", 0x1001).expect("pred0", 0).expect("pred1", 1)
        .expect("next0", 0x1001).expect("next1", 0x9876);
    sim.set("write_en", 0).set("consume", 1).tick()
        .expect("count", 1).expect("valid0", 1).expect("valid1", 0)
        .expect("instr0", 0x5678).expect("pc0", 0x1001).expect("pred0", 1)
        .expect("next0", 0x9876);
    sim.set("enable", 0).tick().expect("count", 1).expect("valid0", 0);
    sim.set("enable", 1).tick().expect("count", 0).expect("valid0", 0);
    sim.set("consume", 0).set("write_en", 1).set("in_pred0", 1).tick()
        .expect("valid0", 1).expect("valid1", 0);
    sim.set("write_en", 0).set("consume", 1).tick().expect("count", 0);
    sim.set("consume", 0).set("write_en", 1).set("in_pred0", 0);
    for (int i = 0; i < 8; i++) sim.set("in_instr0", i).tick().expect("count", i + 1);
    sim.expect("full", 1).expect("instr0", 0);
    sim.set("in_instr0", 0xffff).tick().expect("count", 8).expect("instr0", 0);
    sim.set("write_en", 0).set("consume", 2).tick()
        .expect("count", 7).expect("instr0", 1).expect("full", 0);
    sim.set("write_en", 1).set("in_instr0", 8).tick()
        .expect("count", 7).expect("instr0", 2);
    sim.set("write_en", 0);
    for (int i = 3; i <= 8; i++) sim.tick().expect("instr0", i).expect("count", 9 - i);
    sim.tick().expect("count", 0).expect("valid0", 0);
    sim.set("consume", 0).set("write_en", 1).tick().expect("count", 1);
    sim.set("flush", 1).settle().expect("count", 0).expect("valid0", 0);
    sim.tick().expect("count", 0);
    sim.set("flush", 0).tick().expect("count", 1);
    System.out.println("PASS FetchFIFO8 (partial consume, full, wrap, pause, flush)");
  }

  private static void fetch(Project project, LogisimFile file) {
    Sim sim = new Sim(project, file, "FetchUnit");
    sim.reset().tick().expect("fetch_pc", 0).expect("fifo_count", 0);
    sim.set("enable", 1).tick().expect("fetch_pc", 2).expect("fifo_count", 1)
        .expect("pc0", 0).expect("pc1", 1).expect("instr0", 0x7205)
        .expect("instr1", 0x7443).expect("next0", 1).expect("next1", 2);
    sim.tick().expect("fetch_pc", 4).expect("fifo_count", 2);
    sim.set("enable", 0).set("flush", 1).set("recover_pc", 8).tick()
        .expect("fetch_pc", 8).expect("fifo_count", 0);
    sim.set("flush", 0).set("bp_we", 1).set("bp_pc", 8).set("bp_target", 100)
        .set("bp_taken", 1).tick().expect("fetch_pc", 8);
    sim.set("bp_pc", 9).set("bp_target", 200).tick();
    sim.set("bp_we", 0).set("enable", 1).tick()
        .expect("fetch_pc", 100).expect("pc0", 8).expect("pred0", 1)
        .expect("next0", 100).expect("valid0", 1).expect("valid1", 0);
    sim.set("flush", 1).set("recover_pc", 8).tick()
        .expect("fetch_pc", 8).expect("fifo_count", 0);
    sim.set("flush", 0).set("enable", 0).set("bp_we", 1).set("bp_pc", 8)
        .set("bp_target", 100).set("bp_taken", 0).tick();
    sim.set("bp_we", 0).set("enable", 1).tick()
        .expect("fetch_pc", 200).expect("pred0", 0).expect("pred1", 1)
        .expect("valid1", 1).expect("next0", 9).expect("next1", 200);
    sim.set("consume", 1).set("enable", 0).tick().expect("fetch_pc", 200)
        .expect("pc0", 8);
    sim.set("enable", 1).tick().expect("pc0", 9).expect("valid1", 0)
        .expect("next0", 200);
    System.out.println("PASS FetchUnit (dual fetch, stall, recover, pred0 priority, pred1)");
  }

  private static void imem(Project project, LogisimFile file) {
    Sim sim = new Sim(project, file, "InstructionMemory");
    sim.expect("instr0", 0x7205).expect("instr1", 0x7443);
    sim.set("pc", 4).settle().expect("instr0", 0x8800).expect("instr1", 0xf000);
    sim.set("pc", 0xffff).settle().expect("instr0", 0).expect("instr1", 0x7205);
    System.out.println("PASS InstructionMemory (dual port, address wrap)");
  }

  public static void main(String[] args) throws Exception {
    System.setProperty("java.awt.headless", "true");
    com.cburch.logisim.Main.headless = true;
    if (args.length != 1) throw new IllegalArgumentException("provide frontend-tests.circ");
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
      decode(project, file);
      btb(project, file);
      fifo(project, file);
      imem(project, file);
      fetch(project, file);
      System.out.println("PASS frontend assertions=" + assertions);
    } catch (Throwable failure) {
      failure.printStackTrace();
      System.exit(1);
    } finally {
      project.getSimulator().shutDown();
    }
    System.exit(0);
  }
}
