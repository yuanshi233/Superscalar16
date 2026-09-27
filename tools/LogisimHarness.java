package tools;

import com.cburch.logisim.circuit.Circuit;
import com.cburch.logisim.circuit.CircuitState;
import com.cburch.logisim.circuit.SplitterFactory;
import com.cburch.logisim.circuit.WidthIncompatibilityData;
import com.cburch.logisim.comp.Component;
import com.cburch.logisim.comp.ComponentFactory;
import com.cburch.logisim.comp.EndData;
import com.cburch.logisim.data.Attribute;
import com.cburch.logisim.data.AttributeSet;
import com.cburch.logisim.data.BitWidth;
import com.cburch.logisim.data.Location;
import com.cburch.logisim.data.Value;
import com.cburch.logisim.file.Loader;
import com.cburch.logisim.file.LogisimFile;
import com.cburch.logisim.gui.hex.HexFile;
import com.cburch.logisim.instance.Instance;
import com.cburch.logisim.instance.InstanceFactory;
import com.cburch.logisim.instance.StdAttr;
import com.cburch.logisim.proj.Project;
import com.cburch.logisim.std.arith.Adder;
import com.cburch.logisim.std.memory.Mem;
import com.cburch.logisim.std.memory.MemContents;
import com.cburch.logisim.std.memory.Ram;
import com.cburch.logisim.std.memory.RamAppearance;
import com.cburch.logisim.std.memory.Register;
import com.cburch.logisim.std.memory.Rom;
import com.cburch.logisim.std.plexers.Multiplexer;
import com.cburch.logisim.std.wiring.Constant;
import com.cburch.logisim.std.wiring.Pin;
import com.cburch.logisim.std.wiring.Tunnel;

import java.io.File;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Small, read-only command-line harness for Logisim-evolution 5.0.0 circuits.
 *
 * <p>The harness mutates only in-memory project and simulation data; it never writes the loaded
 * .circ file. It is dependency-free apart from the Logisim all-in-one jar.</p>
 */
public final class LogisimHarness {
  private LogisimHarness() {}

  private static final class Options {
    File file;
    String circuit;
    String clock;
    int cycles;
    int ticks;
    boolean allCircuits;
    boolean components;
    boolean nets;
    boolean strict;
    boolean catalog;
    File cpuProgram;
    int maxCycles = 1000;
    File script;
    boolean suite;
    final Map<String, String> assignments = new LinkedHashMap<>();
  }

  private static final class PinRef {
    final Component component;
    final Instance instance;
    final Pin factory;
    final String label;
    final boolean input;

    PinRef(Component component) {
      this.component = component;
      this.instance = Instance.getInstanceFor(component);
      this.factory = (Pin) component.getFactory();
      this.label = safeLabel(component.getAttributeSet());
      this.input = factory.isInputPin(instance);
    }

    BitWidth width() {
      return factory.getWidth(instance);
    }
  }

  public static void main(String[] args) throws Exception {
    try {
      runMain(args);
    } catch (Throwable ex) {
      ex.printStackTrace();
      System.exit(2);
    }
  }

  private static void runMain(String[] args) throws Exception {
    System.setProperty("java.awt.headless", "true");
    com.cburch.logisim.Main.headless = true;
    Locale.setDefault(Locale.ROOT);

    final Options options;
    try {
      options = parse(args);
    } catch (IllegalArgumentException ex) {
      System.err.println("ERROR: " + ex.getMessage());
      usage();
      System.exit(2);
      return;
    }

    if (options.catalog) {
      printCatalog();
      return;
    }
    if (options.file == null) {
      usage();
      System.exit(2);
      return;
    }

    Project project = null;
    try {
      Loader loader = new Loader(null) {
        @Override public void showError(String message) {
          throw new IllegalArgumentException("Logisim loader: " + message);
        }
        @Override public int showOptions(String message, String title, String[] options, int choice) {
          throw new IllegalArgumentException("Logisim loader prompt: " + title + ": " + message);
        }
      };
      LogisimFile logisimFile = loader.openLogisimFile(options.file);
      if (options.cpuProgram != null) loadCpuProgram(logisimFile, options.cpuProgram);
      project = new Project(logisimFile);
      project.getSimulator().setAutoTicking(false);
      project.getSimulator().setAutoPropagation(false);

      if (options.suite) {
        int failures = runSuite(project, logisimFile);
        if (failures != 0) System.exit(1);
      } else {
        List<Circuit> targets = selectCircuits(logisimFile, options);
        int failures = 0;
        for (Circuit circuit : targets) {
          failures += runCircuit(project, circuit, options);
        }
        if (options.strict && failures != 0) {
          System.exit(1);
        }
      }
    } finally {
      if (project != null) {
        project.getSimulator().shutDown();
      }
    }
    System.exit(0);
  }

