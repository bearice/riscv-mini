# Doom：L2 扩容测量与调色板输出方案

本次保留 CPU/sys 60 MHz、DDR 120 MHz、已归档 RV32IMA CPU 和完整外设，增加共享 writeback L2 的 16 KiB 构建选项。BIOS 本身没有改为链接 libc；Doom 继续静态链接 Newlib。

## 实板结果

16 KiB 候选：`a50a747-full-rv32ima-rom8k-l216k-doom-l2-16k-p2-c3eb0652-60616715`。

- PnR setup/hold 为 0/0。
- Logic 18946/20736，CLS 10118/10368，BSRAM 40/46。还有 6 个 BSRAM，但 CLS 仅剩 250，不能把存储余量等同于逻辑余量。
- 固件功能验收、USB、60 秒 soak 通过；三轮 `all` 共 66 个测量项目通过，结束后 BIOS、音频测试和停止检查通过。
- 三轮固定 Doom 测试为 22756、22740、22772 ms，中位数 22756 ms，相当于 5.273 fps。三轮结束后 LCD underflows 为 0。
- 普通 Doom 运行后，同一暂停帧的 RGB565 输出与引擎索引缓冲区经板上调色板转换逐字节比较：200 行、128000 字节全部一致。
- 16 KiB RTL 检查通过，包括 dirty writeback、复位、仲裁、CPU/video/DMA 混合访问、boot RAM 隔离和移交，以及生成 Verilog 的控制器测试。

固定工作量采用同一份 `build/doom-benchmark/BOOT.RPB`：DEMO1、singletics、leveltime 16 至 136、120 tick，屏蔽键盘输入。时间不含载荷和 WAD 加载、前期 warmup。这是固定 timedemo 的吞吐量，不代表所有游戏场景。此前普通 attract 循环的约 3.45 fps 与此工作量不同，不直接计算提升百分比。

### 对照的限制

为避免写 Flash，16 KiB 候选使用 8 KiB 集成 ROM。L2 容量属于镜像 ABI；旧 Flash XIP loader 不接受新容量的固件镜像，因此不能直接用旧 loader 搭配新配置。

同启动方式的 4 KiB ROM 对照经过三次布局尝试：place2/route2 有 732 个 setup 违例；place3/route2 有 44 条未布通网络；place4/route2 时序 0/0、BSRAM 36/46，但实板两次启动都未收到 UART 启动横幅，未进入 BIOS 验收或性能测试。时序通过不代表实板通过，这份对照不参与性能计算。

已验收的旧 4 KiB XIP 整机另做三轮同载荷测试，数据与身份记录见本报告配套 JSON。

| 项目 | 旧整机：4 KiB L2、XIP | 新候选：16 KiB L2、ROM8K |
| --- | ---: | ---: |
| 第 1 轮，120 tick | 28761 ms | 22756 ms |
| 第 2 轮，120 tick | 28828 ms | 22740 ms |
| 第 3 轮，120 tick | 28779 ms | 22772 ms |
| 中位数 | 28779 ms | 22756 ms |
| 固定 timedemo 吞吐量 | 4.170 fps | 5.273 fps |
| 三轮结束后累计 LCD underflows | 73 | 0 |

这两套整机配置的帧吞吐增加 **26.47%**，固定工作量耗时减少 **20.93%**。同时存在启动方式及生成固件身份差异，不能解释为完全隔离的 L2 容量收益。下溢计数包括复位后的 BIOS、载荷加载、等待和运行活动，并非仅统计 timedemo 的 120 tick。此前的单轮参考值 29479 ms 不混入上述三轮样本。

旧整机实际加载的是已验收 XIP bitstream，配套 BIOS 文件命令固件覆盖镜像来自 `20261011T144208227286Z-62dca7a-dirty-full-rv32ima-rom0k-l24k-doom-bios-filecmds2/firmware/app.img`。本次加载记录同时保存 bitstream 与实际上传固件的 SHA256；不能用原归档默认固件 hash 代替覆盖镜像。基线 BIOS benchmark 使用单独加载记录关联实际身份，原始 results.json 未伪造 build_identity。

双方 `all` 各三轮、66 个项目通过；旧整机 `cache` 三轮、24 个项目通过。结束后的 BIOS/音频检查通过。值得注意的整机差异：

