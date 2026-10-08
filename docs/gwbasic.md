# GW-BASIC 解释器（BIOS 二级程序）

`firmware/apps/gwbasic/` 是一个可在本机常驻 BIOS 下加载运行的 GW-BASIC 解释器。它是一个普通 RPB1 二级程序：ROM 把 BIOS 装进 DDR 并执行，BIOS 再从 SD 或 TFTP 把 `BOOT.RPB` 装到 `0x01000000` 并跳转；解释器只通过 BIOS 的 ecall 服务访问串口/LCD、时钟、SD 和帧缓冲。

## 构建与安装

```powershell
& $MiniPython scripts/gwbasic_image.py                      # 硬浮点 rv32imaf/ilp32f（默认）
& $MiniPython scripts/gwbasic_image.py --soft-float          # 软浮点 rv32im/ilp32
```

产物是 `build/gwbasic/BOOT.RPB`（约 95 KiB，入口 `0x01000000`，另声明 4 MiB `.bss` 作为解释器堆）。把它放进 SD 卡根目录或让主机提供 TFTP，然后在 BIOS 里：

```
set file BASIC.RPB      (或 set server <host-ip> 后用 boot net BASIC.RPB)
boot sd BASIC.RPB
```

`SYSTEM` 返回 BIOS（BIOS 打印 `PAYLOAD RETURNED` 后回到 setup）。也可用 `scripts/gwbasic_sim.py` 在 QEMU 上直接运行，见下文"验证"。

### 硬浮点前提

默认构建使用单精度 FPU（`-march=rv32imaf -mabi=ilp32f`）。RISC-V 上 `mstatus.FS=0` 时任何浮点指令（含 `frrm`）都会触发非法指令，而本机固件只在带 FPU 的构建里置位 `mstatus.FS`：

```asm
/* firmware/boot/start.S */
#if MINI_FEATURE_FPU && !MINI_BOOTLOADER
    li t0, 0x6000
    csrs mstatus, t0
#endif
```

所以**硬浮点 payload 只能跑在 `MINI_FEATURE_FPU` 的 full 配置上**；minimal/lite（无 F）配置请用 `--soft-float` 构建。这是解释器自身不做 FPU 使能的原因：它沿用 BIOS 已经设好的状态，与 `bios_demo.c` 等既有二级程序一致。

## 用法

启动后是标准 GW-BASIC 交互：直接输入带行号的程序行、直接执行语句、`RUN` 运行。提示符与横幅照抄 GW-BASIC（`Ok`）。

```
GW-BASIC for riscv-mini (BIOS payload)
Memory: 4096 Bytes free
Ok
10 FOR I=1 TO 3: PRINT I;: NEXT I
Ok
RUN
 1  2  3
Ok
SYSTEM
```

输入来自 BIOS 的 UART/USB 键盘合并队列。payload 拿到的是**原始字节流**，所以解释器自己的行编辑仍然只有退格：BIOS setup 提示符下那套解码（方向键、Home/End、Ctrl-A/E/K/U/W）属于 BIOS 命令行的行编辑，不作用于 payload。Ctrl-Break（`0x1C`）在语句之间生效，报 `Break in <line>`，可用 `CONT`/`CONT` 续跑。

输出侧则相反：从 v0.8.0 起 BIOS TTY 会解析 VT100/ANSI 序列而不是把转义字节当字符画出来（见 [BIOS](bios.md) 的「VT100/ANSI 终端」一节）。因此 `CLS` 在文字模式下发的 `ESC [ 2 J ESC [ H` 现在是真正的清屏加归位；在更早的固件上它会被逐字节栅格化成 `[2J[ H` 之类的可见垃圾。

### 已实现的语句

`LET`（可省略）、`PRINT`（含 `PRINT USING`、`TAB()`/`SPC()` 之外的分区逗号与分号规则）、`INPUT`、`LINE INPUT`、`IF/THEN/ELSE`（ELSE 绑定最近的 IF，THEN/ELSE 取整行剩余部分）、`FOR/NEXT/STEP`、`WHILE/WEND`、`GOTO`、`GOSUB/RETURN`、`ON ... GOTO/GOSUB`、`ON ERROR GOTO/RESUME`、`DATA/READ/RESTORE`、`DIM`（含 `a(n TO m)` 与多下标）、`ERASE`*、`OPTION BASE`、`DEF FN`、`DEFINT/DEFSNG/DEFDBL/DEFSTR`、`RANDOMIZE`、`SWAP`、`STOP`/`END`、`REM`/`'`、`CLEAR`、`TRON/TROFF`、`CLS`、`COLOR`、`LOCATE`、`SCREEN`、`PSET`/`PRESET`、`LINE`、`CIRCLE`、`PAINT`、`OUT`/`WAIT`（板上无 I/O 端口空间，`INP` 返回 -1）、`BIOS`（直接发起一次 ecall）。

