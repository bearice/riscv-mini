# Nyan Cat BIOS demo

`firmware/examples/nyancat_demo.c` 是由 BIOS 引导的裸机程序，使用
[原版页面](https://www.nyan.cat/index.php?cat=original) 的 `original.gif` 和
`original.mp3`。它不是替换 BIOS 的独立 HAL 固件，不通过 `build.py --app` 构建。

## 画面和音乐

- RGB LCD 为 480×272 RGB565，背景色 `#003366`。程序绘制移动闪烁的白色星点和波动的六色彩虹。
- 原 GIF 为 272×168、12 帧、每帧 70 ms，画面位置为 `(104,52)`。转换器验证每个 8×8 像素块的颜色一致，保存完整的 34×21 调色板网格，没有插值或丢帧转换。
- 板上按 GIF 帧延迟播放，维护两个帧槽各自的旧画面，只更新变化的猫像素。较长的 DDR 写入之间轮询 BIOS，并在音频 FIFO 水位偏低时给 DMA 留出访问机会。
- BGM 离线转换为 48 kHz、有符号 16 位单声道 PCM，输出时复制到左右声道，完整音乐约 27.063 秒循环。软件转换增益为 0.22；板上无需 MP3 解码器。
- PCM 随 RPB 程序一次装入 DDR，播放过程中不反复读取 SD。应用拥有 32768 帧、128 KiB 的双声道 DMA 环形缓冲区。
- 每十秒或按 `s` 输出帧数、音乐循环数、最长画面更新耗时、播放/生产/读取帧数、FIFO 水位、欠载与错误。`max_render_ms` 从 `render()` 开始计时，直到 `VIDEO_PRESENT` 返回，包含绘图、BIOS 轮询、音频让步以及等待 LCD 实际换帧；它不是纯绘图或整屏填充时间。欠载计数用于诊断；硬件补零后继续播放，配置或总线错误才结束程序。

## 构建

准备当前 BIOS 和板级工具后，在仓库根目录执行：

```powershell
uv pip install --python .venv/Scripts/python.exe -r requirements-nyancat.txt
.venv/Scripts/python.exe scripts/nyancat_assets.py --output-dir build/nyancat
.venv/Scripts/python.exe scripts/bios_payload.py `
  --source firmware/examples/nyancat_demo.c `
  --include-dir build/nyancat `
  --extra-source build/nyancat/assets.S `
  --output-dir build/nyancat/payload
```

转换器缓存原 GIF/MP3，并生成调色板、帧延迟、PCM、汇编嵌入文件和来源/哈希清单。
原素材及生成的二进制不提交到 Git，素材权利属于原作者；构建时从指定网站取得。
转换只需主机上的 Pillow 与打包了 FFmpeg 的 imageio-ffmpeg，默认 BIOS 构建不需要这两个依赖。

启动一个只导出这个程序的 TFTP 服务，`--bind` 使用连接 Dock 的本机接口地址：

```powershell
.venv/Scripts/python.exe scripts/bios_tftp.py `
  --bind 169.254.25.153 --file build/nyancat/payload/BOOT.RPB --name NYANCAT.RPB
```

在 BIOS 终端输入：

```text
set server 169.254.25.153
fetch NYANCAT.RPB
boot sd NYANCAT.RPB
```

`fetch` 只新建文件，已有同名文件时会拒绝；再次构建的候选可以用
`boot net NYANCAT.RPB` 直接测试，或另选 SD 文件名保存。
程序同时包含音乐，约 2.61 MB；网络传输、SD 装载和镜像 CRC 检查需要一定时间。
只装载一次，运行期间不需要主机 TFTP 服务。

## 操作和 BIOS 服务

| 键 | 操作 |
| --- | --- |
| `q` | 停止音频并返回 BIOS 文字终端 |
| 空格 | 同时暂停/继续动画和音频 |
| `m` | 静音/取消静音，播放位置继续前进 |
| `s` | 串口状态 |
| `!` | 软件复位 |

需要当前 BIOS 的音频服务，见 [BIOS ABI](bios.md)。服务保留设备所有权；应用只提供
自己的环形缓冲区和 PCM 数据。BIOS 在程序返回后也停止音频 DMA，避免继续访问旧应用内存。
启动 ROM 和 FPGA 配置不需要改变。

主机绘图回归使用真实 demo 的增量渲染函数，连续检查 120 帧双缓冲结果与完整 GIF/彩虹
组合逐像素相同，并可启用 AddressSanitizer/UndefinedBehaviorSanitizer：

```sh
cc -fsanitize=address,undefined -Wall -Wextra -Werror -I build/nyancat \
  tests/nyancat_render_test.c -o build/nyancat/render-test
build/nyancat/render-test
```

它验证画面计算和内存边界，不验证 BIOS trap、真实 LCD、电气音频或 DMA 时序。
