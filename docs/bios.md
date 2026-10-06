# DDR 常驻 BIOS

性能测试命令与计时边界见 [BIOS benchmark](benchmark.md)。

默认固件是 `firmware/bios/main.c`。片上 ROM 负责软件 DDR 初始化、Flash/UART 恢复和装载 BIOS；BIOS 在 DDR 中初始化设备、执行 POST，提供文字终端、图形与 IO 服务，再从 SD 或网络引导自定义裸机程序。原来的基础 monitor 可用 `--app firmware/examples/monitor.c` 单独构建。

## 启动与内存

| 区域 | 地址 / 限制 | 所有者 |
| --- | --- | --- |
| ROM 的 L2/DDR 工作区 | `0x407ff000..0x407fffff` | ROM |
| BIOS 代码、data、BSS、栈 | `0x40800000..0x40bfffff` | BIOS，沿用 ROM 应用窗口 |
| 裸机程序入口 | `0x41000000` | 二级程序 |
| 二级程序内存 | `0x41000000..0x47deffff` | 代码、data、BSS；文件载荷最多 4 MiB |
| 二级程序栈 | `0x47df0000..0x47dfffff`，初始 SP=`0x47e00000` | 二级程序及 BIOS trap 临时栈 |
| 装载暂存区 | `0x47000000`，最多 4 MiB + 32 B | BIOS 装载阶段使用，进入程序前释放 |
| RGB565 双帧槽 | `0x47e00000`、`0x47e40000` | BIOS 扫描器 / 图形程序；DDR 尾部 2 MiB 保留 |

其他 HAL 固定工作区仍由 BIOS 使用，例如 SPI LCD 工作区 `0x40300000`。程序只能使用上表分配的程序内存、栈和两个帧槽，不能把其余 DDR 当成可用堆。

BIOS 装载完成后先初始化 HAL、显示和设置，执行有界 POST，然后进入 setup。默认 `boot=none`；配置为 SD/net 时，倒计时内任意 UART/USB 键盘输入可取消自动引导。引导失败返回 setup。程序入口为 `int payload_main(const struct bios_info *info)`；可以正常返回 BIOS，`info` 指向 BIOS 只读信息，程序可以用 `BIOS_INFO` 获取自己的副本。

BIOS 横幅 `RISCV MINI BIOS <semver>+<commit7>.<config8>[.dirty] rom<rom8>` 与 `status` 的 `BUILD` 行（`BUILD <id> rtl<rtl8> rom<rom8>`）使用同一套 `MINI_BUILD_*` 宏，与 ROM 首行一起构成日志到构建的追踪线索：RTL 版本 = `rtl<rtl8>`，ROM 版本 = `rom<rom8>`（`boot.bin` 的 SHA256 前 8 位，完整值见 `validation.json` 的 `firmware_sha256`），BIOS 版本 = `<semver>+<commit7>.<config8>` 加上 `validation.json` 中 `boot_image.sha256` 的 `app.img` 哈希。ROM 与 BIOS 分开链接，因此 BIOS 能打印 ROM 版本，ROM 只打印 RTL 版本。构建身份同时保存在构建目录的 `build-info.json` 与 catalog 记录中。

交接时 BIOS 停止音频 DMA、静音并停止麦克风，复制程序、清零 BSS，维护 D/I-cache 后跳转。常驻驱动和中断保持运行。SDK 建立程序自己的 `gp`；trap 切换到 BIOS 的 `gp` 并恢复调用者寄存器，程序返回时恢复 BIOS 栈及 callee-saved 寄存器。代码见 [装载和服务](../firmware/bios/boot.c)、[交接](../firmware/bios/enter.S)、[trap](../firmware/hal/src/trap.S)。

这版 ABI 面向可信的 M-mode 裸机程序，要求保留 BIOS 的 `mtvec`、中断运行和未启用分页的环境。程序定期调用 `BIOS_POLL` 服务 USB 和终端。它没有进程隔离；地址参数检查防止常见误传，不能限制 M-mode 代码直接访问硬件。OSB1 镜像使用独立的一次性交接，停止 BIOS 驱动并由 OpenSBI 接管 M-mode、进入 S-mode；此时 BIOS ECALL 服务不再有效。详见 [OpenSBI](opensbi-port.md)。

## POST、TTY 与图形

