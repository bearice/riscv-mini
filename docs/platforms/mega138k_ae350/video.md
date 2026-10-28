# AE350 HDMI、多模式帧缓冲与控制台

> 适用机器：`mega138k_ae350`。配置以机器 JSON 和所选硬件产物的导出为准。

Tang Mega 138K Pro 的 HDMI TX 使用 TMDS0：时钟 Y22/Y23，数据 lane0
AB24/AC24、lane1 V24/W24、lane2 AA22/AA23。Dock 原理图标记为 DVI_TX；
另一组 K20/J20 时钟对应 HDMI RX。管脚与 3.3V/LVCMOS33D 约束来自本仓库
Board 描述，构建读取本地实现和本地 IP。

## 时钟与视频

HDMI 采用 DVI 兼容 TMDS，每通道固定 8 bit。帧缓冲格式可在运行时选择
RGB565（16 bpp）、RGB888（24 bpp）或 XRGB8888（32 bpp，X 忽略）；
RGB888/XRGB8888 在递增内存地址上依次为 B、G、R、可选 X，RGB565 为小端。
这不是 HDMI 10/12-bit Deep Color。

模式表由 `gateware/machines/mega138k_ae350/video_modes.py` 定义，ID 为表中索引。
时钟使用 GW5A PLL 的动态分频与 1/8 步长反馈配置，串行器始终为像素时钟的 5 倍。
软件从模式描述读出标称像素时钟、实际配置像素时钟和据此计算的刷新率；
以下实际值是 PLL 配置计算值，不是仪器测量结果。

| ID | 标称模式 | 配置像素时钟 | 配置刷新率 |
| --- | --- | --- | --- |
| 0 | 640×480 / 60 Hz | 25.178571 MHz | 59.9490 Hz |
| 1 | 800×600 / 60 Hz | 40 MHz | 60.3165 Hz |
| 2 | 1024×768 / 60 Hz | 65 MHz | 60.0038 Hz |
| 3 | 1280×720 / 50 Hz | 74.166667 MHz | 49.9439 Hz |
| 4 | 1280×720 / 60 Hz | 74.166667 MHz | 59.9327 Hz |
| 5 | 1920×1080 / 24 Hz | 74.166667 MHz | 23.9731 Hz |
| 6 | 1920×1080 / 25 Hz | 74.166667 MHz | 24.9719 Hz |
| 7 | 1920×1080 / 30 Hz | 74.166667 MHz | 29.9663 Hz |

默认模式为 ID 0、RGB565、输出 enable=1，保留启动测试图行为。
1080p50/60 的更高串行速率不在此模式表内；未实现 EDID 自动选择和交错扫描。
新模式和动态 PLL 的 RTL/模型验证不能替代完整 PnR 或显示器/采集卡验收。
此前已验收的旧版为 25 MHz、800×525、59.5238 Hz；原有采集证据只适用于旧产物。
采集卡的 USB 帧率不等于 HDMI 输入刷新率。

已验收基线的系统/核心/数据/DDR CK 为 80/800/100/400MHz。数据时钟使用 PLL 静态
+50ps 下降沿微调，对应高/低 5.05/4.95ns 和 SDC 50.5% 占空比。
旧版 HDMI 串行器使用低速 PLL 相移输出释放复位。运行时版本的释放时钟
相对像素输出延后四分之一 VCO 周期，所有模式均保留 recovery/removal 检查；
其板级时序收敛必须针对新产物重新确认。机器 JSON 的 rtl.hdmi_reset_sites
固定三路释放触发器的本地位置，避免长复位路由；该约束不豁免时序检查。
STA 保留 recovery/removal、setup/hold 与高低最小脉宽检查。

## DDR 与帧所有权

CPU 和 scanout 共用既有 256-bit DDR3 UI，按完整事务轮转。授权先寄存，
可在上一读取等待返回时预留下一客户端；未发送的预留可撤回，已经接受的读取
仍保留其所有权。reader 在当前数据交付时提前声明下一块意图，覆盖授权寄存延迟。
每次读拥有
接口直到真实返回；超时进入隔离并排空迟到响应。CPU 写命令与数据共同
握手，scanout 每条命令只读 32B。最大服务等待统计包含仲裁与实际读响应
延迟，单位为 100MHz UI 周期，计数饱和于 `0xffffff`。

