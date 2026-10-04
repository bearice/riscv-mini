# BIOS 性能测试

默认 BIOS 包含 [benchmark.c](../firmware/bios/benchmark.c)，提供以下命令：

```text
bench                 # 与 bench all 相同
bench cpu
bench mem
bench cache           # L1/L2 命中、冲突及 L2 关闭对照
bench gap 0/2/4/8      # 实验用 native 请求额外间隔；复位恢复 8，不保存设置
bench libc            # 对齐、尾部和越界哨兵检查，不计吞吐
bench io
bench sd 15000000     # 与 7.5 MHz 参考数据比较，支持 7500000/10000000/15000000/30000000
bench net BENCH.BIN    # 从当前 settings.server 接收，不执行镜像
```

`bench all` 包含 CPU、内存、Flash/SD 读取、RGB 帧填充和 UART 发送；网络单独运行，要求主机提供 TFTP 文件。
测试不写 SD/Flash，不保存设置。测试期间暂时关闭 TTY 重绘，LCD 扫描、中断继续运行，结束后重新绘制 TTY。
CPU 和内存的内层循环不调用 USB/network poll；各阶段之间恢复 poll。它测量 BIOS 工作环境中的墙钟耗时，不代表设备裸硬件峰值。

## 工作负载与解释

每条 `BENCH` 行报告名称、size、count、ticks、校验值及 PASS/FAIL。`ticks` 来自独立的 timer0，默认频率 60 MHz；差值使用 32 位模运算，各测试小于一次约 71.6 秒的计数回绕周期。计时期间不打印结果，输出和校验通常放在计时区间外。

| 名称 | 内容 | 计量方式 |
| --- | --- | --- |
| cpu.xorshift | 500,000 次六条依赖整数 shift/xor；含循环控制 | ticks / 迭代数，迭代/s；不是 MIPS |
| cpu.mul-add | 100,000 次依赖 mul/add；含循环控制 | ticks / 迭代数，迭代/s |
| cpu.fadd-dependent | 500,000 次依赖硬件 fadd.s，仅 FPU 配置提供 | ticks / 迭代数，浮点加法/s；不是独立操作峰值 |
| mem.read32/write32/copy32 | 1、4、64 KiB 和 1 MiB 工作集；总计处理 1 MiB | 有效数据 MiB/s；copy 的读写总流量约为有效数据两倍 |
| mem.chase32 | 1 MiB 中 stride=4097 words 的完整依赖索引链，262,144 次加载 | ns / 依赖加载，含索引及循环开销 |
| mem.libc-copy/set | 当前 BIOS memcpy/memset 处理 1 MiB | 有效 MiB/s；对齐 32 位访问，含结束 cache sync |
| io.flash-read | 两次读取相同 64 KiB，比对 CRC，只计第一次读取 | MiB/s；Flash 初始化及校验不计入 |
| io.sd-read | 从 LBA 0 顺序读取 1 MiB，分别 1/8 扇区每调用，CRC 必须一致 | MiB/s；含命令、DMA/cache 维护及每批 hal_poll |
| io.rgb-fill16 | 填充当前未显示帧槽的 480×272 RGB565，逐像素检查 | MiB/s、填帧时间；不包含图元绘制/换帧等待 |
| io.uart-tx | 原始发送 4096 个点，不经过 TTY mirror | 含 FIFO 排空及末字节等待，115200、8N1 |
| io.tftp-rx | 接收最多 1 MiB，仅存入内存 | 包含 ARP、512 B stop-and-wait、进度输出、结束等待；CRC 不计入 |

CPU 采用默认 `-Os` 编译。整数/浮点核心使用明确的指令，整数结果与 C 参考循环比较，FPU 检查精确的 500000.0。
当前 [CPU 生成器](../scripts/cpu_generate.py) 设置 `singleCycleMulDiv=false`、`singleCycleShift=false`；因此依赖乘法、移位测试包含多周期执行，不应把 CPU 时钟直接等同于运算吞吐。
内存读取和复制前执行 D-cache invalidate/fence；小工作集随后重复访问，因此结果包含首轮冷访问和后续热访问。L2 仍启用，没有将 L2 冷/热单独分离。write32 为当前写穿透路径。