| 项目 | 旧整机 | 新候选 | 变化 |
| --- | ---: | ---: | ---: |
| 1 MiB 连续写 | 8.565 MiB/s | 7.827 MiB/s | -8.6% |
| 4 KiB 连续写 | 22.076 MiB/s | 24.080 MiB/s | +9.1% |
| 1 MiB 连续读 | 8.713 MiB/s | 8.935 MiB/s | +2.5% |
| 1 MiB copy32 | 5.311 MiB/s | 5.383 MiB/s | +1.4% |

此表为整机观测值；精确中位数和全部对照项见 JSON。扩容并非所有负载都更快，不以 Doom 改善推断全系统一致加速，也不据此自动替换默认构建。

原有 BIOS `cache` 基准在执行前检查 `CONFIG_L2_SIZE == 4096`，所以拒绝了 16 KiB 候选，输出 `CACHE requires 2 KiB L1 / 4 KiB L2 configuration`。这是基准不支持该配置，既不是数据校验错误，也不是通过。其 +4096 地址间距只在 4 KiB L2 下代表 L2 冲突，后续扩展时必须保留明确的固定间距工作量，避免比较不同访问序列后归因于容量。共享 L2 的 16 位统计计数含显示等客户端，长窗口会回绕，不能据此计算 CPU 专属 miss rate。

## 调色板模式的收益

硬件增加 INDEX8 模式，保持 RGB565 为复位默认值及旧 app 的兼容路径。Doom 已有 8 位索引图像，适合这一模式。

| 项目 | RGB565 | INDEX8 |
| --- | ---: | ---: |
| 480×272 单帧 | 261120 B，255 KiB | 130560 B，127.5 KiB |
| 两个显示缓冲区 | 510 KiB | 255 KiB |
| 两套 256 色 RGB565 表 | 无 | 1024 B |
| 约 60 Hz 平均扫描读带宽 | 约 15.7 MB/s | 约 7.8 MB/s |
| 9 MHz 像素时钟的有效画面瞬时消费 | 18 MB/s | 9 MB/s |

MB/s 使用十进制；平均值按约 60 Hz 计算。当前 BIOS 驱动静态保留两块完整 RGB565 数组，因此仅改扫描格式不会释放这部分 DDR 分配。需要配套支持 app 提供对齐的显示缓冲区，按格式校验大小、边界及重叠，同时保留 BIOS 文本显示所需缓冲区。

显示缓冲区在外部 DDR，L2 和扫描 FIFO 使用片上资源。减半 VRAM 不会直接释放 BSRAM 给 L2；对于当前 128 MiB DDR，容量节约不是主要性能价值，扫描带宽、CPU 转换和 FIFO 的像素缓存容量才更直接。

建议先保留 Doom 的 320×200 引擎缓冲区，把索引行复制到 480 字节显示 stride，去掉 RGB565 查表转换。这样可以独立验证硬件格式与带宽收益。进一步直接渲染到硬件后备槽，才可能连同行复制一起去掉；这需要给 ylookup、patch/UI、wipe、屏幕复制等路径统一引入 pitch，并处理双槽 HUD 的增量更新。

### 硬件和 ABI 边界

1. 现有 16 位内存口及 FIFO 每字保存两个索引；在 video 时钟域拆成连续两个像素，再查询 RGB565 表。不要先扩成 RGB565 再入 FIFO，否则失去两倍像素缓存容量。
2. 用双调色板银行，只更新未使用的一套。framebuffer 槽、格式和调色板银行作为一次整帧提交，用跨时钟域请求/确认，在 DMA 和扫描整帧期间固定。还要处理 DMA 延迟开始、禁用、下溢丢弃及重启。
3. 同步 RAM 查表有延迟，DE、HSYNC、VSYNC 必须与像素同步延迟；最后一个字的 `last` 在第二个索引后消费，保留现有丢弃恢复语义。
4. `I_SetPalette` 的 gamma、受伤和拾取闪色必须与图像一起切换，否则可能一帧上下使用不同颜色。边框需要明确的黑色 viewport mask 等策略；不能假定索引 0 永远为黑色，也不能强行改颜色表第 0 项而影响游戏内像素。
5. BIOS service enum 采用末尾追加，不重排原编号。退出 app、加载失败及回到文本模式时恢复格式、缓冲区、wrap 和调色板相关状态。这里改变的是显示服务，不需要把 BIOS 改为依赖 libc。