  private static Options parse(String[] args) {
    Options out = new Options();
    for (int i = 0; i < args.length; i++) {
      String arg = args[i];
      switch (arg) {
        case "--help", "-h" -> {
          usage();
          System.exit(0);
        }
        case "--catalog" -> out.catalog = true;
        case "--cpu" -> out.cpuProgram = new File(requireArg(args, ++i, arg)).getAbsoluteFile();
        case "--max-cycles" -> out.maxCycles = nonNegativeInt(requireArg(args, ++i, arg), arg);
        case "--script" -> out.script = new File(requireArg(args, ++i, arg)).getAbsoluteFile();
        case "--suite" -> out.suite = true;
        case "--circuit" -> out.circuit = requireArg(args, ++i, arg);
        case "--clock" -> out.clock = requireArg(args, ++i, arg);
        case "--cycles" -> out.cycles = nonNegativeInt(requireArg(args, ++i, arg), arg);
        case "--ticks" -> out.ticks = nonNegativeInt(requireArg(args, ++i, arg), arg);
        case "--set" -> addAssignment(out, requireArg(args, ++i, arg));
        case "--all-circuits" -> out.allCircuits = true;
        case "--components" -> out.components = true;
        case "--nets" -> out.nets = true;
        case "--strict" -> out.strict = true;
        default -> {
          if (arg.startsWith("--set=")) {
            addAssignment(out, arg.substring("--set=".length()));
          } else if (arg.startsWith("-")) {
            throw new IllegalArgumentException("unknown option: " + arg);
          } else if (out.file == null) {
            out.file = new File(arg).getAbsoluteFile();
          } else {
            throw new IllegalArgumentException("unexpected argument: " + arg);
          }
        }
      }
    }
    if (out.allCircuits && out.circuit != null) {
      throw new IllegalArgumentException("--all-circuits and --circuit are mutually exclusive");
    }
    if (out.catalog && out.file != null) {
      throw new IllegalArgumentException("--catalog does not take a .circ file");
    }
    if (out.file != null && !out.file.isFile()) {
      throw new IllegalArgumentException("file not found: " + out.file);
    }
    if (out.cpuProgram != null) {
      if (!out.cpuProgram.isFile()) throw new IllegalArgumentException("program not found: " + out.cpuProgram);
      if (out.allCircuits) throw new IllegalArgumentException("--cpu cannot be used with --all-circuits");
      if (out.circuit == null) out.circuit = "CPU_Core";
    }
    if (out.script != null && !out.script.isFile()) {
      throw new IllegalArgumentException("script not found: " + out.script);
    }
    if (out.suite && (out.cpuProgram != null || out.script != null || out.allCircuits)) {
      throw new IllegalArgumentException("--suite cannot be combined with --cpu/--script/--all-circuits");
    }
    return out;
  }

  private static void addAssignment(Options out, String text) {
    int split = text.indexOf('=');
    if (split <= 0 || split == text.length() - 1) {
      throw new IllegalArgumentException("assignment must be LABEL=VALUE: " + text);
    }
    out.assignments.put(text.substring(0, split), text.substring(split + 1));
  }

  private static String requireArg(String[] args, int index, String option) {
    if (index >= args.length) {
      throw new IllegalArgumentException("missing value after " + option);
    }
    return args[index];
  }

  private static int nonNegativeInt(String text, String option) {
    try {
      int value = Integer.parseInt(text);
      if (value < 0) throw new NumberFormatException();
      return value;
    } catch (NumberFormatException ex) {
      throw new IllegalArgumentException(option + " requires a non-negative integer: " + text);
    }
  }

  private static List<Circuit> selectCircuits(LogisimFile file, Options options) {
    if (options.allCircuits) return file.getCircuits();
    if (options.circuit == null) return List.of(file.getMainCircuit());
    Circuit selected = file.getCircuit(options.circuit);
    if (selected == null) {
      throw new IllegalArgumentException("circuit not found: " + options.circuit);
    }
    return List.of(selected);
  }