scanout 描述符携带 base、stride、epoch 和 run。256-bit 返回按当前格式解包；RGB888 使用字节余量拼接，支持像素跨越 32B 读取边界。
解包后的像素经 2048 项异步 FIFO 进入像素域；每个像素带 epoch 和帧内索引。
配置通过一致快照跨域，在垂直消隐处一次应用。换帧会放弃尚未发出的旧帧
请求，在途读取先排空，FIFO 中的旧 epoch 在消隐期丢弃。

PHY 输入将 RGB、DE、HS/VS 一起寄存一级，隔离 FIFO 标签比较和 TMDS 编码器的直流平衡计数路径。
视频时序持续运行。缺少所需像素时，该帧剩余部分输出黑色并计错，下一帧
重新对齐。`UNDERFLOWS` 按坏帧计，`MISSING` 记录被遮黑的活动像素。
FIFO 最低水位由同步后的 Gray 写计数估计，为保守观察值。

停用仍发送 stop 描述符；只有像素端停用、DDR reader 停用且空闲后，
`STOPPED` 才成立。复位同时作用于视频 FIFO 的两端。测试图模式无需 DDR 读取。

## 控制页

地址 `0xf8030000`，ID `0xae350501`，ABI `0x00010000`（major 1）。capability 为 RGB565、双缓冲、
垂直消隐提交和内建测试图；新增 bit4 RGB888、bit5 XRGB8888、bit6 运行时模式、bit7 HPD、bit8 HPD事件中断。SYSINFO 设备表从 `+0x120` 发布它。

| 偏移 | 寄存器 | 语义 |
| --- | --- | --- |
| 00/04/08 | ID/ABI/CAP | 只读身份 |
| 0c/10 | CONTROL/STATUS | 暂存 run/slot；当前 run、slot、pending、busy、STOPPED |
| 14/18/1c | IRQ_STATUS/IRQ_ENABLE/LAST_ERROR | HPD事件W1C、事件屏蔽、最近错误标志 |
| 20/24/28 | BASE0/BASE1/STRIDE | 暂存地址与每行字节数 |
| 2c | COMMIT | 写1提交完整快照，读为 pending |
| 30/34 | FRAMES/UNDERFLOWS | 光栅帧计数（ENABLE=0也运行）、坏帧数 |
| 38/3c | ADDRESS_ERROR/TIMEOUT | 地址拒绝、读超时 |
| 40/44/48 | ACTIVE_BASE/PIXEL_HZ/GEOMETRY | 当前地址、频率、宽高 |
| 4c/50/54 | EPOCH/FIFO_MIN/MAX_WAIT | 帧代数、水位、最大 DDR 服务等待 |
| 58/5c/60 | READS/CPU_COMMANDS/MISSING | 命令数、被遮黑像素 |
| 64/68 | SEQUENCE/ACTIVE_SEQUENCE | 已提交/已应用序号 |
| 6c/70 | CANCELLED/ERRORS | 放弃旧帧、busy/非预期响应错误 |
| 74/78 | CLEAR_ERRORS/REQUEST_OVERFLOW | 写1清理软件提交错误；描述符丢失数 |
| 7c/80 | MODE/FORMAT | 当前模式 ID、帧缓冲格式 0/1/2，只读 |
| 84 | MODE_CONFIG | 写 mode ID[7:0]、format[9:8] 原子启动切换 |
| 88/8c | MODE_STATUS/MODE_COUNT | bit0 切换忙；可枚举模式数 |
| 90 | ENABLE | 独立输出使能，0 禁用 / 1 启用，复位 1 |
| 94 | HPD | bit0 稳定连接状态，bit1 当前同步电平，只读 |
| 0x100+ID×0x20 | 模式描述 | 每项8个32-bit只读字，见下文 |

寄存器偏移只有 `address_map.py:VIDEO_REGS` 一份定义，RTL、生成 C 头与 BSP
共同使用。首32字节遵循平台公共控制头。

base 需32B对齐、位于 1MiB 以上的 DDR aperture；stride 需32B对齐、至少
width×bytes_per_pixel 且小于64KiB，完整帧不能越过 aperture。aperture 上限不是全容量
实板验收声明。pending 快照不会被覆盖；非法提交保留当前帧并记录错误。
`CLEAR_ERRORS` 清理提交侧地址/busy 错误，DDR 超时隔离状态仍须排空或协调复位。

## 输出使能与模式切换

