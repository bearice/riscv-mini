# M7 PT8211 验证记录

日期：2026-10-01。前置提交 M6 `197fe605f908c7a4a12f8837fb7779e11e904e29`。实现音频序列器、512 帧 FIFO、DDR PCM ring DMA 和应用侧 HAL；默认系统静音，测试在独立 DDR 示例中完成。

## 配置与构建

| 项目 | 结果 |
| --- | --- |
| CPU/sys/Wishbone / DDR | 60 MHz / CK120 MHz，DLL-off CL6/CWL6 |
| 音频 | PT8211，PCM16 stereo，BCK1.5 MHz，46,875 Hz |
| 音频 FIFO | 512 帧、2 KiB、约10.9 ms；run 缺样补零 |
| DMA | 单 word Wishbone 只读 master，共用 DDR 桥；ring 软件 producer / DMA fetched 所有权 |
| 示例 ring | 32,768 帧、128 KiB、约699 ms，基础应用不分配该大缓冲 |
| ROM / SRAM / boot.bin | 8,192 / 8,192 / **6,568 字节**；boot 未链接音频 |
| app.bin / app.img / BSS | 25,204 / 25,252 / 6,272 字节；DDR 应用 |
| ABI | `81f318ae` |
| Logic / Register | 11,400 / 20,736；5,964 / 16,173 |
| BSRAM / rPLL | **19 / 46；2 / 4** |
| 最终 PnR | setup 0、hold 0 |
| SPI-SD 回退 | 生成/编译通过；本轮未重新 PnR 或切换实卡至 SPI |

相比 M6 增加一个 BSRAM，未增加 PLL。RGB LCD 9 MHz /480×272、SD 四位7.5 MHz、SPI LCD6 MHz、Flash10 MHz保持既定配置。

最终 `build/base/gateware/riscv_mini.fs` SHA256：`c1eea1ebced0734bede7a175c028cc27649d9b71565751dcb6722e6cc649cced`。

最终 `build/base/firmware/app.img` SHA256：`2d520dd1ff151452793930d7a2cb5e45880f7c76616b6efc01ef1cc1f9823ecb`。

五分钟与模拟录音在 `build/m7-base` bitstream（`d2353b9b16794d03af2b20fff54e2d9ed2e5b0ccb4033a341ea0237e316285b8`）和 UART 加载的 `audio_demo.c` 上完成。最终默认 `build/base` 使用相同 RTL/CSR/ABI 单独 PnR 后安装并做启动回归；没有把最终基础版再跑五分钟。

## 数字逻辑与上板

- `sim/test_audio.py` 解码实际 BCK/WS/DIN，检查右 WS=0、左 WS=1、16bit/MSB-first、声道和样本顺序、PIO 缺样零填充、mute。
- 实际分频仿真：BCK 上升沿间隔40 sys，WS完整周期1280 sys，即1.5 MHz/46,875 Hz。60 MHz只使用clock-enable，没有额外fabric clock。
- DMA 仿真：原始 PCM word 顺序、三槽ring回绕、producer/fetched 所有权、每word释放总线、停止中的迟到响应丢弃、拒绝越过帧缓冲边界、bus error状态与停止发新请求。
- 上板 PIO：写入256帧，played=`0x100`；随后有意耗尽，underrun=`0x294`，无overrun/error，PA_EN=0。缺样是此项的预期结果，不混入后续DMA流统计。
- 上板 DMA暂停：frames/played 停止增长，control=6（DMA/mute），FIFO512；恢复后继续增长、underrun=0。
- 三次停止/重新开始：停止后 busy、FIFO level、amp 均0，重新预填/播放无错误或欠载。
- DDR共享端口、SDCore/SD分频、五项镜像、两项programmer结果测试通过；Python语法和新增公共HAL C++头编译通过。

## 本机模拟录音

用户将 Dock 3.5 mm 输出接到本机 front-panel line-in。通过 Windows WinMM 选中 `Line In (Realtek(R) Audio)`，48 kHz PCM16 stereo录音；工具没有修改默认录音设备、输入音量或系统音频设置。PC录音的48 kHz不是DAC采样率。

测试示例默认静音，`u` 播放两秒低幅度测试音后自动静音。DAC PCM峰值1,024，左/右理论音调分别46875/64与46875/96 Hz：

| 结果 | 左声道 | 右声道 |
| --- | --- | --- |
| 捕获峰值频率 | **732.421875 Hz** | **488.28125 Hz** |
| 测试音幅度（ADC满幅比例） | 0.012347 | 0.012445 |
| 对另一测试音的分离 | **32.16 dB** | **31.43 dB** |
| 静音后测试频率抑制 | **63.90 dB** | **64.93 dB** |
| 削顶样本 | 0 | 0 |

由此检查完整 DAC/功放/连接线/PC输入路径的声道和测试频率。频率扫描分辨率为0.1 Hz，这些值是最匹配频点，不代表ppm级时钟精度。测试音分离包含整个录音链路，不能当作PT8211器件串音指标。

最初使用总RMS静音比，实际录音触发失败：静音窗总RMS约0.0086，有直流约−0.0075与缓慢衰减；而测试频率已被抑制。对同一WAV分窗投影区分偏置/瞬态和测试音，改为要求测试频率抑制至少40 dB，仍记录DC、总/交流RMS。`sim/test_audio_capture.py` 用存续DC复现旧比较的错误判断，并验证真正泄漏的音调会失败；再次实录通过。当前PA_EN切换存在偏置/衰减瞬态，没有淡入淡出或主观音质验收。

## 五分钟并发与恢复

**300.0 秒、132 轮**。静音音频DMA持续消费，CPU填充/交换DDR视频帧并只读`RVTEST00.BIN`：4,096字节，CRC32持续为`08040e1e`。

窗口首尾 played：287,429→14,253,723；fetched：287,941→14,254,235；ring wraps：8→435；RGB frames：17,385→35,244，completed：16,561→34,420。计数采样不覆盖窗口两端的等待间隔，不据此计算精确时钟或DDR吞吐。

整个流 audio underrun/overrun/errors、SD errors、LCD underflows、UART drops和unhandled IRQ均0。用户明确确认本轮两块屏幕均正常稳定。测试结束停止音频，随后恢复默认静音基础应用。

最终基础版Flash配置和应用安装、UART加载、Flash加载与两次软件复位自动Flash启动回归由`build/base/boot-verification.json`记录；启动检查明确要求audio control=4、level=0、underrun/errors=0、amp=0。Flash仅擦除/编程，无Verify或写后读回；bootloader镜像/传输CRC保持原设计。不追加断电检查或SD拔插验收。

## 原始证据与边界

忽略的`build/`保存本地原始产物：`m7-base/audio-verification.json`及`audio-verification-uart.log`、`audio-line-in.wav`、`m7-audio-verification-console.log`、`base/validation.json`、`m7-final-base-build-console.log`、`m7-final-flash-install-console.log`、`base/boot-verification.json`及`boot-verification-uart.log`。

未测试精确44.1/48 kHz、音质/THD/频响、功放负载、ISR/RTOS并发生产PCM、计数实际运行至32位回绕。本轮不宣称Ethernet或USB Host已实现。接口、所有权和复现实验见[音频](audio.md)。