  private static int runCircuit(Project project, Circuit circuit, Options options) throws Exception {
    CircuitState state = CircuitState.createRootState(project, circuit, Thread.currentThread());
    state.getPropagator().propagate();

    List<PinRef> pins = pins(circuit);
    int failures = 0;
    for (Map.Entry<String, String> entry : options.assignments.entrySet()) {
      PinRef pin = uniqueInput(pins, entry.getKey(), circuit.getName());
      Value value = parseValue(pin.width(), entry.getValue());
      drive(state, pin, value);
    }
    state.getPropagator().propagate();

    PinRef manualClock = null;
    if (options.clock != null) {
      manualClock = uniqueInput(pins, options.clock, circuit.getName());
      if (manualClock.width().getWidth() != 1) {
        throw new IllegalArgumentException("clock pin must be one bit: " + options.clock);
      }
      drive(state, manualClock, Value.FALSE);
      state.getPropagator().propagate();
    }

    for (int i = 0; i < options.cycles; i++) {
      if (manualClock == null) {
        state.getPropagator().toggleClocks();
        state.getPropagator().propagate();
        state.getPropagator().toggleClocks();
        state.getPropagator().propagate();
      } else {
        drive(state, manualClock, Value.TRUE);
        state.getPropagator().propagate();
        drive(state, manualClock, Value.FALSE);
        state.getPropagator().propagate();
      }
    }
    boolean high = false;
    for (int i = 0; i < options.ticks; i++) {
      if (manualClock == null) {
        state.getPropagator().toggleClocks();
      } else {
        high = !high;
        drive(state, manualClock, high ? Value.TRUE : Value.FALSE);
      }
      state.getPropagator().propagate();
    }
    if (options.cpuProgram != null) failures += runCpu(state, pins, options.maxCycles);
    if (options.script != null) failures += runScript(state, pins, options.script);

    System.out.printf("CIRCUIT %s components=%d wires=%d ticks=%d oscillating=%s%n",
        circuit.getName(), circuit.getNonWires().size(), circuit.getWires().size(),
        state.getPropagator().getTickCount(), state.getPropagator().isOscillating());

    Set<WidthIncompatibilityData> widthErrors = circuit.getWidthIncompatibilityData();
    if (widthErrors == null) widthErrors = Set.of();
    System.out.printf("WIDTH_ERRORS %d%n", widthErrors.size());
    for (WidthIncompatibilityData mismatch : widthErrors) {
      StringBuilder line = new StringBuilder("  mismatch");
      for (int i = 0; i < mismatch.size(); i++) {
        line.append(' ').append(mismatch.getPoint(i)).append(':')
            .append(mismatch.getBitWidth(i).getWidth());
      }
      System.out.println(line);
    }
    failures += widthErrors.size();
    if (state.getPropagator().isOscillating()) failures++;

    System.out.println("PINS");
    for (PinRef pin : pins) {
      Value value = pin.factory.getValue(state.getInstanceState(pin.instance));
      System.out.printf("  %-3s %-28s loc=%-12s width=%-2d value=%s%n",
          pin.input ? "IN" : "OUT", quoted(pin.label), pin.component.getLocation(),
          pin.width().getWidth(), formatValue(value));
      if (hasError(value)) failures++;
    }

    if (options.components) printComponents(circuit, state);
    if (options.nets) failures += printNets(circuit, state);
    System.out.printf("RESULT %s failures=%d%n", circuit.getName(), failures);
    return failures;
  }

  private static void loadCpuProgram(LogisimFile file, File program) throws Exception {
    int count = 0;
    for (Circuit circuit : file.getCircuits()) {
      for (Component component : circuit.getNonWires()) {
        String label = safeLabel(component.getAttributeSet());
        if (component.getFactory() instanceof Rom && (label.equals("IMEM0") || label.equals("IMEM1"))) {
          MemContents contents = Rom.getMemContents(Instance.getInstanceFor(component));
          contents.clear();
          if (!HexFile.open(contents, program)) {
            throw new IllegalArgumentException("cannot load program: " + program);
          }
          count++;
        }
      }
    }
    if (count != 2) throw new IllegalArgumentException("expected IMEM0 and IMEM1 ROMs, found " + count);
    System.out.printf("PROGRAM file=%s roms=%d%n", program, count);
  }

  private static int runSuite(Project project, LogisimFile file) throws Exception {
    Circuit circuit = file.getCircuit("CPU_Core");
    if (circuit == null) throw new IllegalArgumentException("CPU_Core circuit not found");
    List<MemContents> roms = new ArrayList<>();
    for (Circuit nested : file.getCircuits()) {
      for (Component component : nested.getNonWires()) {
        String label = safeLabel(component.getAttributeSet());
        if (component.getFactory() instanceof Rom && (label.equals("IMEM0") || label.equals("IMEM1"))) {
          roms.add(Rom.getMemContents(Instance.getInstanceFor(component)));
        }
      }
    }
    if (roms.size() != 2) throw new IllegalArgumentException("expected two IMEM ROMs, found " + roms.size());
    Set<WidthIncompatibilityData> errors = circuit.getWidthIncompatibilityData();
    if (errors != null && !errors.isEmpty()) throw new IllegalArgumentException("CPU_Core width errors: " + errors.size());
    BufferedReader input = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
    int failures = 0;
    int cases = 0;
    String line;
    while ((line = input.readLine()) != null) {
      if (line.isBlank()) continue;
      String[] columns = line.split("\\t", -1);
      if (columns.length != 6 || !columns[0].equals("CASE")) {
        throw new IllegalArgumentException("invalid suite line: " + line);
      }
      String name = columns[1];
      int limit = nonNegativeInt(columns[2], "suite max cycles");
      String[] hexWords = columns[3].split(",");
      if (hexWords.length > 65536) throw new IllegalArgumentException("program too large: " + name);
      for (MemContents rom : roms) {
        rom.clear();
        for (int i = 0; i < hexWords.length; i++) rom.set(i, Integer.parseInt(hexWords[i], 16));
      }
      failures += runSuiteCase(project, circuit, name, limit, columns[4], columns[5]);
      cases++;
    }
    System.out.printf("SUITE cases=%d failures=%d%n", cases, failures);
    return failures;
  }