直接模式命令：`RUN [line]`、`LIST [from[-to]]`、`NEW`、`RENUM`、`DELETE`、`CONT`、`LOAD "name"`、`SYSTEM`、`TESTS`。

### 已实现的函数

数值：`ABS SGN INT FIX CINT CSNG CDBL SQR EXP LOG SIN COS TAN ATN RND TIMER FRE POS CSRLIN ERR ERL SCREEN POINT LEN ASC VAL`。
字符串：`CHR$ STR$ LEFT$ RIGHT$ MID$ INSTR SPACE$ STRING$ HEX$ OCT$ UCASE$ LCASE$ DATE$ TIME$ INKEY$ INPUT$`。

类型规则与 GW-BASIC 一致：`%` 16 位整数（越界报 Overflow，不回绕）、`!` 单精度、`#` 双精度、`$` 字符串，未声明后缀的变量按 `DEFtype`（默认单精度）。变量名只有前两个字符加类型后缀有效。数值的显示遵循 GW-BASIC 规则：单精度 7 位有效数字、双精度 16 位，`-4 <= E <= sig-1` 用定点否则用 `E+nn`，小于 1 不写前导 0，`PRINT` 给非负数留一个符号位并在数字后补一个空格。`STR$` 保留同样的符号位。

## 与 GW-BASIC 的差异

这些是硬件或 ABI 决定的边界，不是未实现的敷衍：