BIOS 的命令、POST、驱动诊断及 `test ...` 文本统一镜像到 LCD TTY 和串口。
控制台初始化时注册 HAL 文本镜像回调，旧驱动的字符串、字符和十六进制输出也经过该路径；
回调只更新 TTY 字符缓冲，避免再次发送串口造成递归或重复输出。图形模式下字符缓冲继续维护，
执行 `tty` 后重新显示。启动 ROM 和底层二进制 UART 传输不接入文字镜像。
输入使用 BIOS 的 UART/USB 键盘共同队列，包括 `test soak` 中的软件复位键。
`ls` 列出 SD 根目录，在目录名后添加 `/`，结束时报告条目数；空目录为 `0 entries`，
挂载、目录访问失败及文件系统未启用都有明确提示。

POST 检查 1 KiB DDR scratch、定时器进度、Flash JEDEC、SD 挂载、Ethernet/USB PHY、HID 枚举和 BIOS ECALL。它检查设备是否可用，不覆盖全部 DDR，也不证明物理音频、网络对端或屏幕颜色正确。缺失可选设备会报告 unavailable/FAIL，但 setup 仍可使用。完整硬件检查通过现有 `test ...` 命令显式运行，boot ROM 不链接这些测试。

TTY 将 UART 115200 8N1 与 USB 键盘合并为输入流，输出到 UART 和大 LCD 的 **80×34、6×8 字符格**。支持 US ASCII、Shift、Caps Lock、换行、制表符、Backspace 和滚屏。字符队列容量 128，满时丢弃新字符；无 ANSI escape、Unicode、键盘自动重复和编辑历史。LCD 根据脏字符更新后换帧。图形模式使用两个 480×272 RGB565 帧槽，stride=960 B，写完后显式 present；`tty` 恢复文字画面。

### PC/AT 兼容边界

这里的 BIOS 表示本机基础固件服务，TTY 不兼容 PC/AT 的二进制调用接口。PC/AT 使用 x86 实模式的 INT 10h 视频服务和 INT 16h 键盘服务；本机使用 RISC-V ECALL。没有实现 PC 的中断向量、BIOS Data Area、字符/属性文本显存、显示页、光标位置/形状服务或 BIOS 扫描码键盘队列。当前显示是固定布局、白字黑底的软件栅格化文字，底层为 RGB565 帧缓冲，不是 VGA 文本模式，也不提供 ANSI/VT 终端协议。