  private static int runSuiteCase(Project project, Circuit circuit, String name, int limit,
      String controlText, String probeText) throws Exception {
    CircuitState state = CircuitState.createRootState(project, circuit, Thread.currentThread());
    state.getPropagator().propagate();
    List<PinRef> pins = pins(circuit);
    Map<String, PinRef> outputs = new LinkedHashMap<>();
    for (PinRef pin : pins) if (!pin.input && !pin.label.isEmpty()) outputs.put(pin.label, pin);
    PinRef clock = uniqueInput(pins, "clk", circuit.getName());
    PinRef reset = uniqueInput(pins, "rst", circuit.getName());
    PinRef run = uniqueInput(pins, "run", circuit.getName());
    PinRef debugAddress = uniqueInput(pins, "debug_addr", circuit.getName());
    int[] probes = probeText.isEmpty() ? new int[0] :
        java.util.Arrays.stream(probeText.split(",")).mapToInt(Integer::parseInt).toArray();
    Map<Integer, int[]> controls = new LinkedHashMap<>();
    if (!controlText.isEmpty()) {
      for (String encoded : controlText.split(";")) {
        String[] values = encoded.split(":", -1);
        if (values.length != 3) throw new IllegalArgumentException("invalid suite control: " + encoded);
        controls.put(Integer.parseInt(values[0]), new int[] {Integer.parseInt(values[1]),
            Integer.parseInt(values[2])});
      }
    }
    drive(state, clock, Value.FALSE);
    drive(state, reset, Value.TRUE);
    drive(state, run, Value.FALSE);
    drive(state, debugAddress, Value.createKnown(16, 0));
    state.getPropagator().propagate();
    clockCycle(state, clock);
    drive(state, reset, Value.FALSE);
    drive(state, run, Value.TRUE);
    state.getPropagator().propagate();
    int failures = 0;
    int retired = 0;
    int[] maxima = new int[8];
    String[] queues = {"rs0_count", "rob0_count", "rs1_count", "rob1_count",
        "rs2_count", "rob2_count", "sb_count", "fifo_count"};
    int[] capacity = {16,16,16,16,16,16,8,8};
    int currentRun = 1, currentReset = 0, cycles = 0;
    System.out.printf("CASE_BEGIN %s%n", name);
    while (cycles < limit && !Value.TRUE.equals(read(state, outputs.get("halted")))) {
      int[] control = controls.get(cycles);
      if (control != null) {
        if (control[0] >= 0) currentRun = control[0];
        if (control[1] >= 0) currentReset = control[1];
        drive(state, run, currentRun == 1 ? Value.TRUE : Value.FALSE);
        drive(state, reset, currentReset == 1 ? Value.TRUE : Value.FALSE);
        state.getPropagator().propagate();
      }
      if (currentReset == 0 && currentRun == 1 && bit(state, outputs, "commit_valid") == 1) {
        System.out.printf("RETIRE cycle=%d pc=%d op=%d result=%d sn=%d%n", cycles,
            bit(state, outputs, "commit_pc"), bit(state, outputs, "commit_op"),
            bit(state, outputs, "commit_result"), bit(state, outputs, "commit_sn"));
        retired++;
      }
      long[] paused = currentRun == 0 && currentReset == 0 ? architecturalSnapshot(state, outputs) : null;
      clockCycle(state, clock);
      if (currentReset == 1) {
        System.out.printf("RESET cycle=%d%n", cycles);
        retired = 0;
      }
      if (paused != null && !java.util.Arrays.equals(paused, architecturalSnapshot(state, outputs))) {
        System.out.printf("CONTROL_FAIL cycle=%d pause changed architectural state%n", cycles);
        failures++;
      }
      for (int i = 0; i < queues.length; i++) {
        int count = bit(state, outputs, queues[i]);
        maxima[i] = Math.max(maxima[i], count);
        if (count > capacity[i]) {
          System.out.printf("CAPACITY_FAIL cycle=%d queue=%s count=%d limit=%d%n",
              cycles, queues[i], count, capacity[i]);
          failures++;
        }
      }
      cycles++;
      if (state.getPropagator().isOscillating()) {
        System.out.printf("OSCILLATION cycle=%d%n", cycles);
        failures++;
        break;
      }
    }
    String status = Value.TRUE.equals(read(state, outputs.get("halted"))) ? "HALTED" : "TIMEOUT";
    if (!status.equals("HALTED")) failures++;
    StringBuilder summary = new StringBuilder("CASE_END " + name + " status=" + status
        + " cycles=" + cycles + " retired=" + retired);
    for (int i = 0; i < 8; i++) summary.append(" r").append(i).append('=')
        .append(bit(state, outputs, "r" + i));
    for (int i = 0; i < queues.length; i++) summary.append(" max_").append(queues[i])
        .append('=').append(maxima[i]);
    summary.append(" dual_issue=").append(bit(state, outputs, "dual_issue"));
    summary.append(" dual_cdb=").append(bit(state, outputs, "dual_cdb"));
    summary.append(" recoveries=").append(bit(state, outputs, "recoveries"));
    for (int address : probes) {
      drive(state, debugAddress, Value.createKnown(16, address));
      state.getPropagator().propagate();
      summary.append(" mem_").append(address).append('=')
          .append(bit(state, outputs, "memory_debug"));
    }
    summary.append(" failures=").append(failures);
    System.out.println(summary);
    return failures;
  }