- **屏幕**：本机只有一种图形模式（480×272 RGB565 面板）。`SCREEN 1` 与 `SCREEN 2` 都切到这块面板，坐标就是面板坐标，颜色取 CGA 16 色映射；图形模式下文字由解释器用共享的 5×7 字模直接画进帧缓冲（`firmware/common/font5x7.h`），`CLS`/`LOCATE`/`COLOR` 生效于该画布。文字模式（`SCREEN 0`）下 `LOCATE` 无法控制 BIOS TTY 光标，按无操作处理（`COLOR` 同理只改解释器自己记的前景/背景，没有向控制台发 SGR），`CLS` 发 `ESC [ 2 J ESC [ H` 交给 BIOS 的 VT 解析器执行。
- **`SAVE` 不可用**：BIOS 的 ecall 服务只有 `FILE_READ`，没有文件写服务，所以解释器只能 `LOAD`（按 4 KiB 分块读 SD 根目录下的文本程序），不能存盘；`SAVE` 会给出明确提示。往 SD 写文件需要 FatFs，超出本 ABI。
- **文件语句**：`OPEN/CLOSE/PRINT#/INPUT#/FIELD/GET/PUT` 未实现（`OPEN` 等关键字被识别但按无操作跳过）。
- **`PRINT USING`** 支持 `# . , + - $ $$ * ! & \...\ _ ^` 的常用子集：`#` 位数、`.` 小数点、`,` 字面逗号、`$$` 浮动美元号、`*` 星号填充、前导 `+` 强制符号、`!`/`&`/`\...\` 字符串域、`_` 转义下一个字符；放不下时按 GW-BASIC 习惯打 `%`。不支持区域设置（`SET`）、货币/日期掩码等扩展。
- **三角函数**：参数归约用两段 `pi/2` 常数，`|x|` 超过约 `1.6e6` 后精度下降（`frrm` 级别的误差在 1e8 附近约 1e-8）。`EXP/LOG/SQR/ATN/^` 相对误差在 2e-16 以内（自检里用 Python `math` 生成的参考值 + 相对容差核对）。
- **`CINT`/取整**：半数入偶（MS BASIC 的规则）。`RND` 用 32 位 xorshift 而不是 GW 的 LCG，只保证 0..1 单精度分布；`RANDOMIZE` 可复现。`DATE$` 固定 `01-01-80`（板上没有 RTC），`TIME$` 由启动后的毫秒数推出。
- **`PEEK`/`POKE`/`CALL`/`DRAW`/`PLAY`/`SOUND`/`GET`/`PUT`** 未实现（`PEEK` 等会明确报错而不是返回垃圾）。
- **内存**：解释器堆 4 MiB（`.bss`），程序行、变量、数组、字符串都从这里分配；`FRE(0)` 报剩余字节数除以 4。程序行的存储上限是 255 字符/行、65529 行号。
- 错误信息用 GW-BASIC 的英文名（如 `Syntax error in 10`、`Duplicate definition`、`Out of DATA`），代码表见 `basic.h` 的 `E_*`。

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `basic.h` | 全部共享类型、错误码、平台接口 |
| `mem.c` | 尺寸分级堆、引用计数字符串、值语义与运算符、**精确十进制**数值格式化、`PRINT USING` 域 |
| `basmath.c` | 不依赖 libm 的 `EXP LOG SQR SIN COS TAN ATN ^`（归约 + 多项式） |
| `prog.c` | 程序行链表、变量/数组表、词法助手、常量扫描、`DIM`/`DEFtype` |
| `expr.c` | 表达式递归下降、数组下标、`DEF FN` 用户函数 |
| `funcs.c` | 内建函数表与实现、`RND`、`ERR/ERL`、`POS/CSRLIN` 输出列跟踪 |
| `stmt.c` | 全部语句、PRINT/INPUT、控制栈（FOR/GOSUB/WHILE）、DATA、运行驱动、图形语句 |
| `screen.c` | 帧缓冲绘制、文字栅格化、CGA 调色板、图形语句实现 |
| `shell.c` | 控制台缓冲与行列跟踪、REPL、直接模式命令、`LIST/RENUM/DELETE/LOAD`、控制台捕获 |
| `selftest.c` | `TESTS` 自检：格式化/运算/内建/数学/USING/程序行 + 一段端到端小程序 |
| `plat_bios.c` | ecall 后端、帧缓冲与文件读取、`SYSTEM` 退出 |
| `setjmp.S` | 14 字 `bas_setjmp/bas_longjmp`（错误处理与 `SYSTEM` 用） |
| `main.c` | `payload_main`：接 BIOS 描述、跑 REPL、`SYSTEM` 后返回 BIOS |

## 验证

```powershell
& $MiniPython tests/gwbasic_test.py -v          # 镜像布局 + QEMU 上跑真实代码
& $MiniPython scripts/gwbasic_sim.py --script-text 'TESTS' --expect "ALL TESTS PASSED"
```

`tests/gwbasic_test.py` 先校验 RPB1 头/CRC/装载地址/入口/内存声明（就是 BIOS 校验的那几项），再用 `scripts/gwbasic_sim.py` 把**真实 payload 目标文件**跑在 QEMU 上。仿真器用一个 shim 顶替 BIOS：提供 `bios_info`、按 ABI 服务 ecall（UART、确定性时钟、无设备时返回 -1）、在 `_start` 里照抄 `firmware/boot/start.S` 的 `mstatus.FS` 使能、并把异常打印成 `SIM TRAP cause=... mepc=...` 而不是静默挂死。**它不模拟**：真实 LCD/键盘/SD/USB、DDR 训练、BIOS TTY 行为、Flash 装载，也不校验 RPB1 镜像本身（镜像由上面那组主机断言检查）。

因为 payload 不是位置无关的（`-msmall-data-limit=0` 让它用绝对地址），仿真构建把同一批目标文件重链接到 `0x80000000`（QEMU virt 的 RAM 基址）；板端链接在 `0x01000000`。**只有地址常量不同**，代码路径与编译产物一致。

板端验收：把 `BOOT.RPB` 放到 SD 或用 TFTP 提供给 BIOS，`boot sd|net <file>` 进入解释器后执行 `TESTS`，看到 `ALL TESTS PASSED (77 checks)` 即与仿真结果一致。`scripts/gwbasic_verify.py` 把这条流程串起来（TFTP 导出 + 串口驱动 + 交互检查 + 结果 JSON）；它**不**写 Flash、不重启板子、不改主机网络或防火墙。注意 TFTP 服务必须用主机防火墙已放行的解释器启动（仓库 `.venv` 不在放行列表里，用它监听 UDP/69 收不到板端请求）。

**兼容性前提：payload 的 load 是 `0x01000000`，只能由零基址 RAM 改造之后的 BIOS（v0.8.0 起）装载。** 更早的固件（例如 Flash 里的 v0.7.1）地址映射是 `0x41000000`，会以 `BOOT rejected: format/range/version/CRC` 拒绝同一镜像。所以硬浮点 payload 需要「v0.8.0+ gateware 且带 FPU 的 full 配置」同时成立；v0.8.0 之前的板子要先把 gateware 下到 SRAM / 更新 Flash 才能用它。