`CONTROL.run` 管理帧缓冲扫描，`ENABLE` 独立管理 HDMI 数据输出。
run=0 且 ENABLE=1 仍输出测试图，所以仅停止帧缓冲不足以允许切换模式。
ENABLE=0 时 PHY 保持复位，不发送有效画面；转发像素时钟仍存在，不表示物理拔线。
ENABLE 写入要求 STOPPED=1、pending=0、MODE_STATUS=0；运行中直接关闭将被拒绝，
应先提交 run=0 并等待排空。MODE_CONFIG 还必须满足 ENABLE=0，硬件才能接受。
非法 ID/格式/保留位不被总线接受；忙或仍有所有权的请求置 ERRORS.bit0，保留当前配置。
禁用输出时提交 run=1 也会被拒绝；run=0 的停止提交仍可执行。

切换期间同时复位 FIFO 两端和扫描/reader，PLL 先保持复位后重锁。
重锁后保留复位暖机时间，再等待像素/DDR 域返回本次模式的新序号确认及停止快照确认，
随后清除 MODE_STATUS.bit0。切换忙期间 reader 的 run 强制为0，不能重放旧 FIFO 请求。
即使 LOCK 已报告锁定，只要像素时钟尚未恢复，就不能凭旧状态提前完成切换。
扫描器/reader 计数器与 FIFO 随模式切换复位；提交序号和共享 DDR 仲裁计数保持。
PLL 不重锁时保持禁用与 busy，
BSP 超时返回，不释放可能仍由硬件持有的缓冲。

每项模式描述依次为：宽[15:0]/高[31:16]、配置像素 Hz、水平总计/垂直总计、
h-front/h-sync/h-back（各10位）、v-front/v-sync/v-back（各10位）、
正极性标志 HS bit0 / VS bit1、配置刷新率 mHz、标称像素 Hz。

## EDID 与 HPD

TX J11 的 HPD 经板上分压接 T15，为高有效输入，与 DDC mux 独立。
硬件在 sys 域双级同步，并对两个方向要求连续 2ms 稳定后更新 HPD.bit0；
bit1 为同步后的即时电平。检测不依赖像素时钟或 VIDEO_ENABLE。
`ae350_video_get_hpd(&connected)` 要求两个电平均为1才报告可读取的连接状态，
因此低电平同步后即可阻止 EDID 读取，高电平需先稳定2ms。
旧硬件没有 CAP.bit7 时返回 AE350_VIDEO_DEVICE。
保留轮询接口，同时提供HPD事件中断；不自动重新配置输出。

IRQ_STATUS.bit0=稳定连接、bit1=稳定断连，均为锁存事件，读取不清除；
向IRQ_STATUS写1确认对应位，新事件与清除同拍时优先保留新事件。
IRQ_ENABLE对应位控制屏蔽，复位为0；屏蔽期间事件仍锁存，稍后使能可立即产生中断。
两个状态事件可合并；pending不是插拔次数。事件不随VIDEO_ENABLE或像素时钟停走而丢失。
`irq = (IRQ_STATUS & IRQ_ENABLE) != 0` 接到 `EXT_INT[8]`，SYSINFO视频设备项发布IRQ标志及编号。
EXT_INT编号不等于PLIC source；PLIC source按硬核配置/实板发现确认，不能把8直接传给irq_attach。

BSP提供 `ae350_video_hpd_events`、`ae350_video_hpd_irq_enable`、`ae350_video_hpd_ack`，
事件掩码为 `AE350_VIDEO_HPD_CONNECTED` / `AE350_VIDEO_HPD_DISCONNECTED`。
没有CAP.bit8的旧硬件返回AE350_VIDEO_DEVICE。应用用现有irq_attach、plic_priority、irq_mask
接入已确认的PLIC source，先处理/确认已有事件，再开放所需屏蔽位。
中断处理只读取并确认观察到的事件、通知主循环使旧EDID缓存失效；
EDID读取、缓冲分配和模式切换在前台进行，仍须遵守停止输出和排空的要求。

`ae350_edid.h` 提供 `ae350_edid_read_block` 和 `ae350_edid_read`。
先调用 `board_i2c_init()`；读取复用已初始化、单所有者的管理总线，
暂时选择 TX DDC raw3（GPIO N19/P19/P26=1/1/0），每块完成或出错后恢复先前分支。
结束使用管理总线时调用 `board_management_stop()`；EDID 不自行停止共享的管理总线。
TX DDC 与 PLL0 共用分支，读取只访问 EDID 0x50、扩展段指针0x30，不修改时钟芯片。

