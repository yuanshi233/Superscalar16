# 16 位乱序超标量处理器（Logisim Evolution）

正式工程是 [`Superscalar16_impl.circ`](Superscalar16_impl.circ)，可用 Logisim Evolution 5.0.0 打开。`Superscalar16.circ` 是早期仅含接口的骨架，保留作参考，不是可执行的处理器。设计规格见 [`doc/超标量处理器设计文档.md`](doc/超标量处理器设计文档.md)。

## 打开与运行

1. 在 Logisim Evolution 中打开 `Superscalar16_impl.circ`，主电路 `Superscalar16` 是带演示程序和状态输出的面板；`CPU_Core` 是可接入外部时钟、复位、运行开关和调试地址的处理器电路。
2. 在主电路将 `reset` 置 1，至少运行一个时钟上升沿，再置 0。`pause=0` 运行，`pause=1` 暂停。使用 `Simulate > Ticks Enabled` 和时钟频率菜单连续运行，或手动单步时钟。
3. 默认 ROM 程序执行 `ADDI`、`ADD`、`SW`、`LW`、`HALT`，完成后 `halted=1`，`r1=5`、`r2=8`、`r3=r4=13`、`memory_debug=13`。主电路的内存调试地址固定为 0；在 `CPU_Core` 上可通过 `debug_addr` 查看任意字地址。

PC 和内存地址都是 16 位**字地址**，因此连续指令位于 `pc`、`pc+1`；按 16 位自然回绕。`r0/p0` 恒为 0。复位会清空微结构状态；未嵌入数据镜像时也会清空数据 RAM，嵌入 `.data`/`--data` 初值时则恢复并保留这些初值。ROM 内容不变。停机由 `HALT` 顺序提交触发；此后需复位才能重新运行。

## 实现结构

工程内有 17 个原生 Logisim 子电路：双取指和双端口 64K×16 ROM、64 项直接映射 BTB、8 组取指 FIFO、译码、RAT/64×16 PRF 与空闲表、两个 ALU 和一个访存执行通路、三个各 16 项的 RS/分布式 ROB、双 CDB、8 项 Store Buffer、64K×16 数据 RAM，以及全局顺序提交和错误预测恢复。指令可乱序执行，但只按连续的全局序列号提交。Load 扫描更早的暂存 Store 进行转发；Store 仅在提交时写入 RAM。

设计文档部分伪代码和字段总宽度互相矛盾。此实现以指令编码与架构行为为准：连续 PC 是 `pc+1`；分支比较预测和实际的**下一 PC**（方向及目标都能触发恢复）；提交维护独立的 committed RAT，以便错误路径冲刷后恢复映射和空闲表。单个 RS/ROB bank 每周期只有一个插入端口，若两条指令争用同一 bank，第二条留在取指队列，下一周期再发射。严格按序提交每周期最多一条。

## 写程序、数据并生成工程

推荐使用 [`tools/assembler.py`](tools/assembler.py) 编译汇编，而不是手工填写 ROM：

```powershell
python tools/assembler.py examples/demo.s -o build/demo.hex `
  -d build/demo.data.hex -l build/demo.lst --symbols build/demo.sym