  private static long[] architecturalSnapshot(CircuitState state, Map<String, PinRef> outputs) {
    long[] values = new long[11];
    values[0] = bit(state, outputs, "pc");
    values[1] = bit(state, outputs, "cycles");
    values[2] = bit(state, outputs, "committed");
    for (int i = 0; i < 8; i++) values[i + 3] = bit(state, outputs, "r" + i);
    return values;
  }

  private static int bit(CircuitState state, Map<String, PinRef> outputs, String label) {
    PinRef pin = outputs.get(label);
    if (pin == null) throw new IllegalArgumentException("CPU output not found: " + label);
    Value value = read(state, pin);
    if (!value.isFullyDefined()) throw new IllegalStateException("undefined CPU output " + label
        + "=" + formatValue(value));
    return (int) value.toLongValue();
  }

  private static int runCpu(CircuitState state, List<PinRef> pins, int maxCycles) throws Exception {
    String circuitName = state.getCircuit().getName();
    PinRef clock = uniqueInput(pins, "clk", circuitName);
    PinRef reset = uniqueInput(pins, "rst", circuitName);
    PinRef run = uniqueInput(pins, "run", circuitName);
    PinRef halted = uniquePin(pins, "halted", false, circuitName);
    drive(state, clock, Value.FALSE);
    drive(state, reset, Value.TRUE);
    drive(state, run, Value.FALSE);
    state.getPropagator().propagate();
    clockCycle(state, clock);
    drive(state, reset, Value.FALSE);
    drive(state, run, Value.TRUE);
    state.getPropagator().propagate();
    trace(state, pins, "RESET cycle=0");
    int cycles = 0;
    while (!Value.TRUE.equals(read(state, halted)) && cycles < maxCycles) {
      trace(state, pins, "EDGE cycle=" + (cycles + 1));
      clockCycle(state, clock);
      cycles++;
      if (state.getPropagator().isOscillating()) {
        System.out.printf("CPU_RESULT status=OSCILLATING cycles=%d%n", cycles);
        return 1;
      }
    }
    boolean finished = Value.TRUE.equals(read(state, halted));
    trace(state, pins, "FINAL cycle=" + cycles);
    System.out.printf("CPU_RESULT status=%s cycles=%d%n", finished ? "HALTED" : "TIMEOUT", cycles);
    return finished ? 0 : 1;
  }

  private static void clockCycle(CircuitState state, PinRef clock) {
    drive(state, clock, Value.FALSE);
    state.getPropagator().propagate();
    drive(state, clock, Value.TRUE);
    state.getPropagator().propagate();
    drive(state, clock, Value.FALSE);
    state.getPropagator().propagate();
  }

