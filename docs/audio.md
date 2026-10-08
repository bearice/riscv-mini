# PT8211 音频输出

当前默认音频使用共用 DDS，平均 BCK 1.536 MHz、32 BCK/立体声帧、48,000 Hz。所有逻辑保留在 sys 域，无新增 PLL；GPIO 边沿量化到 sys 周期，详见 [时钟树](clocks.md)。`--audio-clock legacy` 为可选整数分频模式：BCK 1.5 MHz、采样率 46,875 Hz。录音输入由独立 [麦克风](microphone.md) 模块提供。

## 线序与 PCM

| 信号 | FPGA 引脚 | 时序 |
| --- | --- | --- |
| BCK | N15 | 连续平均 1.536 MHz，即使暂停仍保持时钟、发送零 |
| DIN | P15 | 16 位二补码，MSB 先传，在 BCK 下降沿更新 |
| WS | P16 | 低为右声道，高为左声道，每声道 16 BCK |
| PA_EN | R16 | 高有效；复位、暂停、停止和 mute 均关闭 |

IO 使用 LVCMOS33。PT8211 在 BCK 上升沿采样，采用 LSB-justified/right-justified 格式；这不是带一位延迟的普通 I²S。声道极性以 [PT8211 原厂手册](https://www.princeton.com.tw/LinkClick.aspx?fileticket=S9pGNkngU-k%3D&language=en-US&mid=13928&portalid=0&tabid=3476)为准，本地示例中 WS 低为左的注释相反。

每个 `uint32_t` 是一帧 stereo PCM16：**低 16 位左、高 16 位右**。little-endian DDR 内存为左声道两个字节，然后右声道两个字节。DMA 不做 byte swap；序列器先发高半字右声道，再发低半字左声道。满幅为有符号 16 位，测试示例峰值仅 1,024（约 −30 dBFS）。

## FIFO、DMA 与所有权

FIFO 共 512 stereo frames / 2 KiB，包含输出寄存器，约 10.7 ms 音频。PIO 通过 sample CSR 填写，正常 HAL 满时返回 `HAL_BUSY` 和实际写入帧数，不丢弃输入；直接向满 FIFO 写 CSR 才增加 overrun。

DDR DMA 作为 32 位只读 Wishbone master，共用现有 DDR 桥、视频优先调度。每次只发一个 word 请求，响应后释放总线；FIFO 满或没有已提交数据时停止预取，不锁总线等待下一帧。DMA 错误会停止发新请求。没有新 DDR 物理端口、DMA IRQ 或音频 IRQ。

DMA ring 容量为 1..65,535 帧、四字节对齐，必须完全位于 `[0x00000000,0x07e00000)` DDR，不能覆盖帧缓冲保留区。应用负责分配有效、独占的内存；HAL 的范围检查不替代应用的内存所有权管理。

producer 是已提交帧的 32 位累计计数，fetched 是已复制到 FIFO 的累计计数，二者相减得到 ring 中未取数据。`hal_audio_ring_write()` 只复制到空闲槽，执行 `fence rw,rw` 后更新 producer；DMA ACK 后更新 fetched，CPU 才可重用该槽。FIFO 中数据仍受硬件保护，不需要等到播放后才释放 ring 槽。计数按无符号回绕，差值不得超过容量；硬件拒绝错误的 producer。

默认构建启用 4 KiB 共享 writeback L2；音频 DMA 是独立 Wishbone master，与 CPU 共用 main-RAM 的 32-bit L2 入口；SD lite 使用 coherent 128-bit 入口。模型是单裸机主循环，不能在 ISR 或多个任务中同时提交 PCM。`hal_poll()` 不自动合成或补充 PCM：应用要按返回的 written 推进自身位置、及时 refill。示例用 32,768 帧 / 128 KiB ring（约 683 ms）覆盖 SD 读取和 CPU 写视频期间的供数间隔；这不是基础应用常驻缓冲。

## 生命周期与 HAL

`hal_init()` 将音频停止并静音。bootloader 不链接音频，整个 FPGA 复位也默认 PA_EN=0。基础应用只报告音频状态，不自动播放。

| 调用 | 行为 |
| --- | --- |
| `hal_audio_write(pcm,frames,&written)` | PIO 非阻塞填写；未全部接收时 `HAL_BUSY`，written 仍有效 |
| `hal_audio_ring_begin(ring,capacity)` | 停止并清空旧流，配置 DDR ring，启用 DMA 预填但保持暂停/静音 |
| `hal_audio_ring_write(pcm,frames,&written)` | 非阻塞复制/提交，容量不足返回 `HAL_BUSY` 和进度 |
| `hal_audio_start()` | 开始消费当前 FIFO；不改变 mute 状态 |
| `hal_audio_mute(1)` | 关闭功放、从下一帧发送零；继续消费 PCM，时间线继续 |
| `hal_audio_pause()` | 关闭功放、停止消费、串行补零；DMA 可预填至 FIFO 满 |
| `hal_audio_stop()` | 关闭功放、清空 FIFO/统计/所有权，等待当前 DMA 请求返回，最多 100 ms |
| `hal_audio_get_info()` | 采样率、control、FIFO level、frames/played、underrun/overrun、fetched/wraps、errors/busy、amp、last_sample |

control 位依次为 run/DMA/mute。frames/played 在立体声帧开始时计数；run 但无数据时该帧发送零并计 underrun，不重复上次样本。mute 下有效数据仍计 played；暂停不计 frame 或 underrun。停止清除这些计数，先取状态再停止才能保留统计。

停止期间不撤销已发出的 Wishbone 请求，而是丢弃旧响应并等待 idle，防止迟到的 DDR 响应混入下一次播放。若停止超时，ring 必须继续保持有效，待再次停止成功或系统复位后才能释放。硬件 errors 的位 0/1/2 分别是地址/容量配置、producer 所有权、Wishbone bus error；开始新流时清除。

PA_EN 开关及外部 AC 耦合输入会产生偏置/衰减瞬态，当前不实现淡入淡出。模拟静音验收直接检查测试音频率被抑制，同时保留 DC、总 RMS 和交流 RMS；不能用包含 DC 的总 RMS 代表残留音调。

## 独立示例与验证

```powershell
. .\scripts\env.ps1
& $MiniPython scripts/build.py --synthesize
& $MiniPython scripts/build.py --app firmware/examples/audio_demo.c --output-dir build/m7-demo
& $MiniPython scripts/audio_verify.py --output-dir build/base --program --soak-seconds 300
# 无 Windows line-in 环境时可加 --skip-capture，报告仅覆盖数字部分。
& $MiniPython scripts/audio_capture.py --list
```

示例上电静音。`t` 验证 256 帧 PIO 后缺样补零；`d` 开始静音 DMA；`p/c/x` 暂停/恢复/停止；`u` 取消静音两秒后自动恢复；`r/f/s` 是 SD CRC/写换视频帧/状态。默认 DDS 下低幅度左音 750 Hz、右音 500 Hz，方便识别交换或串音。测试命令和大 ring 不进入基础 monitor。

Windows 录音工具使用 WinMM 选择 `Line In`，48 kHz PCM16 stereo；不会改变系统默认录音设备或音量。录音采样率来自 PC，不能据此声称 DAC 是 48 kHz。真实 DAC 速率来自序列器周期和测试音频率。`audio_verify.py` 在测量后保持静音，五分钟只做静音 DMA+只读 SD+视频并发，结束停止音频。

针对性仿真解码实际 BCK/WS/DIN 并检查声道/位序、零填充、mute、真实分频、ring wrap、所有权、单请求仲裁、停止丢弃迟到响应和 bus/range 错误。WinMM 的数字录音不替代 DAC 音质、功放负载、频响、THD 或主观听感测试；本轮不做这些测量。
