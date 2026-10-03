# 固件测试命令

所有板上检查都从 DDR monitor 的 `test ...` 命令进入；上电不自动执行。
输入 `test` 或 `help` 显示清单。代码在 `firmware/app/tests.c`，
boot ROM 只有硬件 DDR ready 等待和 Flash/UART 装载，不链接这些测试。

| 命令 | 自动检查 | 影响与验收边界 |
| --- | --- | --- |
| `test isa` | C 压缩指令与已启用 Zba/Zbb/Zbs 指令的数值检查 | 按构建能力条件编译；不是完整 ISA compliance |
| `test l2` | L1 清空后的重复读、字节写及 L2 命中，比较 enable=0/1 的 ticks | L2 未启用时报告 UNSUPPORTED；暂时屏蔽 IRQ，使用 `0x40d00000` 的 256 字节 |
| `test uart` | 命令确实经 UART IRQ 接收，检查 RX drop 计数 | 不测物理波特率裕量；串口回显可直接观察 |
| `test irq` | ECALL 保存/恢复、DDR 栈上计算、timer IRQ、deadline 回绕 | 只处理测试主动触发的 ECALL；其他异常仍报告 FAULT |
| `test ddr` | 专用 8 KiB DDR scratch 的全零/全一/交替位/地址及反地址模式 | 不覆盖完整 128 MiB，不改程序、帧缓冲或 PCM ring |
| `test flash` | JEDEC、容量、工厂 UID | 只读；不擦写，不做写后 Verify |
| `test sd` | 挂载、读取 `RVTEST00.BIN`，检查 4,096 字节与 CRC32 `08040e1e` | 固定验收文件必须存在；无卡/坏文件返回 FAIL |
| `test sd blocks` | 比较 16 次单块与一次 16 块读取的 CRC；故意使用非对齐缓冲 | 只读前 16 扇区；覆盖超过 8 扇区 bounce chunk 的请求 |
| `test sd write` | 新建 64 KiB 文件、写模式数据、sync、关闭并逐字节读回 | 只用 CREATE_NEW，最多尝试 100 个名字，不覆盖原文件；文件保留 |
| `test lcd` | DDR 生成色条/白框/灰阶/角标、整帧读回 CRC、换帧、扫描进度和欠载 | 显示测试画面；颜色、方向、边界、闪烁仍需人观察 |
| `test lcd clear` | 重新清空两帧并检查扫描进度 | 恢复基础黑色画布 |
| `test spi-lcd` | 执行 SPI LCD 初始化与状态页传输 | 颜色、字体、方向仍需人观察；屏幕无读回通道 |
| `test io` | 逐一设置六个 LED 并读回、WS2812 busy 完成、读取按键/DIP | 寄存器检查不能替代灯光/按键实物观察；灯光短时改变，最后单色 LED 恢复、RGB 灯灭 |
| `test audio` | 静音 PIO 256 帧与预期 underrun；DDR DMA、pause/resume、进度和错误 | 测试结束停止 DMA，恢复静音；不代表左右声道已听音通过 |
| `test mic` | 启动左声道、等待 200 ms、采集并读取 512 个有符号 PCM24 样本，检查非恒定数据/无溢出，输出 min/max | 启动 BCLK/WS，结束后停止；不代替对声音响应和 LCD 波形的人工确认 |
| `test mic stereo` | 两只麦克风共享 CK/WS，LR 分别为 0/1；读取 512 对 PCM24，检查两路非恒定、独立数值及无溢出 | 需要两只麦克风，输出各路 min/max；不代替声道归属和声音响应的人工确认 |
| `test audio pio` | 只运行 PIO/预期 underrun 检查 | 预期产生的 underrun 会在 stop 时清零 |
| `test audio start/stop/pause/resume` | 操作同一个 128 KiB PCM ring 的 DMA | start 默认静音，pause 保持 ring；resume 需要已 start |
| `test audio tone` | 开启低幅度、左右不同频率的两秒音调，到期自动静音 | 这是主动发声命令，只在准备好听音/line-in 时运行；物理声音需外部确认 |
| `test eth` | PHY ID、50 MHz REF_CLK 计数、错误及工厂 UID 派生 MAC | 不要求 link up，不能据此声称真实收发通过 |
| `test eth parser` | CRC/checksum、UDP MTU/奇数长度、截断、fragment 拒绝、ARP | 使用与独立示例共用的协议验收向量；不是线上收发 |
| `test eth start` | 启用 ARP/ICMP/UDP1234 回显，SPI LCD 显示 MAC 与 `169.254.20.20` | 需要外部主机发包；每轮有界处理 RX，不保证两个 RX 槽能承受任意突发 |
| `test eth stop` | 停止回显并恢复 SPI LCD 的 IP UNCONFIGURED | PHY/HAL 仍运行；不修改主机网卡配置 |
| `test usb` | PHY ID/ready、HID 枚举、错误/队列、DDR HCCA 对齐，以及 OHCI 后端下的 frame 推进和实际周期 ED 链的 TD 所有权 | 接收器空闲也可通过；不能代替真实按键输入 |
| `test usb stop` | 停止 Host/PHY 初始化逻辑，检查 ready 清零 | 保持接收器连接，禁用时 ULPI PLL 仍运行以完成状态复位 |
| `test usb restart` | 停止状态检查、重新初始化、枚举与 Host 进度 | 不需要拔插设备 |
| `test usb input` / `test usb input stop` | 开/关 raw HID 和鼠标事件输出；键盘按下/释放始终输出 | 返回 READY 表示等待实物输入，没有输入时不判 PASS；鼠标坐标为带符号值的 32 位十六进制 |
| `test usb leds DEVICE INTERFACE MASK` | 调用异步 HID LED 发送；参数为十进制、mask 0..31 | 返回 QUEUED 只表示驱动接受请求，需看键盘灯；device/interface 可从 raw HID 输出取得 |
| `test phys` | 共享 F10 复位后 USB 恢复枚举、Ethernet PHY/MAC 恢复 | 两个 PHY 一起复位，运行中的网络请求可能丢失 |
| `test soak N` | 目标 1..300 秒静音 DMA、SD 文件 CRC、整帧校验/换帧、USB/OHCI 与错误检查 | 前台命令；每轮检查 `!` 复位，其余期间输入忽略；最后一轮完整结束后返回，停止音频，可用 lcd clear 清除画面 |