可在 RISC-V 服务接口中逐步提供类似 INT 10h 的光标、字符属性和区域滚屏语义，但这只提供功能映射；执行原有 PC/AT 软件还需要 x86 与相应机器环境的模拟。本版没有增加这些兼容层。PC/AT 接口参考 [IBM PC AT Technical Reference, September 1985](https://bitsavers.trailing-edge.com/pdf/ibm/pc/at/6139362_PC_AT_Technical_Reference_Sep85.pdf)。

输入由 BIOS 统一消费，`test usb input` 观察同一事件流，不抢占 TTY 的键盘队列。鼠标服务返回累计相对位移、滚轮、最新按钮和时间，累计量限幅 ±32767。`test lcd ...` / `test soak ...` 切换到图形模式保留测试画面，结束后用 `tty` 返回文字画面。

## Setup 命令与持久设置

| 命令 | 行为 |
| --- | --- |
| `help` / `status` / `post` | 帮助、当前配置与扫描/音频/IRQ 状态、重复 POST |
| `io` / `led HH` / `rgb RRGGBB` | 按键/DIP 状态、六个单色 LED、WS2812 |
| `ls` | SD 根目录 |
| `tty` / `graphics` | 文字 / RGB565 图形模式 |
| `boot sd [file]` / `boot net [file]` | 从 SD / TFTP 下载、校验并执行程序 |
| `fetch FILE` | TFTP 下载有效程序后创建同名 SD 文件；不覆盖已有文件 |
| `settings` / `settings defaults` | 查看 / 恢复内存中的默认设置 |
| `settings save` / `settings load` | 在 SD 根目录保存 / 读取 `BIOS.CFG` |
| `set boot none/sd/net` | 设置自动引导来源 |
| `set file NAME` | 默认程序文件名，1..63 字节 |
| `set delay 0..30000` | 自动引导等待毫秒数 |
| `set ip A.B.C.D` / `set server A.B.C.D` | 板端 / TFTP 服务器静态 IPv4 |
| `test bios` | 程序格式、截断、CRC、保留区拒绝和 ECALL 错误返回检查 |
| `test ...` / `reboot` | [硬件自检](firmware-tests.md) / 软件复位；`!` 为 setup 的复位快捷键 |

设置默认为 `none`、3000 ms、`BOOT.RPB`、板端 `169.254.20.20`、服务器 `169.254.20.1`。`BIOS.CFG` 是 version=1 的 88 B 小端结构，包含 magic、version、boot、delay、两组 IPv4 和 64 B 文件名；尺寸或字段非法时保持默认值。仅 `settings save` 覆盖此文件，设置不写 Flash。SPI LCD 显示 Flash UID 派生的 MAC、当前 IP 和链路状态。

网络引导采用同一链路上的静态 IPv4、ARP、UDP 和 TFTP octet，RRQ 发送到服务器 UDP69，后续锁定服务器 TID。支持 512 B DATA/ACK、重复块、重传和最终 ACK 等待；整 512 B 文件用零长度 DATA 结束。ARP 最多 10 秒；数据超时 1 秒、最多 5 次重试，传输总上限 120 秒。检查 IPv4/UDP 长度、校验和和分片标志。不支持 DHCP、网关、IPv4 options、分片、TFTP options 或认证。只在可信的直连/局域网使用；CRC 是完整性检查，不是签名。

SD 和 TFTP 装载每完成 64 KiB 打印一个点，并同步显示到 LCD TTY；结束时报告实际字节数、毫秒数和成功/失败。
随后分别打印 CRC、复制、清零和缓存同步的耗时。CRC32 使用 256 项查表，镜像格式及校验结果不变。
TFTP 为 512 B 停等协议，吞吐受逐块往返和软件轮询限制；最后保留约 1.1 秒重复 DATA 确认窗口。
固定地址的二进制镜像包含链接布局中的空洞，传输大小可能显著大于有效代码。

## 程序格式和 ABI

[公开 C 接口](../firmware/bios/include/bios.h) 与 [程序 SDK](../scripts/bios_payload.py) 不依赖生成的 CSR 地址。ABI=1、ILP32：`a7=0x42494f53`，`a6=功能号`，`a0..a5=参数`，`a0=返回值`。负值表示错误（GETC/MOUSE 的 -1 也表示暂无输入）。TIME 返回毫秒计数的 32 位原始位模式，差值应按 uint32_t 处理。

| ID | 服务 | 参数 / 返回 |
| --- | --- | --- |
| 0 | INFO | a0=info 输出指针；0 成功 |
| 1 | WRITE | a0=字符指针，a1=长度 ≤4096；返回字节数 |
| 2 | GETC | 返回字符或 -1，不阻塞 |
| 3 | TIME | 返回 uint32_t 毫秒计数 |
| 4 | POLL | 服务 USB、网络 PHY 和 TTY |
| 5 | VIDEO_MODE | a0=0 文字 / 1 图形 |
| 6 | VIDEO_PRESENT | a0=帧槽 0/1 |
| 7 / 8 | SD_READ / SD_WRITE | a0=LBA，a1=缓冲区，a2=扇区数 1..8 |
| 9 | FILE_READ | a0=文件名指针，a1=偏移，a2=输出，a3=容量 ≤4096；返回实际字节数 |
| 10 | IO_READ | a0=bios_io 输出，buttons/switches 状态 |
| 11 | LEDS | a0=六位灯掩码 |
| 12 | REBOOT | 不返回 |
| 13 | FLASH_READ | a0=Flash 地址，a1=输出，a2=长度 ≤4096 |
| 14 | RGB | a0=0xRRGGBB |
| 15 | MOUSE | a0=bios_mouse 输出；0 成功，-1 暂无事件 |
| 16 | AUDIO_BEGIN | a0=四字节对齐的程序区 uint32_t 环形缓冲区，a1=容量 1..65535 帧；0 成功 |
| 17 | AUDIO_WRITE | a0=四字节对齐的 L16/R16 PCM，a1=帧数 ≤1024；返回实际接受帧数，0 表示暂时满 |
| 18 | AUDIO_CONTROL | a0=0 停止 / 1 播放 / 2 暂停 / 3 静音 / 4 取消静音；0 成功 |
| 19 | AUDIO_INFO | a0=bios_audio 输出；采样率、FIFO 水位、播放/读取帧数、欠载/溢出/错误；0 成功 |

IO/存储服务 0 成功、-1 失败；未启用设备使用 HAL 的 unsupported 路径。info.features 位 0..4 依次表示 SD、视频、USB、Ethernet、音频。音频服务追加于 ABI=1，旧的 0..15 服务和 info 结构布局保持不变；新程序检查音频 feature 位再使用。指针服务只接受程序区域内的完整缓冲区。RAW SD 写会绕过 FatFs，程序自行协调文件系统与扇区访问。Flash 服务只读，编程仍走 ROM 恢复入口。

音频使用打包的 16 位双声道 PCM，采样率由 AUDIO_INFO 查询；默认 DDS 配置为 48000 Hz。
BEGIN 停止旧播放并设置程序拥有的环形缓冲区，WRITE 部分接受时由调用者保留未写入的尾部。
先预填数据，再 PLAY 和 UNMUTE；PAUSE 保留队列，STOP 清空 DMA 状态。程序返回时 BIOS
停止并静音音频，再恢复 TTY。实际示例见 [Nyan Cat](nyancat-demo.md)。

RPB1 镜像头是 32 B 小端八个 uint32_t：magic=`0x31425052`、ABI version、load、file_bytes、memory_bytes、entry、数据 CRC32、头前 28 B 的 CRC32。load 必须为 `0x41000000`，entry 四字节对齐且在文件范围内，memory_bytes 包含 BSS 且保留顶端 64 KiB 栈。文件总长度必须精确匹配头和载荷；两个 CRC、版本和范围均检查后才执行。它与 ROM 使用的 CSR ABI app.img 格式分离，不是 ELF 或 PC BIOS 兼容格式。OSB1 使用相同头字段、CRC 和范围限制，magic=`0x3142534f`，只允许 MMU 配置，入口按 OpenSBI 契约传参且不返回 BIOS。

## 构建与使用

```powershell
. .\scripts\env.ps1
& $MiniPython scripts/build.py --output-dir build/bios
# 重用已验收、CSR ABI 相同的 FPGA 配置，UART 运行 BIOS；不写 Flash。
& $MiniPython scripts/boot_upload.py --reset --mode uart --output-dir build/base --image build/bios/firmware/app.img
& $MiniPython scripts/bios_payload.py
& $MiniPython scripts/bios_payload.py --source firmware/examples/bios_demo.c
# 在接 Dock 的本机网卡地址上运行只读 TFTP；地址须替换为实际地址。
& $MiniPython scripts/bios_tftp.py --bind 169.254.25.153 --file build/bios-payload/BOOT.RPB
```

在 BIOS 中设置 `set server 169.254.25.153`，然后 `boot net`；`fetch BOOT.RPB` 创建 SD 文件后可 `boot sd`。主机脚本不会修改网卡或防火墙；接收 RRQ 需要所选 Python 运行环境能够接收 UDP 入站。默认文件已有时，选择新的文件名。

[端到端验证工具](../scripts/bios_verify.py) 调用相同固件命令，覆盖网络重试/重复块/零长度结束、坏 CRC、SD 重复启动、缺失文件、设置往返和非法参数。运行示例：`scripts/bios_verify.py --host-ip 169.254.25.153 --sd-file BIOSDEM.RPB`；需要 SD 上不存在这个目标文件，会显式保存 `BIOS.CFG`。Python 格式测试位于 [bios_image_test.py](../tests/bios_image_test.py)，板端格式/ECALL 测试为 `test bios`。

默认 full BIOS 已在实际板卡验证 SD/TFTP 执行与返回，并确认 LCD TTY/USB 键盘输入。minimal 仅构建检查，不代表被裁剪的硬件已上板验收。BIOS 不包含 OS 引导、动态装载重定位、DHCP 或图形窗口系统；SDK 示例演示 `.data`/`.bss`、IO、服务调用、RGB565 换帧和返回 BIOS。

USB 键盘/鼠标可直连，也可通过全速 Hub 接入；`test usb tree` 显示配置成功的 Hub、下游端口及设备。当前容量为 2 个 Hub、4 个普通设备、总计 8 个 HID 接口；默认 PIO 后端只支持全速传输，低速设备和超过 7 个下游端口的 Hub 不在支持范围内。详见 [USB Host](usb-light.md)。
