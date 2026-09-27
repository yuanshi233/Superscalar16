package tools;

import com.cburch.logisim.circuit.Circuit;
import com.cburch.logisim.circuit.CircuitState;
import com.cburch.logisim.comp.Component;
import com.cburch.logisim.comp.ComponentDrawContext;
import com.cburch.logisim.data.Attribute;
import com.cburch.logisim.data.AttributeSet;
import com.cburch.logisim.data.Bounds;
import com.cburch.logisim.file.Loader;
import com.cburch.logisim.file.LogisimFile;
import com.cburch.logisim.instance.StdAttr;
import com.cburch.logisim.proj.Project;

import java.awt.Color;
import java.awt.Font;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.io.File;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import javax.imageio.ImageIO;
import javax.swing.JPanel;

/** Read-only native rendering and component-bound overlap audit for Logisim-evolution 5.0.0. */
public final class LayoutAudit {
  private static final long MAX_PIXELS = 24_000_000L;
  private static final int MARGIN = 24;

  private LayoutAudit() {}

  private static final class Options {
    File source;
    String circuit;
    File output;
    Bounds crop;
    double scale = 1.0;
    int maxSize = 4096;
    int pairLimit = 30;
    boolean allCircuits;
    boolean printBounds;
    boolean markOverlaps;
  }

  private record Item(int id, Component component, Bounds body, Bounds graphic) {}
  private record Pair(Item first, Item second, Bounds intersection, boolean bodyOverlap) {}
  private record Audit(Circuit circuit, CircuitState state, List<Item> items, Bounds bounds,
      List<Pair> examples, Set<Integer> overlapping, long count, long bodyCount) {}

  public static void main(String[] args) {
    int status = 0;
    Project project = null;
    try {
      System.setProperty("java.awt.headless", "true");
      com.cburch.logisim.Main.headless = true;
      Locale.setDefault(Locale.ROOT);
      Options options = parse(args);
      Loader loader = new Loader(null) {
        @Override public void showError(String message) {
          throw new IllegalArgumentException("Logisim loader: " + message);
        }
        @Override public int showOptions(String message, String title, String[] values, int choice) {
          throw new IllegalArgumentException("Logisim loader prompt: " + title + ": " + message);
        }
      };
      LogisimFile file = loader.openLogisimFile(options.source);
      Circuit selected = file.getCircuit(options.circuit);
      if (selected == null) throw new IllegalArgumentException("circuit not found: " + options.circuit);
      project = new Project(file);
      project.getSimulator().setAutoTicking(false);
      project.getSimulator().setAutoPropagation(false);
      System.out.println("NOTE native bounds are measured after drawing at 1:1; labels/tunnel text are"
          + " included where the component exposes them. Intersections are bounding-box candidates,"
          + " not exact-pixel collisions. Wires and touching edges are excluded.");
      List<Circuit> circuits = options.allCircuits ? file.getCircuits() : List.of(selected);
      long total = 0;
      long bodyTotal = 0;
      for (Circuit circuit : circuits) {
        Audit audit = inspect(project, circuit, options);
        report(audit, options);
        total += audit.count();
        bodyTotal += audit.bodyCount();
        if (circuit == selected) render(audit, options);
      }
      System.out.printf("TOTAL circuits=%d overlaps=%d body_overlaps=%d%n",
          circuits.size(), total, bodyTotal);
    } catch (Throwable error) {
      error.printStackTrace();
      status = 2;
    } finally {
      if (project != null) project.getSimulator().shutDown();
    }
    // Logisim's autosave worker is non-daemon even for a headless, read-only project.
    System.exit(status);
  }