  private static int runScript(CircuitState state, List<PinRef> pins, File file) throws Exception {
    int failures = 0;
    int lineNumber = 0;
    for (String source : Files.readAllLines(file.toPath())) {
      lineNumber++;
      String line = source.split("#", 2)[0].trim();
      if (line.isEmpty()) continue;
      String[] words = line.split("\\s+");
      switch (words[0]) {
        case "set" -> {
          for (int i = 1; i < words.length; i++) {
            String[] pair = words[i].split("=", 2);
            if (pair.length != 2) throw new IllegalArgumentException("invalid script assignment: " + words[i]);
            PinRef pin = uniqueInput(pins, pair[0], state.getCircuit().getName());
            drive(state, pin, parseValue(pin.width(), pair[1]));
          }
          state.getPropagator().propagate();
        }
        case "cycle" -> {
          boolean internalClock = words.length > 1 && words[1].equals("@internal");
          PinRef clock = internalClock ? null
              : uniqueInput(pins, words.length > 1 ? words[1] : "clk", state.getCircuit().getName());
          int count = words.length > 2 ? nonNegativeInt(words[2], "script cycle") : 1;
          for (int i = 0; i < count; i++) {
            if (internalClock) {
              state.getPropagator().toggleClocks();
              state.getPropagator().propagate();
              state.getPropagator().toggleClocks();
              state.getPropagator().propagate();
            } else {
              clockCycle(state, clock);
            }
          }
        }
        case "expect" -> {
          for (int i = 1; i < words.length; i++) {
            String[] pair = words[i].split("=", 2);
            if (pair.length != 2) throw new IllegalArgumentException("invalid script assertion: " + words[i]);
            PinRef pin = uniquePin(pins, pair[0], false, state.getCircuit().getName());
            Value expected = parseValue(pin.width(), pair[1]);
            Value actual = read(state, pin);
            if (!expected.equals(actual)) {
              failures++;
              System.out.printf("ASSERT_FAIL line=%d pin=%s expected=%s actual=%s%n", lineNumber,
                  pin.label, formatValue(expected), formatValue(actual));
            }
          }
        }
        case "trace" -> trace(state, pins, "TRACE line=" + lineNumber);
        default -> throw new IllegalArgumentException("unknown script command at line " + lineNumber + ": " + words[0]);
      }
    }
    System.out.printf("SCRIPT_RESULT file=%s failures=%d%n", file, failures);
    return failures;
  }

  private static void trace(CircuitState state, List<PinRef> pins, String prefix) {
    StringBuilder line = new StringBuilder(prefix);
    for (PinRef pin : pins) {
      if (pin.input) continue;
      Value value = read(state, pin);
      line.append(' ').append(pin.label).append("=0x").append(value.toHexString());
    }
    System.out.println(line);
  }

  private static Value read(CircuitState state, PinRef pin) {
    return pin.factory.getValue(state.getInstanceState(pin.instance));
  }

  private static List<PinRef> pins(Circuit circuit) {
    List<PinRef> result = new ArrayList<>();
    for (Component component : sorted(circuit.getNonWires())) {
      if (component.getFactory() instanceof Pin) result.add(new PinRef(component));
    }
    return result;
  }

  private static PinRef uniqueInput(List<PinRef> pins, String label, String circuitName) {
    return uniquePin(pins, label, true, circuitName);
  }

  private static PinRef uniquePin(List<PinRef> pins, String label, boolean input, String circuitName) {
    PinRef found = null;
    for (PinRef pin : pins) {
      if (pin.input == input && pin.label.equals(label)) {
        if (found != null) {
          throw new IllegalArgumentException("duplicate " + (input ? "input" : "output")
              + " pin label in " + circuitName + ": " + label);
        }
        found = pin;
      }
    }
    if (found == null) {
      throw new IllegalArgumentException((input ? "input" : "output")
          + " pin not found in " + circuitName + ": " + label);
    }
    return found;
  }

  private static void drive(CircuitState state, PinRef pin, Value value) {
    pin.factory.driveInputPin(state.getInstanceState(pin.instance), value);
    state.markComponentAsDirty(pin.component);
  }

  private static Value parseValue(BitWidth width, String original) throws Exception {
    String text = original.replace("_", "").trim();
    if (text.equalsIgnoreCase("x") || text.equalsIgnoreCase("unknown")) {
      return Value.createUnknown(width);
    }
    if (text.equalsIgnoreCase("e") || text.equalsIgnoreCase("error")) {
      return Value.createError(width);
    }
    int radix = 10;
    boolean negative = text.startsWith("-");
    int prefix = negative ? 1 : 0;
    if (text.regionMatches(true, prefix, "0x", 0, 2)) {
      radix = 16;
      text = text.substring(0, prefix) + text.substring(prefix + 2);
    } else if (text.regionMatches(true, prefix, "0b", 0, 2)) {
      radix = 2;
      text = text.substring(0, prefix) + text.substring(prefix + 2);
    } else if (text.regionMatches(true, prefix, "0o", 0, 2)) {
      radix = 8;
      text = text.substring(0, prefix) + text.substring(prefix + 2);
    } else if (text.matches("(?i)[01x]+") && text.toLowerCase(Locale.ROOT).contains("x")) {
      return Value.fromLogString(width, text);
    }
    BigInteger parsed = new BigInteger(text, radix);
    BigInteger modulus = BigInteger.ONE.shiftLeft(width.getWidth());
    if (parsed.signum() < 0) parsed = parsed.mod(modulus);
    if (parsed.bitLength() > width.getWidth()) {
      throw new IllegalArgumentException("value " + original + " does not fit " + width.getWidth() + " bits");
    }
    return Value.createKnown(width, parsed.longValue());
  }