`TEST ... PASS` 表示表中“自动检查”通过。测试不修改 Flash；SD 写入、
音频发声、共享 PHY 复位和屏幕/灯光改变都只在对应命令显式运行时发生。
`test soak` 不自动打开 Ethernet 回显；需要并发网络时先执行 `test eth start`，
再让主机持续发 UDP，结束后执行 `test eth stop`。网络突发丢包限制见 [轻量 USB](usb-light.md)。

## 批量运行

主机脚本仅负责 UART 装载和调用固件命令，不重新实现 DDR/SD/USB 等判定逻辑：

```powershell
& $MiniPython scripts/firmware_verify.py --program
# 已在运行 monitor：软件复位后执行所有自动检查，并创建新 SD 测试文件。
& $MiniPython scripts/firmware_verify.py --reset --sd-write --soak-seconds 300
```

默认产物为 `build/base`；不同构建使用 `--output-dir`，不同但 ABI 匹配的应用使用 `--image`。
脚本不会拔插 USB、触发按键输入或键盘 LED 请求、发声、写 Flash、改变网卡，
也不自动宣称屏幕、鼠标或网络物理验收通过。报告与 UART 日志分别为
`firmware-verification.json` 与 `firmware-verification-uart.log`；重复命令的每次结果均保留。

## 仍在主机上的检查

HDL 模拟、外部 IO 边沿模型、Gowin PnR/资源报告、主机 UDP 收发和 WinMM
音频捕获只能在相应主机工具中完成，固件无法观察自己的布局布线或主机收到的包。
ROM 的坏镜像/分包 CRC/截断拒绝仍由 `scripts/boot_verify.py` 经 UART 测试真正的
bootloader，不在正常应用里复制另一套 loader。对应 `sim/` 检查与旧独立示例保留；
常规板上验收优先使用本页的 monitor 命令，不再需要临时探针固件。

已运行 monitor 时，`scripts/monitor_external_verify.py` 可调用 `test audio tone`
并经本机 Line In 检查左右声道、频率和自动静音；再调用 `test eth start`，
经指定网卡执行 ARP、ping、UDP echo，并交替执行现有 SD/LCD/USB 命令。
该脚本只承担 UART 传输和外部观测，不复位、不写 Flash、不修改网卡配置。

```powershell
& $MiniPython scripts/monitor_external_verify.py --output-dir build/base --audio --host-ip 169.254.25.153 --interface-index 80 --seconds 30
```

主机 IP 和接口序号必须按当前连接查询；示例数值不是固件配置。执行音频选项会
播放两秒低幅度测试音，输出连接 Line In 时用于自动测量。