  private static Options parse(String[] args) {
    if (args.length < 3) {
      throw new IllegalArgumentException("Usage: java -cp <logisim.jar>;. tools.LayoutAudit"
          + " FILE.circ CIRCUIT OUTPUT.png [--crop X Y W H] [--scale FACTOR]"
          + " [--max-size PIXELS] [--pairs N] [--all-circuits] [--bounds] [--mark-overlaps]");
    }
    Options options = new Options();
    options.source = new File(args[0]).getAbsoluteFile();
    options.circuit = args[1];
    options.output = new File(args[2]).getAbsoluteFile();
    if (!options.source.isFile()) throw new IllegalArgumentException("file not found: " + options.source);
    if (options.output.equals(options.source)) throw new IllegalArgumentException("output cannot be source");
    for (int i = 3; i < args.length; i++) {
      switch (args[i]) {
        case "--crop" -> {
          int x = Integer.parseInt(args[++i]);
          int y = Integer.parseInt(args[++i]);
          int width = positive(args[++i], "crop width");
          int height = positive(args[++i], "crop height");
          options.crop = Bounds.create(x, y, width, height);
        }
        case "--scale" -> {
          options.scale = Double.parseDouble(args[++i]);
          if (!Double.isFinite(options.scale) || options.scale <= 0) {
            throw new IllegalArgumentException("scale must be finite and positive");
          }
        }
        case "--max-size" -> options.maxSize = positive(args[++i], "max size");
        case "--pairs" -> {
          options.pairLimit = Integer.parseInt(args[++i]);
          if (options.pairLimit < 0) throw new IllegalArgumentException("pairs cannot be negative");
        }
        case "--all-circuits" -> options.allCircuits = true;
        case "--bounds" -> options.printBounds = true;
        case "--mark-overlaps" -> options.markOverlaps = true;
        default -> throw new IllegalArgumentException("unknown option: " + args[i]);
      }
    }
    return options;
  }

  private static int positive(String value, String label) {
    int result = Integer.parseInt(value);
    if (result <= 0) throw new IllegalArgumentException(label + " must be positive");
    return result;
  }