每块128字节；块0/1直接写offset并 repeated START读取，兼容不支持段指针的基础 DDC。
块2及以上先向0x30写segment，再连续 repeated START写0x50 offset和读取128字节，最后STOP。
支持0..255号块；验证基础块8字节头以及每块8位累加校验和。
完整读取按基础块byte126读取所有扩展，最多32768字节；不解析扩展或自动选择模式。
缓冲不足返回 AE350_EDID_CAPACITY，blocks_read=1；其他错误时 blocks_read 仅计已校验的块。
未连接或读取前后发现断连返回 AE350_EDID_DISCONNECTED；旧硬件缺少HPD返回 BUS_DEVICE。
总线NACK/超时/仲裁错误保留 BUS_* 状态，错误后需清理管理总线并重新初始化。
任何失败或不完整的内容均不可用于协商；HPD变化后应用应丢弃旧缓存并重新读取。
读取不改变输出使能或模式，配置仍须遵守停止输出后的切换流程。

```c
#include "board_management.h"
#include "ae350_edid.h"
#include "ae350_video.h"

unsigned connected, blocks;
uint8_t edid[256]; /* 更大的扩展表会返回 CAPACITY，可增加容量后重读。 */
int rc = ae350_video_get_hpd(&connected);
if (!rc && connected) {
    rc = board_i2c_init();
    if (!rc) rc = ae350_edid_read(edid, sizeof(edid), &blocks);
    int cleanup = board_management_stop();
    if (!rc) rc = cleanup;
}
```

## BSP 与 BIOS TTY

`firmware/bsp/mega138k_ae350/video/ae350_video.h` 提供 init、frame、present、
stop 与 status。新增 mode_count/get_mode、set_mode、enable、init_buffers 和 frame_data。
set_mode 自动停止排空并写 ENABLE=0，切换后保留禁用状态且废弃旧的软件缓冲配置。
应用随后重新分配足够大的双缓冲，调用 init_buffers，再 present；present 会先启用输出。
原 init/frame 是 RGB565 类型接口，在其他格式下拒绝使用，避免 TTY 按16位像素写错内存。
init 验证双缓冲完整跨度和重叠；frame 只交出软件拥有的
缓冲。present 先按32B cache line clean，再提交并等待应用序号与实际地址
确认。停止或换帧超时后，缓冲仍可能由硬件持有。

TTY 复用 `firmware/bios/vt.c` 的 VT100/ANSI 状态机，AE350 构建配置为80列
30行，8×16单元。渲染器从设备读取几何与 stride，将脏单元绘制到后备帧，
保留每个缓冲自己的单元缓存。共享 VT 头的默认80×34保持原平台设置。
TTY 渲染只支持 RGB565；切换模式或重建双缓冲后需重新调用 ae350_tty_init，
清除旧布局和逐缓冲单元缓存。

诊断使用 0x01000000 与 0x01100000 两块缓冲，避开 P5 的1/2/3MiB audio、
mic 和 CPU 压力区。使用前先整帧写入、clean/invalidate 后逐像素读回，
另有相邻 cache-line guard 检查。

## NixOS 操作

先在仓库根目录进入 `nix develop`，全部构建走 `scripts/mini.py`。
迭代时复用同一个 workspace，保留编译与 PnR 工作目录缓存；以下命令使用默认 workspace。

```sh
python scripts/mini.py build gateware --machine mega138k_ae350 --synthesize
python scripts/mini.py build payload --machine mega138k_ae350 --source firmware/diagnostics/mega138k_ae350/ae350_video_suite.c --optimize=-O2
python scripts/mini.py build payload --machine mega138k_ae350 --source firmware/examples/mega138k_ae350/ae350_hdmi_tty_demo.c --optimize=-O2
```

`board video-suite` 先校验位流/时序、SRAM装载和启动身份，再检查每张送达
USB 帧的六块色板。可用 `--payload` 和 `--cpu-jtag-location` 在采集结束后
核对诊断结果。TTY 使用 `--pattern console --expected-frame <RGB565>`，
按同一 BIOS VT/字体生成的画面比较文字掩码，容忍1像素压缩/缩放偏差。
每帧检查前景/背景采样，完整掩码每秒检查两次；保存 PNG 与统计。
静态图样不能识别重复帧，未送达的 HDMI 帧不能由此路径枚举。

`board audio-suite --video-capture-device /dev/video0` 配合带
`AE350_VIDEO_STRESS=1` 的 audio suite，记录共享 DDR 下 audio/mic、CPU 压力、
video 计数、水位和采集结果。UART 完成之前不暂停 CPU。

## 回归验证