两套表的逻辑容量仅 1 KiB，但映射到多少 BSRAM/逻辑需要综合确认，不能仅凭字节数保证资源结果。硬件 palette 输出和 BIOS 接口尚未实现，本报告给出的是方案及其验证边界。

## 其余加速方向

**先验证移位器，再单独验证乘法器。** 实际归档 CPU 的 generator.json 配置 `singleCycleShift false`、`singleCycleMulDiv false`，I/D cache 各 2 KiB。Doom 定点渲染有大量移位和乘法。当前逐位移位器值得测试桶形移位器；硬件乘法选项不意味着除法也是单周期。每个候选都需要重新检查 CLS、时序和实板表现。

**减少像素循环里的全局指针重读。** `R_DrawColumn`、`R_DrawSpan` 内部每次像素写入后再次加载 source/colormap 全局变量，C 的 byte store 别名规则阻止编译器提升这些读取。可在进入循环时把稳定指针缓存到局部变量，检查反汇编，再用固定 timedemo 和逐像素画面比较验证。不要以全局禁用别名规则替代局部修正。

**缓存容量与 framebuffer pitch 分开测。** 16 字节缓存行、direct map 下，320 字节的垂直列步长在 4 KiB L2 每 64 行重复索引，在 16 KiB 每 256 行重复。336 字节 pitch 也可能缓解 4 KiB 下的冲突，但必须修改所有相关读写路径；直接改 SCREENWIDTH 会改变引擎逻辑和资产布局。VexRiscv L1 D-cache 是 write-through，共享 L2 是 writeback；扩 L2 不会消除每像素 CPU/L1/L2 事务开销。

**显示优化不能替代主循环优化。** 之前混合 attract 窗口中 blit 约 29 ms/帧、draw/present 约 9 ms/帧，仍有大量游戏和渲染工作。`tick` 包含这些子阶段，不能累加嵌套计时，也不能把整个 tick 称为纯栅格化。调色板能降低带宽和转换成本，但不能仅据此承诺帧率翻倍。

## 后续 INDEX8 验收

用全部 256 项调色板、颜色 ramp、字节顺序、首末像素和行/帧边界检查查表及时序；测试整帧 palette/slot 切换、enable/disable、FIFO stall、下溢与重启、旧 RGB565 app 和 BIOS 文本恢复。再运行实际配置 RTL、PnR 0/0、匹配镜像实板验收、同一 timedemo、源/输出画面比较和下溢计数差值。

## 证据

- 候选目录：`build/runs/20261012T142723996204Z-a50a747-dirty-full-rv32ima-rom8k-l216k-doom-l2-16k-p2`。
- 候选板测：`build/operations/20261012T143306320961Z-verify/`；重新加载成功：`build/operations/20261012T145655015689Z-run/`。
- 三轮 Doom：`build/doom-perf-l216k-round1.log` 至 `round3.log`；普通画面：`build/doom-l216k-matched-frame/`、`build/doom-l216k-matched-diff.log`。
- 旧整机实际加载记录：`build/operations/20261012T150037568025Z-run/result.json`；Doom 三轮：`build/doom-perf-l24k-xip-round1.log` 至 `round3.log`；基准：`build/doom-perf/l24k-xip-all/` 与 `l24k-xip-cache/`。
- `all` 原始数据：`build/doom-perf/l216k-all/results.json`；不支持的 cache 测试：`build/doom-perf-l216k-cache.log`。
- RTL：`build/doom-l2-16k-sim.log`、`build/doom-l2-boot-ram.log`、`build/doom-l2-16k-emitted-test.log`。
- 失败 ROM 对照板测：`build/operations/20261012T145357043297Z-verify/` 和 `build/operations/20261012T145732950196Z-run/`。
- CPU 参数：`build/archives/v0.9.1-30a935686925/full-rv32ima-rom0k-l24k/inputs/cpu/generator.json`。
- 显示实现入口：`gateware/video.py`、`firmware/drivers/video.c`、`firmware/bios/include/bios.h`、`firmware/apps/doom/i_video.c`。
- 结束后恢复候选：`build/operations/20261012T150549423722Z-run/result.json`；普通 Doom 重新运行：`build/doom-l216k-final-live.log`。

本次 SRAM 实验没有写 Flash，也没有把实验候选设为默认 `current`。改动与性能报告尚未提交。