  private static void printComponents(Circuit circuit, CircuitState state) {
    System.out.println("COMPONENTS");
    for (Component component : sorted(circuit.getNonWires())) {
      System.out.printf("  %s loc=%s bounds=%s", component.getFactory().getName(),
          component.getLocation(), component.getBounds());
      String label = safeLabel(component.getAttributeSet());
      if (!label.isEmpty()) System.out.print(" label=" + quoted(label));
      System.out.println();
      List<EndData> ends = component.getEnds();
      for (int i = 0; i < ends.size(); i++) {
        EndData end = ends.get(i);
        Value value = state.getValue(end.getLocation());
        System.out.printf("    port=%d loc=%s width=%d dir=%s value=%s%n", i,
            end.getLocation(), end.getWidth().getWidth(), endDirection(end), formatValue(value));
      }
    }
  }

  private static int printNets(Circuit circuit, CircuitState state) {
    System.out.println("NETS");
    int failures = 0;
    List<Location> locations = new ArrayList<>(circuit.getAllLocations());
    locations.sort(Comparator.naturalOrder());
    for (Location location : locations) {
      BitWidth width = circuit.getWidth(location);
      Value value = state.getValue(location);
      boolean error = hasError(value);
      if (error) failures++;
      System.out.printf("  loc=%-12s width=%-2d value=%s%s%n", location,
          width.getWidth(), formatValue(value), error ? " ERROR" : "");
    }
    return failures;
  }

  private static boolean hasError(Value value) {
    return value != null && value.toBinaryString().toLowerCase(Locale.ROOT).contains("e");
  }

  private static String formatValue(Value value) {
    if (value == null || value == Value.NIL) return "nil";
    String binary = value.toBinaryString();
    String hex = value.toHexString();
    return value.getWidth() <= 4 ? binary : "0x" + hex + " (" + binary + ")";
  }

  private static String safeLabel(AttributeSet attrs) {
    if (!attrs.containsAttribute(StdAttr.LABEL)) return "";
    String label = attrs.getValue(StdAttr.LABEL);
    return label == null ? "" : label;
  }

  private static String quoted(String value) {
    return '"' + value.replace("\\", "\\\\").replace("\"", "\\\"") + '"';
  }

  private static List<Component> sorted(Collection<? extends Component> components) {
    List<Component> result = new ArrayList<>(components);
    result.sort(Comparator
        .comparingInt((Component c) -> c.getLocation().getY())
        .thenComparingInt(c -> c.getLocation().getX())
        .thenComparing(c -> c.getFactory().getName()));
    return result;
  }

  private static String endDirection(EndData end) {
    if (end.isInput() && end.isOutput()) return "inout";
    if (end.isInput()) return "in";
    if (end.isOutput()) return "out";
    return "none";
  }

  private static void printCatalog() {
    System.out.println("LOGISIM_EVOLUTION_5_COMPONENT_CATALOG");
    for (var library : new com.cburch.logisim.std.Builtin().getLibraries()) {
      System.out.println("LIBRARY name=" + library.getName() + " display=" + library.getDisplayName());
    }
    catalog("Pin/input", Pin.FACTORY, Map.of("type", "input", "facing", "east", "width", "16"));
    catalog("Pin/output", Pin.FACTORY, Map.of("type", "output", "facing", "west", "width", "16"));
    catalog("Tunnel", Tunnel.FACTORY, Map.of("facing", "east", "width", "16", "label", "BUS"));
    catalog("Constant", Constant.FACTORY, Map.of("facing", "east", "width", "16", "value", "0x1"));
    catalog("Register", new Register(), Map.of("facing", "east", "width", "16"));
    catalog("Register/classic", new Register(), Map.of("appearance", "classic", "width", "16"));
    catalog("Adder", new Adder(), Map.of("facing", "east", "width", "16"));
    catalog("Comparator", new com.cburch.logisim.std.arith.Comparator(), Map.of("facing", "east", "width", "16", "mode", "unsigned"));
    catalog("Multiplexer/2", new Multiplexer(), Map.of("facing", "east", "width", "16", "select", "1"));
    catalog("Multiplexer/4", new Multiplexer(), Map.of("facing", "east", "width", "16", "select", "2"));
    catalog("Multiplexer/2/size30", new Multiplexer(), Map.of("facing", "east", "width", "16", "select", "1", "size", "30"));
    catalog("Splitter", SplitterFactory.instance,
        Map.of("facing", "east", "fanout", "2", "incoming", "16", "appear", "left"));
    catalog("Splitter/slice", SplitterFactory.instance,
        Map.of("facing", "east", "fanout", "1", "incoming", "16", "appear", "right", "spacing", "1"));
    catalog("Splitter/pack", SplitterFactory.instance,
        Map.of("facing", "west", "fanout", "3", "incoming", "16", "appear", "left", "spacing", "1"));
    catalog("RAM", new Ram(), Map.of("addrWidth", "8", "dataWidth", "16"));
    catalog("ROM", new Rom(), Map.of("addrWidth", "8", "dataWidth", "16"));
    catalog("RAM/classic/separate", new Ram(), Map.of("addrWidth", "16", "dataWidth", "16", "appearance", "classic", "databus", "bibus", "asyncread", "true", "clearpin", "true"));
    catalog("ROM/classic", new Rom(), Map.of("addrWidth", "8", "dataWidth", "16", "appearance", "classic"));
  }