`tests/mega138k_ae350/video_runtime_test.py` 检查所有模式的整帧 DE/HS/VS
与同步极性、厂商 PLL 模型的动态时钟，以及缩短光栅下三种格式的跨 burst 像素顺序、
额外 stride、连续切换、PLL 失锁等待、ENABLE/STOPPED 门控及停止排空。
`video_hal_host_test.py` 检查模式枚举、三种格式的跨度/cache clean、类型保护、超时所有权和HPD能力查询。
`edid_host_test.py` 检查头/校验和/容量、全部扩展块、拔线及实际BSP的E-DDC事务顺序、分支恢复与总线错误。
`hdmi_hpd_test.py` 检查同步、短脉冲过滤、拔线阻止读取、稳定重连、事件屏蔽/锁存和新事件优先于清除。
运行时RTL仿真还检查模式切换且像素时钟停止期间，HPD寄存器仍能更新连接/断连状态，并产生、屏蔽和确认中断。
`tmds_encoder_verilog_test.py` 用独立整数编码模型验证全部256种输入及随机控制/消隐的完整符号与固定延迟。
完整 PnR 与各模式实屏验收需另有对应产物回执。

### 运行时模式候选验收（加入HPD前）

候选 `de65342b8e0e674abb959c3ac873006ac7de55020fc0a9330066e8f311a18d4d`
通过完整 Gowin PnR：setup/hold=0/0，最小脉宽余量 +0.048ns，
最差 serializer recovery/removal 分别 +0.036/+0.055ns，所有模式均保留检查。

- [构建回执](../../../build/runs/20261027T165441999173Z-build-hdmi-runtime-reset-locality-c030f2/result.json)
- [完整视频、DDR/AHB、SD 回归](../../../build/runs/20261027T163010467963Z-hdmi-delimiter-regressions-a38d2f/result.json)
- [最终时钟暂停/新序号确认、平台与 HAL 测试](../../../build/runs/20261027T171002111136Z-hdmi-final-mode-confirmation-f8672e/result.json)

该候选尚未进行各模式的实板显示、采集和并发带宽验收；未编程板子或写 Flash。


### EDID / HPD 轮询候选验收（Migen迁移及中断前）

加入T15 HPD输入的候选 `c7a58e87c8c46ed651be13ccc970bdbe6ca874db3496cc8fcb30bd67dab4fc00`
通过完整PnR，setup/hold=0/0，最小脉宽余量+0.052ns。
BSP交叉编译通过（payload `3349576a101d`），所有构建继续复用default工作区。

- [新硬件构建](../../../build/runs/20261027T174235854554Z-build-hdmi-edid-hpd-258b51/result.json)
- [最终BSP编译](../../../build/runs/20261027T174735417676Z-build-hdmi-edid-hpd-restore-error-b89b0f/result.json)
- [EDID/HPD、HAL、平台和模式切换回归](../../../build/runs/20261027T174512655365Z-hdmi-edid-hpd-regressions-349ae7/result.json)
- [最终总线分支恢复测试](../../../build/runs/20261027T174746840396Z-hdmi-edid-restoration-bc81fb/result.json)
- [像素时钟停止期间的HPD寄存器仿真](../../../build/runs/20261027T175318679365Z-hdmi-hpd-stopped-clock-883c02/result.json)

尚未验证真实显示器的EDID内容和物理插拔；本轮未编程板子或写Flash。

### Migen PHY/TMDS 与 HPD 中断候选验收

PHY/TMDS 已迁为 Migen，本地旧 Verilog 源已移除；保留原语边界和独立编码参考测试。
最终候选 `6bd5bebf957f22a989db75b4b37807cdc0a2619ee264b83a1697138f2e6dfe56`
完整PnR通过：setup/hold=0/0，最小脉宽余量+0.047ns，
最差recovery/removal分别+0.036/+0.055ns。
第三路释放FF位于R36C181[0][B]，兼顾此前两端位置的恢复/保持要求；全部检查保留。
源码已逐项核对产物输入，无不匹配。

- [最终硬件](../../../build/runs/20261028T022458266635Z-build-hdmi-migen-reset-window-4edbb3/result.json)
- [最终BSP编译](../../../build/runs/20261028T023430496410Z-build-hdmi-migen-reset-window-bsp-9a4c8e/result.json)
- [编码、PHY、HPD中断、HAL、EDID、平台及模式切换回归](../../../build/runs/20261027T180547841157Z-hdmi-migen-hpd-irq-regressions-1797ec/result.json)

HPD事件屏蔽/锁存/W1C竞争、EXT_INT[8]连接和设备表发现均通过主机/RTL验证。
尚未进行真实显示器显示、插拔和PLIC中断实板验收；未编程板子或写Flash。