python tools/build.py --source examples/demo.s --output build/Superscalar16.circ
```

汇编器支持 `.text/.data`、标签和表达式、`.word/.fill/.zero/.space/.align/.ascii/.asciz`、
`.include/.equ`，以及 `LI/MOV/B/BEQZ/BNEZ/CALL/J/JR/RET` 伪指令。`LI` 可以加载任意
16 位常量；大常量会自动展开为多条指令。完整语法和诊断示例见
[`doc/汇编器使用说明.md`](doc/汇编器使用说明.md)。`.data` 镜像由 `--source` 自动嵌入
数据 RAM；使用 `--program` 时可用 `--data` 单独指定 `v2.0 raw` 数据镜像。

程序是 Logisim `v2.0 raw` 格式的 16 位十六进制字流，从地址 0 开始。可以在 `InstructionMemory` 电路中分别编辑标为 `IMEM0`、`IMEM1` 的两个 ROM，确保两者内容完全相同，然后复位。工程生成器也可从程序文件重新嵌入 ROM：

```powershell
python tools/build.py --program path\to\program.hex --output Superscalar16_impl.circ
```

生成器会覆盖指定输出文件；要保留原工程，请另选 `--output` 路径。`tools/demo.hex` 是默认演示程序。测试工具在内存中替换 ROM，不会修改 `.circ` 文件。

## 外设 IO 接口

`CPU_Core` 和主电路 `Superscalar16` 都导出以下外设引脚：

| 引脚 | 宽度 | 用途 |
|---|---:|---|
| `io_addr` | 16 | 当前访存字地址；仅 `F000` 到 `F0FF` 属于外设窗口 |
| `io_write_data` | 16 | Store 提交时送给外设的数据 |
| `io_write` | 1 | 外设写使能脉冲；只在 Store 按程序顺序提交时有效 |
| `io_read` | 1 | 外设读使能脉冲；只对最老的 MMIO Load 有效 |
| `io_read_data` | 16 | 外设组合读数据，接回 CPU/顶层输入 |
| `io_clk` / `io_reset` | 1 | 与 CPU 同步的时钟/复位转发 |

在 Logisim 中，把计数器、GPIO、UART 等子电路放在 `Superscalar16` 顶层，连接
`io_addr`、`io_write_data`、`io_write` 和 `io_read`，用地址低 8 位选择寄存器；所有未选中的
外设把读数据置 0，再用多路选择器把结果接到 `io_read_data`。程序中把外设地址放入寄存器后，
直接使用 `LW`/`SW` 访问即可。例如：

```asm
LI  r1, 0xF000       ; r1 = 外设基址
LI  r2, 0x0041
SW  r2, 0(r1)        ; io_write 脉冲，向地址 F000 写入 0x41
LW  r3, 1(r1)        ; io_read 脉冲，从地址 F001 取回数据
```

普通 RAM 访问保持在 `0000–EFFF`；`F000–F0FF` 的写入不会改写 RAM。无数据镜像时复位会清空
RAM；由 `.data` 或 `--data` 嵌入初值时，生成器会保留该初值，复位不会把它清零。

## 验证

本机已验证：26/26 完整处理器程序逐条提交对比通过；前端 1,275 项、ALU/重命名/PRF/Store Buffer/DMEM 953 项原生断言通过；ISA 参考模型 8 项测试通过（含全部 65,536 个编码往返）；17 个正式子电路均无位宽错误、错误值或振荡，原生绘图边界审计为 0 个重叠候选。默认演示程序 23 周期停机，提交 6 条指令。

以下命令需要 Python、Java 21 和 Logisim Evolution 5.0.0 all-in-one JAR。默认路径指向本机的 `D:\logisim\app\logisim-evolution-5.0.0-all.jar`；其他环境可设置 `LOGISIM_JAR` 与 `LOGISIM_JDK_BIN`（或 `JAVA_HOME`）。

```powershell
python -m unittest discover -s tests -p 'test_*.py'
python tools/test_frontend.py
python tools/test_core_units.py
python tools/test_primitives.py
$jar = $env:LOGISIM_JAR
if (-not $jar) { $jar = 'D:\logisim\app\logisim-evolution-5.0.0-all.jar' }
javac -cp $jar tools/FrontendTests.java tools/CoreUnitTests.java tools/LogisimHarness.java tools/LayoutAudit.java
java -cp "$jar;." tools.FrontendTests tools/frontend-tests.circ
java -cp "$jar;." tools.CoreUnitTests tools/core-unit-tests.circ
java -cp "$jar;." tools.LogisimHarness tools/primitive-tests.circ --all-circuits --strict
java -Xmx3g -cp "$jar;." tools.LayoutAudit Superscalar16_impl.circ Superscalar16 tools/layout-dashboard.png --all-circuits --pairs 20
python tests/run_cpu.py
```

前面三个 `tools/test_*.py` 只生成原生测试电路；断言由后面的 Java 命令执行。`LayoutAudit` 使用 Logisim 自身的绘图边界离屏渲染 PNG，并检查器件本体与标签包络；报告的 overlap 是保守候选，最终应为 0。若 `javac`/`java` 不在 PATH，请用 Java 21 JDK 的完整可执行文件路径，或者把 JDK `bin` 加入 PATH。`tests/run_cpu.py` 在 Logisim 原生引擎中执行 `tests/programs.json` 的定向程序，并逐条对比独立 ISA 参考模型的提交顺序、结果、最终寄存器与内存。用 `--quick` 跳过长程序，用 `--names NAME` 只运行指定案例。生成源码在 `tools/circuit.py`、`frontend.py`、`backend.py`、`core.py`、`build.py`；修改生成器后重新运行 `python tools/build.py`，避免手工改动工程被覆盖。