  private static void catalog(String title, ComponentFactory factory, Map<String, String> changes) {
    AttributeSet attrs = factory.createAttributeSet();
    List<String> warnings = new ArrayList<>();
    List<Map.Entry<String, String>> ordered = new ArrayList<>(changes.entrySet());
    ordered.sort(Comparator.comparingInt(e -> switch (e.getKey()) {
      case "width", "incoming", "dataWidth", "addrWidth", "fanout" -> 0;
      default -> 1;
    }));
    for (Map.Entry<String, String> change : ordered) {
      Attribute<?> attr = attrs.getAttribute(change.getKey());
      if (attr == null) {
        warnings.add("no attribute " + change.getKey());
      } else {
        try {
          setParsed(attrs, attr, change.getValue());
        } catch (RuntimeException ex) {
          warnings.add(change.getKey() + "=" + change.getValue() + ": " + ex.getMessage());
        }
      }
    }
    Component component = factory.createComponent(Location.create(100, 100, true), attrs);
    System.out.printf("COMPONENT %s factory=%s bounds=%s%n", title, factory.getName(), component.getBounds());
    for (Attribute<?> attr : attrs.getAttributes()) {
      Object value = attrs.getValue(attr);
      System.out.printf("  ATTR name=%s value=%s save=%s%n", attr.getName(),
          standard(attr, value), attrs.isToSave(attr));
    }
    List<EndData> ends = component.getEnds();
    for (int i = 0; i < ends.size(); i++) {
      EndData end = ends.get(i);
      int dx = end.getLocation().getX() - component.getLocation().getX();
      int dy = end.getLocation().getY() - component.getLocation().getY();
      System.out.printf("  PORT index=%d rel=(%d,%d) abs=%s width=%d dir=%s exclusive=%s%n",
          i, dx, dy, end.getLocation(), end.getWidth().getWidth(), endDirection(end), end.isExclusive());
    }
    for (String warning : warnings) System.out.println("  WARNING " + warning);
    if (factory instanceof Ram) {
      System.out.printf("  RAM_INDICES address=%d data_in=%d data_out=%d oe=%d we=%d clock=%d clear=%d%n",
          RamAppearance.getAddrIndex(0, attrs), RamAppearance.getDataInIndex(0, attrs),
          RamAppearance.getDataOutIndex(0, attrs), RamAppearance.getOEIndex(0, attrs),
          RamAppearance.getWEIndex(0, attrs), RamAppearance.getClkIndex(0, attrs),
          RamAppearance.getClrIndex(0, attrs));
    }
  }

  @SuppressWarnings({"rawtypes", "unchecked"})
  private static void setParsed(AttributeSet attrs, Attribute attr, String text) {
    attrs.setValue(attr, attr.parse(text));
  }

  @SuppressWarnings({"rawtypes", "unchecked"})
  private static String standard(Attribute attr, Object value) {
    try {
      return attr.toStandardString(value);
    } catch (RuntimeException ex) {
      return String.valueOf(value);
    }
  }

  private static void usage() {
    System.err.println("Usage: java -cp <logisim.jar>;. tools.LogisimHarness FILE.circ [options]");
    System.err.println("  --circuit NAME       simulate one circuit (default: project main)");
    System.err.println("  --all-circuits       load and inspect every circuit independently");
    System.err.println("  --set LABEL=VALUE    drive an input Pin (repeatable; 0x/0b/0o/decimal/x/e)");
    System.err.println("  --clock LABEL        use a one-bit input Pin as a manual clock");
    System.err.println("  --cycles N           run N complete clock cycles");
    System.err.println("  --ticks N            run N half cycles after --cycles");
    System.err.println("  --script FILE        execute set/cycle/expect/trace commands after simulation");
    System.err.println("                        scripts may use 'cycle @internal N' for Clock components");
    System.err.println("  --cpu PROGRAM_HEX    load IMEM0/IMEM1, reset CPU_Core, trace until HALT");
    System.err.println("  --max-cycles N       CPU timeout (default 1000)");
    System.err.println("  --suite              batch CPU cases from stdin (used by tests/run_cpu.py)");
    System.err.println("  --components         dump every component port and value");
    System.err.println("  --nets               dump every known circuit location and width");
    System.err.println("  --strict             exit 1 for width errors, oscillation, or E values");
    System.err.println("  --catalog            print native component attributes and port geometry");
  }
}