临时内存窗口为 `0x46000000..0x460fffff` 和 `0x46200000..0x462fffff`，不重叠 BIOS、ROM 工作区、装载暂存区或 framebuffer。只在 BIOS setup 执行，窗口内旧的二级程序数据会被覆盖；后续启动仍按正常装载流程运行。

USB HID、音频、麦克风是事件或固定采样率接口，本命令不测其最大吞吐。SD 不包含文件系统读写和写入速度；网络不包含 MAC 饱和吞吐、TCP 或独立链路带宽测试。

## 主机重复运行

`bench cache` 针对默认 2 KiB 直接映射 L1 D-cache / 4 KiB 直接映射 L2。两条 32 B 数据行交替访问：间距 32 B 时均可驻留 L1；间距 2048 B 时冲突于 L1 而驻留不同 L2 行；间距 4096 B 时冲突于两级缓存。另对 2048 B 间距关闭 L2 作对照。数据先预热，汇编循环展开八次，每组执行 4096 次依赖指针加载或 262144 次交替写入，统计有效 4 B 数据吞吐及 L2 命中/未命中计数差值。延长写测试覆盖多个 LCD 刷新周期，减少视频仲裁相位影响。计数器为 16 位模计数，包含计时边界可能产生的指令或栈访问；不统计写次数。

缓存测试仅在短计时内关闭 CPU 中断，LCD 扫描继续；它与普通 `bench mem` 的中断开启结果不直接等价。L1 与 L2 都是写穿透，L2 写入还会失效对应行，因此写测试的“hot/conflict”指预热地址布局，不代表写入只在缓存完成。测试结束恢复 L2 原启用状态和中断。无 L2 或不同 L2 容量配置明确报告不支持。

[benchmark.py](../scripts/benchmark.py) 驱动相同 BIOS 命令，默认三轮，保存 UART 原始输出与 JSON，并计算中位数。

```powershell
& $MiniPython scripts/benchmark.py --rounds 3
# 需要该 Python 的 UDP 入站已允许，地址为接 Dock 网口的本机 IPv4。
& $MiniPython scripts/benchmark.py --rounds 3 --host-ip 169.254.25.153
```

网络测试由主机生成已知的 1 MiB 数据，校验接收长度与 CRC。主机不改变网卡、路由或防火墙。`--host-ip` 会设置 BIOS 的当前 server 地址，不执行 settings save。脚本最后运行 `test bios` 和 `status`，检查 BIOS 回归并保留设备状态。

`--suite cpu/mem/cache/io/net` 可单独重复某组测试。`bench libc` 在实板覆盖 304 个对齐、短长度、首个差异和哨兵检查；主机 [libc_memory_test.c](../tests/libc_memory_test.c) 还用标准 libc 参考值、保护页及 sanitizer 检查更大的长度。只对完整对齐字使用 `may_alias` 字类型，非同余对齐的 memcpy 和短尾部保留字节路径；字符串函数保持逐字节读取，避免越过 NUL 所在对象。

[sd_clock_verify.py](../scripts/sd_clock_verify.py) 默认扫描四档读频率，每档三次；`--clocks 15000000 --soak-seconds 300` 进行五分钟只读测试。每轮先在 7.5 MHz 获取参考 CRC，再测试请求频率的单块/八块读取，失败候选明确记录。30 MHz 是实验候选，需要 CMD6 协商且尚未通过实板读取，不能作为默认。结束时检查低速恢复、BIOS 自检和设备状态，不写卡。重新 mount/init 会恢复驱动默认频率。

非 baseline L2 模式下，主机可用 `--gap 0/2/4/8` 在同一 FPGA 配置上扫描额外的 DDR 请求间隔，成功完成后恢复默认值 0。显式选择 baseline 的构建不暴露该 CSR。`--suite net --host-ip ADDRESS` 仅运行现有 `bench net BENCH.BIN` 命令；失败时保留 TFTP 服务事件和 `test eth` 状态，不能把失败的传输计入吞吐。
