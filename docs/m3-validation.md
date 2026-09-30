# M3 RGB LCD 验证记录

日期：2026-09-30。范围为 480×272 并行 RGB LCD，HDMI 暂缓。

## 已完成

- 独立 9 MHz PLL；525×286 总时序、59.94006 Hz、HS/VS 负极性。
- 固定两个 DDR RGB565 帧槽，地址 0x47E00000 / 0x47E40000，stride 960。
- LiteDRAM native 128-bit read DMA，8 KiB 数据 FIFO，128→16-bit 转换及 sys→video CDC。
- 每个扫描周期只请求一帧；上一帧 last 消费/丢弃后才允许下一帧，垂直消隐处锁存帧选择。
- 缺数计数、黑色输出、按 last 丢弃旧帧和下一扫描边界恢复；关闭时排空已发起帧。
- 固件生成白色外框、竖向 RGB 三色带、灰阶渐变；两帧分别有青色/黄色角标。
- 新增 fbinfo / fbflip monitor 命令；保留 M2 的 SD/FatFs/SPI LCD。

## 构建和仿真证据

sim/test_video.py 正常和断流两种场景各运行三个完整扫描周期：
有效区每帧 130,560 像素，HS 低 41×286 个像素周期，VS 低 10×525 个像素周期；
正常场景无 underflow，注入一次缺像素场景记一次 underflow，旧帧完成丢弃后再次完成后续帧。
固件还检查完整帧消费 completed 计数增长、enable 回读，以及 100 ms 稳态窗口内 underflow 不增加，失败不会输出 M3 READY。
此仿真覆盖扫描和流恢复，不代替真实 DDR 仲裁或 LCD 电气验收。

Gowin 综合/PnR 完成，setup / hold violated endpoints 均为 0。
固件 32,440 字节 / 32 KiB ROM；Logic 7,459 / 20,736，Register 3,607 / 16,173，BSRAM 32 / 46。
bitstream SHA256：`c0a1385c9b08ca9a8f83b96e416b07192fa90aae069b0842a5a765de2a32eb73`。

初次报告的跨域 hold 路径为异步 FIFO Gray 指针和 enable 首级同步器；
后续两条 removal 路径为 sys reset 到 video 异步复位同步器。
constraints.py 从生成 RTL 确认七组 LCD CDC 首级（含诊断 test），精确豁免这些 D 引脚以及专用 lcd_video_async_reset 网络。
第二级同步器、同域数据路径、CPU/DDR 路径继续参与时序检查。未采用全时钟组 false path。
当前尚未取得具体面板 setup/hold 数据，因此内部时序收敛不能视为完整连接器输出时序验收。

## 上板状态

SRAM 下载已恢复并完成。版本 f97f1ea0 的启动、两次 CPU 软件复位均通过 DDR 模式、地址/数据位与 DDR C 代码/栈测试；SPI loopback、SD 挂载及 SPI LCD 重绘通过。
三次 fbflip 确认 active=1/0/1；显示运行期间 SD READ PASS: RVTEST00.BIN bytes=4096 CRC32=08040e1e。
完整帧消费计数持续增加，underflows 始终为 0。日志见 build/m3/hardware-validation.json 与 lcd-hardware-validation.json。

用户照片及反馈显示横向条纹/闪烁；RGB 顺序、边框、角标大体正确，但画面不能验收为正常。
数字流完整、内部 STA 收敛不代表 DDR 返回每个像素正确或 LCD 电气输出正常。
新增 fbpattern / fbmemory 对照模式：直接生成色带沿用相同 LCD 时序/引脚，隔离 DDR 像素来源。
用户随后确认：FPGA 直接生成图案正常稳定；切回本次下载后的 DDR 图案也正常；一次软件复位后仍正常。
原异常可能与复位有关，但目前不能稳定复现，尚未确认根因。新增 native DMA 整帧像素 32-bit 模和与固件绘图预期对比，启动检查与 fbcheck 使用该值；不称为 CRC。
最终带校验版本 c0a1385c：一次 SRAM 下载启动 + 五次软件复位均通过（每次启动整帧校验匹配）；
三次换帧与五次 fbcheck 均通过，frame0 模和 80d32504、frame1 模和 82c2e704；
同时 SD 只读 CRC 与 SPI LCD 重绘通过，underflows 持续为 0。用户确认最终版本黄色角标的 DDR 画面正常稳定，无先前条纹/闪烁。
本轮功能验收通过；历史异常根因尚未确认，不能仅凭未复现宣称彻底解决，M4 继续观察。未写 FPGA 配置 Flash、未执行 SD 写入。

此前发现并修复两处问题：32-bit 中间端口下 DMA 未完成整帧，改为控制器原生 128-bit 端口并新增 completed 验证；
video_stop / fbflip 的 io_ticks 增长计时差值方向错误，修正后排空和两次软件复位正常。

## 命令

```powershell
. .\scripts\env.ps1
& $MiniPython .\scripts\build.py --stage m3 --synthesize
& $MiniPython .\scripts\board_test.py --stage m3 --program --port COM4 --location 107569 --soft-resets 5
& $MiniPython .\sim\test_video.py
```

fbinfo 读取 active / busy / frames / completed / checksum / underflows；fbflip 请求切换到另一个固定槽并等待 active 确认，超时报告错误。
只读 SD 回归使用 `sdcheck RVTEST00.BIN`，不必再次创建文件。
画面故障排查中；30 分钟并发压力测试属 M4，尚未完成。