  private static Graphics2D graphics(BufferedImage image) {
    Graphics2D graphics = image.createGraphics();
    graphics.setColor(Color.BLACK);
    graphics.setFont(new Font(Font.SANS_SERIF, Font.PLAIN, 12));
    graphics.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
    graphics.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING, RenderingHints.VALUE_TEXT_ANTIALIAS_ON);
    return graphics;
  }

  private static ComponentDrawContext context(Circuit circuit, CircuitState state, Graphics2D graphics) {
    ComponentDrawContext context = new ComponentDrawContext(new JPanel(), circuit, state,
        graphics, graphics, true);
    context.setShowState(false);
    context.setShowColor(false);
    return context;
  }

  private static Audit inspect(Project project, Circuit circuit, Options options) {
    CircuitState state = CircuitState.createRootState(project, circuit, Thread.currentThread());
    BufferedImage scratch = new BufferedImage(8, 8, BufferedImage.TYPE_INT_RGB);
    Graphics2D graphics = graphics(scratch);
    List<Item> items = new ArrayList<>();
    Bounds bounds;
    try {
      // A native draw initializes text metrics and tunnel shapes even outside the scratch clip.
      circuit.draw(context(circuit, state, graphics), null);
      List<Component> components = new ArrayList<>(circuit.getNonWires());
      components.sort(Comparator.comparingInt((Component c) -> c.getLocation().getY())
          .thenComparingInt(c -> c.getLocation().getX())
          .thenComparing(c -> c.getFactory().getName()));
      int id = 0;
      for (Component component : components) {
        items.add(new Item(id++, component, component.getBounds(), component.getBounds(graphics)));
      }
      bounds = circuit.getBounds(graphics);
    } finally {
      graphics.dispose();
    }
    List<Item> ordered = new ArrayList<>(items);
    ordered.sort(Comparator.comparingInt((Item item) -> item.graphic().getX())
        .thenComparingInt(Item::id));
    List<Pair> examples = new ArrayList<>();
    Set<Integer> overlapping = new HashSet<>();
    long count = 0;
    long bodyCount = 0;
    for (int i = 0; i < ordered.size(); i++) {
      Item first = ordered.get(i);
      long right = (long) first.graphic().getX() + first.graphic().getWidth();
      for (int j = i + 1; j < ordered.size(); j++) {
        Item second = ordered.get(j);
        if (second.graphic().getX() >= right) break;
        Bounds intersection = intersection(first.graphic(), second.graphic());
        if (intersection == null) continue;
        boolean bodyOverlap = intersection(first.body(), second.body()) != null;
        count++;
        if (bodyOverlap) bodyCount++;
        overlapping.add(first.id());
        overlapping.add(second.id());
        if (examples.size() < options.pairLimit) {
          examples.add(new Pair(first, second, intersection, bodyOverlap));
        }
      }
    }
    return new Audit(circuit, state, items, bounds, examples, overlapping, count, bodyCount);
  }

  private static Bounds intersection(Bounds first, Bounds second) {
    int left = Math.max(first.getX(), second.getX());
    int top = Math.max(first.getY(), second.getY());
    int right = Math.min(first.getX() + first.getWidth(), second.getX() + second.getWidth());
    int bottom = Math.min(first.getY() + first.getHeight(), second.getY() + second.getHeight());
    return left < right && top < bottom ? Bounds.create(left, top, right - left, bottom - top) : null;
  }

  private static void report(Audit audit, Options options) {
    long changed = audit.items().stream().filter(item -> !item.body().equals(item.graphic())).count();
    System.out.printf("AUDIT circuit=%s components=%d wires=%d bounds=%s"
            + " overlaps=%d body_overlaps=%d label_extended=%d involved=%d%n",
        quote(audit.circuit().getName()), audit.items().size(), audit.circuit().getWires().size(),
        audit.bounds(), audit.count(), audit.bodyCount(), changed, audit.overlapping().size());
    for (Pair pair : audit.examples()) {
      System.out.printf("  OVERLAP kind=%s intersection=%s a={%s} b={%s}%n",
          pair.bodyOverlap() ? "body" : "label-envelope", pair.intersection(),
          describe(pair.first()), describe(pair.second()));
    }
    if (audit.count() > audit.examples().size()) {
      System.out.printf("  OMITTED pairs=%d (increase --pairs)%n", audit.count() - audit.examples().size());
    }
    if (options.printBounds) {
      for (Item item : audit.items()) System.out.println("  BOUNDS " + describe(item));
    }
  }

  private static String describe(Item item) {
    Component component = item.component();
    AttributeSet attrs = component.getAttributeSet();
    String label = attrs.containsAttribute(StdAttr.LABEL) ? attrs.getValue(StdAttr.LABEL) : "";
    Attribute<?> text = attrs.getAttribute("text");
    if ((label == null || label.isEmpty()) && text != null) label = String.valueOf(attrs.getValue(text));
    return "id=" + item.id() + " type=" + quote(component.getFactory().getName())
        + " label=" + quote(label == null ? "" : label) + " loc=" + component.getLocation()
        + " body=" + item.body() + " graphic=" + item.graphic();
  }

  private static String quote(String text) {
    return '"' + text.replace("\\", "\\\\").replace("\"", "\\\"")
        .replace("\r", "\\r").replace("\n", "\\n") + '"';
  }

  private static void render(Audit audit, Options options) throws Exception {
    Bounds viewport = options.crop == null ? audit.bounds().expand(MARGIN) : options.crop;
    int logicalWidth = Math.max(1, viewport.getWidth());
    int logicalHeight = Math.max(1, viewport.getHeight());
    double scale = Math.min(options.scale,
        (double) options.maxSize / Math.max(logicalWidth, logicalHeight));
    scale = Math.min(scale, Math.sqrt((double) MAX_PIXELS / logicalWidth / logicalHeight));
    int width = Math.max(1, (int) Math.ceil(logicalWidth * scale));
    int height = Math.max(1, (int) Math.ceil(logicalHeight * scale));
    BufferedImage image = new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB);
    Graphics2D graphics = graphics(image);
    try {
      graphics.setColor(Color.WHITE);
      graphics.fillRect(0, 0, width, height);
      graphics.scale(scale, scale);
      graphics.translate(-viewport.getX(), -viewport.getY());
      graphics.setColor(Color.BLACK);
      audit.circuit().draw(context(audit.circuit(), audit.state(), graphics), null);
      if (options.markOverlaps) {
        graphics.setColor(new Color(220, 20, 30, 65));
        for (Item item : audit.items()) {
          if (audit.overlapping().contains(item.id())) {
            Bounds bounds = item.graphic();
            graphics.fillRect(bounds.getX(), bounds.getY(), bounds.getWidth(), bounds.getHeight());
          }
        }
      }
    } finally {
      graphics.dispose();
    }
    File parent = options.output.getParentFile();
    if (parent != null && !parent.isDirectory() && !parent.mkdirs()) {
      throw new IllegalArgumentException("cannot create output directory: " + parent);
    }
    if (!ImageIO.write(image, "PNG", options.output)) throw new IllegalStateException("PNG writer unavailable");
    System.out.printf("PNG file=%s width=%d height=%d scale=%.6f viewport=%s%n",
        quote(options.output.toString()), width, height, scale, viewport);
  }
}
