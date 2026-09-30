# 频率扫描结果

日期：2026-10-01。实验使用独立的 `build/frequency/` 输出目录；未通过 Gowin setup/hold 的位流没有下载。实验结束后开发板已恢复到已提交的 M4 60/120 MHz 稳定版本。

## 已验证的最高频率

| 部件或组合 | 最高已验证配置 | 结果 |
| --- | --- | --- |
| CPU、ROM/SRAM、Timer、UART 及片上总线（隔离 M0） | **86.4 MHz** | Gowin setup/hold 0/0；SRAM 下载后 UART 启动和 CPU 运算检查通过 |
| CPU、ROM/SRAM、Timer、UART 及片上总线（隔离 M0） | 90 MHz | setup 35 个，未下载 |
| CPU、ROM/SRAM、Timer、UART 及片上总线（隔离 M0） | 96 MHz | setup 335 个，未下载 |
| CPU、ROM/SRAM、Timer、UART 及片上总线（隔离 M0） | 120 MHz | setup 767 个，未下载 |
| DDR 控制器、共享 native 端口、视频 DMA、LCD（M4） | **62.4375 MHz sys / 124.875 MHz DDR CK** | setup/hold 0/0；启动、DDR 自检、5 次软件复位、301.6 秒 / 199 轮并发压力全部通过，欠载 0 |
| DDR 控制器、共享 native 端口、视频 DMA、LCD（M4） | 153 MHz sys / 306 MHz DDR CK，DLL-on CL6/CWL5 | setup 2360 个，未下载 |

这里的“最高”是本次扫描范围内经过综合、布局布线和上板验证的最高值，不是芯片的绝对频率上限。CPU 扫描在 86.4 和 90 MHz 之间还存在未测区间；86.4 MHz 是当前已确认可工作的最高点。Gowin PLL 对某些目标频率没有精确参数组合，例如 88 MHz 和 125 MHz，因此没有把近似频率冒充精确结果。

## DDR 解释

当前颗粒为 H5TQ1G63EFR-PBC。DLL-off 使用 CL6/CWL6，并按同系列 DLL-off 时序资料的 `tCK(DLL_OFF) >= 8 ns` 保守限制，把 DDR CK 上限约束在 125 MHz 以下；124.875 MHz 是本次 PLL 能生成、且仍在限制内的最高候选。它不是 H5TQ1G63EFR 的 DDR3-1600 标称上限，后者是更高频的 DLL-on speed-bin 条件，必须同时选择合法的 CL/CWL 和 tCK。

153 MHz sys / 306 MHz CK 的 DLL-on CL6/CWL5 试验首先被布局布线时序拒绝，因此没有进行上板测试。之前的 144 MHz CK 试验虽通过短测，但 DLL-off 周期为 6.944 ns，低于采用的 8 ns 保守限制；它不计入当前有效结果。

## 外设频率

频率扫描没有改变外部设备的协议速率。稳定版本和 62.4375 MHz 候选均使用 UART 115200 baud、SD 初始化 400 kHz、SD/SPI LCD 工作 6 MHz，以及 RGB LCD 9 MHz 像素时钟、约 59.94 Hz 刷新率。CPU/sys 提频不会自动提高这些协议速率；SPI 分频由 `CONFIG_CLOCK_FREQUENCY` 重新计算。

## 可复现实验

```powershell
. .\scripts\env.ps1
& $MiniPython .\scripts\build.py --stage m0 `
  --experiment-config .\build\frequency\cpu-86p4.json `
  --output-dir .\build\frequency\cpu-86p4 --synthesize
& $MiniPython .\scripts\board_test.py --stage m0 --program `
  --port COM4 --location 107569 `
  --output-dir .\build\frequency\cpu-86p4

& $MiniPython .\scripts\build.py --stage m4 `
  --experiment-config .\build\frequency\system-62p4375.json `
  --output-dir .\build\frequency\system-62p4375 --synthesize
& $MiniPython .\scripts\board_test.py --stage m4 --program `
  --port COM4 --location 107569 --soft-resets 5 `
  --output-dir .\build\frequency\system-62p4375
& $MiniPython .\scripts\stress_test.py --seconds 300 `
  --output-dir .\build\frequency\system-62p4375
```

CPU-only 的实验固件运行片上 SRAM 访问、整数运算和 Timer 计数检查；M4 候选运行现有的 DDR 逐字内存复制、SD 文件 CRC、SPI LCD 重绘、RGB LCD 换帧和整帧模和检查。实验过程不写 SD、不写 FPGA Flash。
