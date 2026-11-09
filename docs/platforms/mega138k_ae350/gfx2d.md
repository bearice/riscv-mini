# AE350 2D 像素处理流水线（GFX2D）规格与集成

> 适用机器：`mega138k_ae350`。状态：**一期（Step 1–4b）已全链路落地并完成板级实机验收（BIOS TTY / Linux fbdev / DRM / 自测套件通过）**。
> 本文档是架构设计、寄存器合同与实机验收记录；实现细节与演进以代码和构建回执为准。

## 1. 目标与非目标

目标：在 FPGA fabric 内实现 2D 像素处理流水线，给 framebuffer 驱动加速渲染，
优先加速 BIOS / U-Boot / Linux FB 的字符渲染（清屏、滚动、逐字绘制）。

一期（Phase 1）只做无 Alpha、无缩放的块操作：

| 操作 | 编码 `OP` | 说明 |
| --- | --- | --- |
| `FILL_RECT` | 0 | 纯色矩形填充 |
| `COPY_AREA` | 1 | 矩形区域复制（支持 stride、重叠） |
| `IMAGE_BLIT` | 2 | 1bpp 位图展开为 RGB565（字符核心） |

二期（后续）：Alpha Blend、Scale/StretchBlit。见 §7，不在一期范围内。

非目标：一期不做 3D、不做任意旋转、不做 YUV 转换、不改变 HDMI scanout 的时序与 FIFO。

## 2. 为什么不用 AE350 自带 DMAC 做 stride

AE350 硬核 DMAC（ATCDMAC300，见 [DMA](dma.md) 与 `firmware/bsp/mega138k_ae350/dma/ae350_dma.c`）：

- 仅支持 1D 线性传输：递增（mode 0）/ 递减（mode 1）/ 固定（mode 2），宽度 byte/halfword/word。
- 无 2D / stride / scatter 语义。`ae350_dma_submit` 的 `span()` 只校验线性区间。
- 逐行提交 DMA 只能用链表描述符（LLP）逐行描述，每行一次描述符开销 + 一次
  cache sync，行宽越小效率越低；字符渲染（8×16）每字数十次提交，收益为负。

结论：**stride 搬运必须在 fabric 内做专用 2D 引擎**，DMA 只保留给线性大块拷贝。

## 3. 架构

```
CPU (APB, sys 50MHz)
  │  寄存器：描述符 + START
  ▼
Gfx2D (sys 域 APB 寄存器 + DDR 域引擎)
  │  DDR3UI 256-bit / 32B beat（后续步骤接入）
  ▼
DDRMemoryArbiter（新增 gfx2d 端口，与 CPU/Video/Ethernet/USB 轮询）
  │
  ▼
DDR3（100MHz UI）
```

- 控制面：APB MMIO，挂 `AE350Fabric`，页面 `GFX2D_BASE = 0xF80C0000`（4KiB）。
- 数据面（Step 2+）：独立 `DDR3UI` 端口接入 `DDRMemoryArbiter`，不占用 CPU 数据总线。
- 边缘处理：利用 DDR UI 的 `wr_data_mask`（bit=1 表示该字节不写）做首/末部分块写，
  一期纯填充/对齐复制可零 RMW；非对齐 Blit/Copy 的目标 RMW 在引擎内用 256-bit 缓冲完成。
- 中断：`EXT_INT[10]`（当前 fabric 中 10–15 为空），锁存 + 屏蔽 + W1C，仿 HPD 中断模式。

### 地址与设备表

| 项 | 值 |
| --- | --- |
| `GFX2D_BASE` | `0xF80C0000` |
| `GFX2D_ID` | `0xAE350601` |
| `ABI` | `0x00010000`（major 1） |
| `CAP` bit0/1/2 | FILL_RECT / COPY_AREA / IMAGE_BLIT（骨架阶段置位表示寄存器面存在，能力以 Step 3 验收为准） |
| `CAP` bit3/4/5 | RGB565 / RGB888 / XRGB8888 |
| `EXT_INT_GFX2D` | 10 |
| SYSINFO | 新增一条 32B device record，`DEVICE_COUNT` 11→12，`SYSINFO_CAP` 新增 `CAP_GFX2D` |

## 4. 寄存器合同（APB，sys 域）

偏移唯一定义：`gateware/machines/mega138k_ae350/address_map.py: GFX2D_REGS`，
RTL 与生成 C 头共用。首 32 字节遵循平台公共控制头。

| 偏移 | 寄存器 | 语义 |
| --- | --- | --- |
| 0x00 | ID | 只读 `0xAE350601` |
| 0x04 | ABI | 只读 `0x00010000` |
| 0x08 | CAP | 只读能力位 |
| 0x0C | CONTROL | bit0 START（写 1 触发，读为 0）；bit1 IRQ_ENABLE |
| 0x10 | STATUS | bit0 BUSY；bit1 DONE（W1C）；bit2 ERROR（W1C） |
| 0x14 | IRQ_STATUS | bit0 DONE 事件锁存（W1C） |
| 0x18 | IRQ_ENABLE | bit0 事件屏蔽 |
| 0x1C | LAST_ERROR | 最近错误码（0=ERR_NONE，1=ERR_UNSUPPORTED，2=ERR_BUSY，3=ERR_PARAM，4=ERR_ADDR，5=ERR_TIMEOUT） |
| 0x20 | OP | 0=FILL_RECT，1=COPY_AREA，2=IMAGE_BLIT |
| 0x24 | SRC_ADDR | 源基址（DDR 物理地址） |
| 0x28 | DST_ADDR | 目标基址 |
| 0x2C | SRC_STRIDE | 源每行字节数（32B 对齐，见 §5） |
| 0x30 | DST_STRIDE | 目标每行字节数 |
| 0x34 | WIDTH | 宽度（像素） |
| 0x38 | HEIGHT | 高度（行） |
| 0x3C | FG_COLOR | 填充色 / Blit 前景色 |
| 0x40 | BG_COLOR | Blit 背景色 |
| 0x44 | BLIT_SRC_WB | Blit 源每行字节数（1bpp packed） |
| 0x48 | COMMANDS | 已接受命令计数 |
| 0x4C | ERRORS | 累计错误计数 |
| 0x50 | CLEAR_ERRORS | 写 1 清除 ERROR/DONE/LAST_ERROR |
| 0x54 | FORMAT | 像素格式（0=RGB565, 1=RGB888, 2=XRGB8888, 3=XRGB1555） |

约束：

- 描述符寄存器在 `BUSY=1` 时写禁止（写被拒绝并置 `ERRORS`），避免半更新描述符被引擎采样。
- `START` 只在描述符完整提交后有效；骨架阶段（Step 1）`START` 直接置 `ERROR=UNSUPPORTED`，
  不产生任何 DDR 流量。Step 2 起改为真正的引擎启动。
- 所有 W1C/锁存语义同 HPD 中断：新事件优先于同拍清除。

## 5. 地址与格式约束（硬件与驱动合同）

- `SRC_ADDR/DST_ADDR`：DDR aperture 内。
  - `FILL_RECT`：`DST_ADDR` 支持任意字节对齐。非对齐起始由首拍 `first_mask` 处理；奇地址起始由硬件旋转像素模式（`pat_odd`）保证跨 32B beat 边界像素相位无零填充损坏。
  - `IMAGE_BLIT`：`DST_ADDR` 要求像素对齐（偶字节地址），行内字节偏移在硬件中吸收为 `doff/2` 个 dummy 像素；`SRC_ADDR` 支持任意字节对齐。
  - `COPY_AREA`：要求 `(src % 32) == (dst % 32)` 且 `(src_stride % 32) == (dst_stride % 32)`。
- `SRC_STRIDE/DST_STRIDE`：`>= WIDTH * bpp`，`< 64KiB`，整帧不越 aperture。
- Blit 源为 1bpp packed，`BLIT_SRC_WB >= ceil(WIDTH/8)`；展开按字节内 MSB-first（与 `font5x7` 一致）。
- 颜色格式一期固定 RGB565（与 BIOS TTY 一致）；RGB888/XRGB8888 在引擎支持后开放。
- **重叠处理契约**：
  - **垂直重叠（下移/跨行）**：当源与目标在同一缓冲且垂直下移（`SRC_ADDR < DST_ADDR` 且 `dy > sy`）且步长相等（`src_stride == dst_stride`）时，硬件状态机置 `dir_down=1`，自动从末行向首行反向迭代（row `height-1` 到 0），避免行间覆盖；
  - **水平重叠（同扫描线右移）**：当在同一扫描线内向右复制（`sy == dy` 且 `sx < dx` 重叠）时，硬件每行内部 beat 递增传输（0 到 N-1），前序写入会覆盖后序未读源数据。**硬件契约**：硬件在 `INIT3` 静态校验中检测同扫描线右移重叠 `(src_addr < dst_addr) & ((dst_addr - src_addr) < row_bytes)`，直接拒绝并置 `ERR_PARAM`；**驱动契约**：软件驱动（Linux `ae350_fb_copyarea` 等）对 `sy == dy && sx < dx && sx + width > dx` 识别为不可硬件加速场景，自动回退至 `sys_copyarea` 软件处理。
  - **不等步长重叠拒绝契约**：当 `src_stride != dst_stride` 时，两区域各行间距不同，无法保证单一前向或反向遍历避免行间覆盖（例如 src 0x2000 stride 128, dst 0x2020 stride 32, w 16, h 4 逆向遍历会提前覆写尚未读取的中间行）。**硬件契约**：在 `INIT` 计算内存跨度 `src_span = (height - 1) * src_stride + row_bytes` 与 `dst_span = (height - 1) * dst_stride + row_bytes`，在 `INIT2` 检测重叠 `(src_stride != dst_stride) & (src_addr < dst_addr + dst_span) & (dst_addr < src_addr + src_span)` 并在 `INIT3` 拒绝并置 `ERR_PARAM`；同时仅在 `src_stride == dst_stride` 时允许启用 `dir_down` 逆序；**驱动与 BSP 契约**：BSP `ae350_gfx2d_copy_area` 预判不等步长重叠并返回 `AE350_GFX2D_PARAM`。
- **自定义视频模式扫描行宽契约**：`VideoFrameReader` DDR 突发以 32 字节（256 位，`blocks_per_row = row_bytes >> 5`）整块读取，行尾不足 32 字节部分会被截断并导致后续所有行产生累积偏移与像素错位。**硬件契约**：硬件在自定义模式校验 `cus_bad` 中强制检查 `(cus_row_bytes[:5] != 0)`，拒绝行有效字节不是 32 字节整数倍的自定义几何模式（RGB565 下宽度必须为 16 的倍数）；**驱动与 BSP 契约**：BSP `ae350_video_set_custom_mode`、Linux DRM `ae350_drm_mode_valid` 与 fbdev 校验均强制拒绝非 32 字节对齐的自定义行宽。

## 6. 分阶段落地

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| **Step 1（本提交）** | 计划文档；`peripherals/gfx2d.py` APB 骨架（只响应 ID/ABI/CAP/寄存器读写，START 返回 UNSUPPORTED）；`address_map.py` 地址/ID/寄存器/设备表；`fabric.py` APB decode + `EXT_INT[10]`；`soc.py` 实例化。**不接 DDR，无 PnR 风险增量之外的改动。** | `mini.py build gateware` PnR 通过（`--pnr-mode full`，run `20261105T204542678685Z-build-gfx2d-step1-skeleton-irqreg-81fdd7`；setup slack +0.002ns，余量极小，Step 2 改动需复核时序）；host 测试读 ID/ABI/CAP；SYSINFO 设备数 12。 |
| **Step 2（本提交）** | DDR 数据面：`DDRMemoryArbiter` 新增 gfx2d 端口；引擎状态机 FILL_RECT（mask 写、每行 first/last mask 预计算、引擎输出寄存器化）；BSP `ae350_gfx2d_fill_rect`（含 cache sync） | gateware PnR 通过（`--pnr-mode full`，run `20261106T062458439090Z-build-gfx2d-step2-fill-timingfix6-6a7acd`；setup slack +0.036ns）；host 测试对齐/非对齐填充逐像素校验通过；BSP 单文件编译通过。诊断 suite 逐像素校验待 Step 3 板级验收时一并覆盖。 |
| **Step 3（本提交）** | COPY_AREA（stride + 重叠，`src<dst` 时逆序逐 beat 复制；要求 `(src%32)==(dst%32)` 且 `(src_stride%32)==(dst_stride%32)`，同 FB 滚动天然满足）与 IMAGE_BLIT（1bpp MSB-first 位流→RGB565 展开；dst 须像素对齐即偶地址/偶 stride，行内字节偏移吸收为 `doff/2` 个 dummy 像素；源 beat 按行跨 beat 取字节） | gateware PnR 通过（`--pnr-mode full`，run `20261106T090149635451Z-build-gfx2d-step3-copy-blit-b59f33`；setup slack +0.003ns，**余量已耗尽，Step 4 前必须做时序加固**）；host 测试 COPY 基本+重叠下移、BLIT 8×2+非对齐 16×1 通过；BSP `ae350_gfx2d_copy_area`/`ae350_gfx2d_image_blit` 编译通过。板级诊断 suite 覆盖重叠/逐字待 Step 4 实板验收。 |
| **Step 3 板级修复（本提交）** | 实板发现 IMAGE_BLIT 源字节偏移 ≥4 读错字节：根因是 `(src_byte_off[0:5] << 3)` 在 Migen 生成的 Verilog 中被截断为 5 位（移位结果宽度=左操作数宽度），硬件按 `(off<<3)%32` 移位，而 Python 仿真语义正确故 host 测试漏检。修复：移位量改为显式 8 位 `Cat(Constant(0,3), src_byte_off[0:5])`（off*8）；同时做时序加固：INIT 拆为 INIT/INIT2/INIT3（限值/增量/校验各一拍，`dst_stride<row_bytes` 比较预注册到 INIT2）、ROW_SETUP 拆为范围检查与 ROW_PASCAL 两拍。host 测试新增源偏移 16 的 BLIT 回归用例。 | gateware PnR 通过（`--pnr-mode full`，run `20261107T074146873302Z-build-gfx2d-blit-rowsetup-split-bed245`；setup slack +0.001ns，GFX2D 内最差 +0.009ns）；实板（同 run bitstream + dbg4 payload，run `20261107T080619590330Z-board-ae350-upload`）：8×1/16×1 BLIT 逐字正确；完整 suite（run `20261107T080723432810Z-board-ae350-upload`）stage=`0xC3100005` 全过（FILL/COPY/BLIT）。 |
| Step 4 | BIOS TTY 接入（`ae350_tty_flush` 走 blit/fill）；Linux fbdev `fb_fillrect/fb_copyarea/fb_imageblit`；U-Boot LCD 加速 | TTY 帧时间对比、Linux 控制台、视频并发压力 |
| **Step 4a TTY 板级验证（本提交）** | `ae350_tty_flush` 接入 GFX2D：启动屏填 + 每 cell 单次 `IMAGE_BLIT(8×16)`（8×16 整格打包：首尾行为背景、字形 2 倍竖直展开于 (1,1)，`src_wb=1`，字形缓冲放 BSS/DDR 静态区），`tty_gfx2d_ready()` 探针缓存，失败回退 CPU 路径。实板调试发现两个真 bug：(1) **取指饿死**：bootloader 将 `mcache_ctl=0`（L1 I/D cache 全关），紧轮询 STATUS 时引擎高速写 DDR，每条指令都走 DDR 取指，APB 轮询+引擎写使取指超时报 illegal instruction（`mcause=0x2,mtval=0`）；隔离开关 cache 即过。修：TTY 诊断按 BIOS（`services.c: csrw 0x7cc,23; csrs 0x7ca,3`）使能 I+D cache；BSP `ae350_gfx2d_wait` 轮询间加 24 周期退避作无 cache 环境的防御。(2) 历史遗留：`src_wb` 误传总值 28 而非每行 2 字节（引擎按 stride 28 读行，字形错乱）。另修复测试侧 slot 翻转误读（present 后 active 切换 FB0/FB1）。 | gateware 沿用 `20261107T074146873302Z`（bed245）；TTY 诊断实板通过（run `20261107T151131422238Z-board-ae350-upload`，stage=`0xC3200030`，'A'/'B' bad=0/0，ERRORS=0，COMMANDS=0x12c2）；GFX2D suite 回归通过（run `20261107T151047802650Z-board-ae350-upload`，stage=`0xC3100005`）。 |
| **Step 4a 性能优化（本提交）** | 实测 GFX2D 全屏重绘（2400 cell）比 CPU 路径慢（41.3M vs 34.4M cycles），瓶颈是单 op 固定开销：优化后 38.1M cycles（CPU 34.4M，接近持平）：(a) BSP probe 结果缓存（此前每 op 两次 APB 读）；(b) 按行 sync 只清触及的 cache line（cell 此前每 op 清 ~600 行→~30 行），blit 源改为单区 sync；(c) 描述符影子 diff（每 op APB 写 10→~4）；(d) 每 cell 两 op（fill+blit）合并为单次 8×16 blit。引擎本身不慢，DDR 单 beat 握手+CDC 延迟占主导；cell 级 GFX2D≈CPU，真正的加速价值在整屏 fill、COPY_AREA 滚动（单 op 搬 10KB≈30× CPU）等大 rect 操作。 | verify 诊断（2400 cell 逐像素）0 坏；suite 回归 `20261107T151047802650Z` stage=`0xC3100005`（亦验证 diff 跨 op 切换正确）；perf 实测 run `20261107T150920276059Z`：CPU 0x20b485a / GFX2D 0x245b199 cycles（800MHz）。 |
| **Step 4a TTY 滚动优化（本提交）** | TTY flush 增加滚动检测：grid 在 DECSTBM 区上移 k 行（k=1..8）时，用一次 `COPY_AREA` 搬移像素（640×k*16，src>dst 前向迭代，引擎重叠安全），同步移位 `drawn[]`，diff 只重绘底部 k 行与光标——单行滚动从 ~2400 次 blit 降为 1 次 copy + ~80 次 blit。检测时 REVERSE 位归一化比较（drawn 存的是 post-reverse 格）；未命中则回退全量重绘。TTY 诊断改为同槽位两次 flush 结构使 copy 真正 engage，滚动后逐格像素校验通过。 | TTY 诊断实板通过（run `20261108T015409013435Z-board-ae350-upload`，stage=`0xC3200040`，全部 bad=0，ERRORS=0）；引擎/suite 不变（gateware 沿用 bed245）。 |
| **Step 4b Linux fbdev 接入（本提交）** | 新内核驱动 `firmware/linux/riscv_mini_ae350_fb.c`（fbdev，`riscv-mini,ae350-video`，RGB565/XRGB1555 固定模式+CUS 自定义模式，双缓冲单次 `frame_bytes*2` 连续分配支持 mmap，GFX2D `fb_fillrect/fb_copyarea/fb_imageblit` 加速——fbcon 坐标按虚拟屏空间 `y/height` 映射 slot、blit 源经 DDR scratch、`fb_sync`/present 用 S/U cache CSR 批量 clean）+ 共享头 `riscv_mini_ae350_regs.h`（寄存器/PLL 搜索/commit/cache-clean/GFX2D 助手）+ `riscv_mini_ae350_drm.c`（simple-pipe DRM + GEM DMA 拷贝 + shmem fbdev emulation，`riscv-mini,ae350-video-drm`）；`install.py` 安装 `FB_AE350_VIDEO`/`DRM_AE350_VIDEO`；DTS `video0` 启用（`status=okay`）并加 `consoleblank=0`；BIOS `services.c` 置 `CCTL_SUEN`（`csrs 0x7ca,0x103`）使 S 模式可用 `ucctlbeginaddr/ucctlcommand(0x80b/0x80c)`；init.c fb 自测动态几何。实板修复两个启动 panic：(1) S 模式用 M-mode cache CSR → illegal instruction；(2) VA 型 cache clean 命令传 DMA 物理地址 → load/store fault，须传 CPU 虚拟地址。 | Linux 6.12.112 构建通过（run `20261108T161708343609Z-build-gfx2d-ae350-fbfix-33ec64`）；实板启动到 shell（run `20261108T165430470478Z-board-ae350-linux`，`fb0: 640x480 RGB565 double-buffered (gfx2d accel)`，fbcon 80x30）；实板串口 `fb` 自测 `SELFTEST fb0 RGB565/mmap/readback/DMA/blank PASS`；GFX2D 加速确认在用（COMMANDS=0x1a0a，ERRORS=0）；MS2109 采集卡捕获到 Linux 绘制的 RGB565 红/绿/蓝三分竖条（mmap 写入 + cache clean 路径打通）。 |
| **Step 4c PR 审查缺陷修复（本提交）** | 针对 PR #3 代码审查发现进行软硬件防御与缺陷修复：(1) `geo_sum` 扩宽为 7 位，`beats_w` 计算引入完整高位商 `rb_hi5 + geo_sum[5:7]`，防止行跨 beat 时进位截断导致 beat 计数下溢；(2) `C_WSEND` 跨 beat 转移分支中补全 `NextValue(beat_byte, beat_byte + 32)`，修复多 beat COPY_AREA 目标写地址停滞在首块的 bug；(3) `wr_pattern` 由左移 8 位（补 0 破坏边界像素）改为奇偶字节旋转（`pat_odd = Cat(fg[8:16], fg[0:8])`），修复奇目标地址填充跨 32B 边界像素损坏；(4) 同扫描线右移重叠（`sy==dy && sx<dx`）：硬件 `INIT3` 增加 `(src_addr < dst_addr) & ((dst_addr - src_addr) < row_bytes)` 检测并返回 `ERR_PARAM`，驱动层预先检测并回退 `sys_copyarea`；(5) Linux fbdev 中颜色值与调色板索引映射：`pseudo_palette` 转换，`fb_fillrect` 仅对 `ROP_COPY` 加速（`ROP_XOR` 回退 `sys_fillrect`）；(6) DRM pipe disable 时先通过 `ae350_video_enable(drm->csr, 0)` 确认硬件 scanout 完全停止后再释放 DMA 缓冲区；(7) video 自定义模式在输出关闭停止时允许安全重写寄存器。 | Migen 仿真回归全过（`tests/mega138k_ae350/gfx2d_host_test.py` 覆盖多 beat 拷贝、进位溢出、奇地址旋转、右移重叠拒绝）；C HAL 测试全过；Linux 6.12.112 构建通过；实机验收启动并通过自测套件。 |
| **Step 4d PR 审查第 3 轮修复（本提交）** | 针对 PR #3 第 3 轮代码审查发现缺陷修复：(1) 自定义模式 32 字节行对齐：VideoFrameReader 按 32 字节突发读取，行宽不为 32 字节倍数时截断。门级 `cus_bad` 增加 `(cus_row_bytes[:5] != 0)`，BSP 与 Linux DRM/fbdev 校验同步拒绝非 32 字节对齐的自定义行宽；(2) 不等步长重叠安全：当 `src_stride != dst_stride` 时，单一正/反向迭代无法保证避免行间覆盖；硬件限制 `dir_down` 仅在等步长下生效，`INIT2` 增加 `unequal_stride_overlap` 计算并在 `INIT3` 拒绝并置 `ERR_PARAM`，BSP 同步增加参数校验；(3) DRM 模式完全匹配：`ae350_drm_match_mode` 对几何尺寸、总周期、前后沿及同步脉冲宽度、极性与像素时钟（1kHz 容差内）进行完整比对，避免参数不同的自定义模式被错误降级为固定时序模式。 | Migen 仿真回归全过（覆盖不等步长重叠拒绝用例）；C HAL 测试全过；Linux 6.12.112 构建通过；实机验收启动并通过自测套件。 |
| **Step 4e PR 审查第 4 轮修复（本提交）** | 针对 PR #3 第 4 轮代码审查发现缺陷修复：(1) fbdev 兼容 GEM DMA：由 `drm_fbdev_shmem_setup` 改为 DMA 专用的 `drm_fbdev_dma_setup`，避免 GEM 对象强转为 shmem 导致野指针与越界访问；(2) 格式转换状态补全：`ae350_drm_copy_fb` 为 `drm_fb_blit` 补全 `drm_format_conv_state` 初始化与释放，避免空指针解引用，并在 `pipe_check` 中对格式或尺寸变化置 `mode_changed = true` 强制完整 modeset；(3) 脏矩形刷新机制：改用 `drm_gem_fb_create_with_dirty` 并启用 `drm_plane_enable_fb_damage_clips`，保证控制台 damage 与用户空间 dirty 正常触发 plane update；(4) 帧缓冲偏移量校正：通过 `drm_gem_fb_vmap` 获取 `data` 映射地址传入 `drm_fb_blit`，正确处理 ADDFB2 非零偏移。 | Linux 6.12.112 构建通过；实机验收启动并通过自测套件。 |
| **Step 4f PR 审查第 5 轮修复（本提交）** | 针对 PR #3 第 5 轮代码审查发现缺陷修复：(1) 自定义模式像素深度对齐修复：`gateware/video.py` 的 `cus_bpp` 直接采用 `video_modes.py: BYTES_PER_PIXEL = (2, 3, 4, 2)`（RGB565=2, RGB888=3, XRGB8888=4, XRGB1555=2），并同步修正 BSP 与 Linux 驱动中的 `bpp_table` 映射；(2) probe 失败停止 scanout：`ae350_fb_probe` 在注册/创建文件失败分支中，先调用 `ae350_video_enable(fb->csr, 0)` 确认硬件 scanout 完全停止后再释放 DMA 内存，若停机超时则保留缓冲区，防止向系统释放仍在被硬件 DMA 读取的内存；(3) fbdev 垂直平移公告：在 `info->fix.ypanstep = 1` 声明垂直平移能力，并在 `ae350_fb_pan_display` 中严格限制 `yoffset == 0 || yoffset == fb->height`，允许通过 `FBIOPAN_DISPLAY` 切换双缓冲第二帧。 | Linux 6.12.112 构建通过；实机验收启动并通过自测套件。 |
| **Step 4g PR 审查第 6 轮修复（本提交）** | 针对 PR #3 第 6 轮代码审查发现缺陷修复：(1) modeset disable 到 enable 期间保护 plane update：在 `ae350_drm_pipe_disable` 中清空 `drm->width/drm->height`，并在 `ae350_drm_pipe_update` 与 `ae350_drm_copy_fb` 中增加 `drm->cpu` 空指针防护，防止 atomic commit tail 在关闭与启用之间调用 plane update 触发 NULL 目标指针解引用；(2) DRM 平面源坐标视口应用：将 `plane_state` 传入 `ae350_drm_copy_fb`，计算 `src_x/src_y/src_w/src_h` 视口并作为 clip 传给 `drm_fb_blit`，在 `pipe_check` 中拒绝非整数视口坐标并将尺寸判断对齐 CRTC 模式而非 FB 尺寸，正确支持大于屏幕的 FB 视口渲染。 | Linux 6.12.112 构建通过；实机验收启动并通过自测套件。 |
| **Step 4h PR 审查第 7 轮修复（本提交）** | 针对 PR #3 第 7 轮代码审查发现缺陷修复：(1) 格式敏感的行对齐校验：`ae350_drm_mode_valid` 将宽度对齐判定放宽为 `((mode->hdisplay * 4) & 31) == 0`，避免过早拒绝 XRGB8888 格式下的有效几何；`ae350_drm_pipe_check` 依据原子提交中实际选择的像素格式校验 `((crtc_state->mode.hdisplay * bpp) & 31) == 0`，防止 RGB888（3 BPP）等非 32 字节对齐行在 commit tail 硬件写入阶段失败；(2) DRM 平台驱动卸载与关机清理：在 `ae350_drm_probe` 中设置 `platform_set_drvdata`，实现 `.remove`（调用 `drm_dev_unplug` 与 `drm_atomic_helper_shutdown`）和 `.shutdown`，确保模块卸载与系统关机时正确注销 DRM 设备并关闭硬件 scanout 管道。 | Linux 6.12.112 构建通过；实机验收启动并通过自测套件。 |
| **Step 4i PR 审查第 8 轮修复（本提交）** | 针对 PR #3 第 8 轮代码审查发现缺陷修复：GFX2D 加速与 CPU 缓存一致性同步：在 `firmware/linux/riscv_mini_ae350_fb.c` 中增加基于虚拟地址的矩形缓存维护助手（`ae350_fb_clean_rect` / `ae350_fb_inval_rect`），在 `ae350_fb_fillrect`、`ae350_fb_copyarea`、`ae350_fb_imageblit` 启动 GFX2D DMA 前将源和目的矩形脏缓存行刷写到 DDR（`L1D_VA_WB`），在 DMA 操作成功完成后立即失效目的矩形缓存行（`L1D_VA_INVAL`），防止软件回退（如 `sys_copyarea`）读取到加速前的陈旧缓存数据，并杜绝 CPU 脏行在后续 `ae350_fb_sync` 中覆盖 GFX2D 硬件写入的像素。 | Linux 6.12.112 构建通过；实机验收启动并通过自测套件。 |
| **Step 4j PR 审查第 9 轮修复（本提交）** | 针对 PR #3 第 9 轮代码审查发现缺陷修复：软件回退渲染缓存刷写：在 `firmware/linux/riscv_mini_ae350_fb.c` 中为 `sys_fillrect`、`sys_copyarea`、`sys_imageblit` 软件回退路径及 `ae350_fb_write` 增加绘制完成后的目的矩形缓存刷写（`ae350_fb_clean_rect`），并在 `ae350_fb_cctl_rect` 中加入视口边界钳位保护，确保 CPU 软件绘制的像素立即刷写至 DDR，使硬件 scanout 无需等待后续 draw 或 buffer switch 即可即时显示更新内容。 | Linux 6.12.112 构建通过；实机验收启动并通过自测套件。 |
| **Step 4k PR 审查第 10 轮修复（本提交）** | 针对 PR #3 第 10 轮代码审查发现缺陷修复：当前显存槽 present 缓存刷写：在 `firmware/linux/riscv_mini_ae350_fb.c` 的 `ae350_present` 中，将 `ae350_fb_clean(fb, slot)` 移到 `already-active` 检查之前，确保用户空间使用 `mmap()` 直接修改当前活动帧缓冲并调用 `FBIOPAN_DISPLAY` 提交时，能立即将修改内容刷写至 DDR 并在硬件 scanout 上即时呈现，避免更新滞留于 CPU 缓存。 | Linux 6.12.112 构建通过；实机验收启动并通过自测套件。 |
| **Step 5a 多格式支持与软件栈完善（本提交）** | GFX2D 引擎新增多格式支持与全链路软件栈打通：(1) 寄存器面新增 `FORMAT` (0x54)，CAP 更新为 `0x6f` (FILL/COPY/BLIT/RGB565/XRGB8888/XRGB1555)；(2) 状态机支持 2 次幂深度动态行字节计算 (`width << shift`)、32bpp 像素图样生成与任意字节对齐相位旋转、32bpp 1bpp 展开 (`IMAGE_BLIT`) 与 4 字节对齐校验；(3) 时序加固：倒计数比较器优化与 `video_waiting` 打拍打断跨 die 10.1ns 路径，Gowin 综合与 PnR 全时钟 0 TNS 闭合；(4) BSP (`ae350_gfx2d`) 新增 `_fmt` 变体与默认格式包装；(5) Linux fbdev (`riscv_mini_ae350_fb`) 解除 16bpp 硬编码，动态适配 bpp、stride、check_var 与调色板计算；(6) Linux DRM (`riscv_mini_ae350_drm`) 接入 GFX2D `COPY_AREA` 加速 GEM DMA 帧缓冲搬移；(7) 板级工具链增加 openFPGALoader 掉电/复位后 JTAG ROM 自动重启重试机制。 | Gowin PnR 通过（0 TNS，run `20261109T093521412719Z-build-multiformat-step5a-pnr-opt-1dc318`）；Host 仿真 11 项单元测试全过；BIOS 上传实测通过（run `20261109T101533255760Z-board-ae350-upload`）；Linux 6.12.112 实板 TFTP 启动成功并通过自测（run `20261109T102918792481Z-board-ae350-linux`）；实板硬件 GFX2D 32bpp XRGB8888 FILL/COPY/BLIT 逐像素校验通过、16bpp XRGB1555 FILL 逐像素校验通过、24bpp RGB888 参数拒绝通过；fb0 硬件加速运行无误（0 ERRORS, 0 underflows）。 |
| 附带发现（实板） | CPU 流式写 FB 偶发丢像素：cached 全屏写/读回 307200 像素坏 2 个（首坏 idx 291837）；未 cache 大批量流式写卡死（DDR3AHB 写缓冲与连续写交互）。属平台既有问题、与 GFX2D 无关，BIOS 路径（cache 开）亦受影响——GFX2D 渲染全屏逐像素 0 坏。另：DLM 内结果区在 cache 开时被掩盖，诊断需强制刷回。 | cpu 写测试 run `20261107T150249834830Z` |
| Step 5b | 二期评估：Alpha Blend（读-改-写 + 混合运算）、Scale（行缓冲 + 最近邻/线性插值） | 按 DE/灰度需求单独立项 |

每步都复用同一 `--workspace`（默认 `default`），见 AGENTS.md 构建铁律。

## 7. 二期扩展（不在一期范围）

| 功能 | 硬件增量 | 备注 |
| --- | --- | --- |
| Alpha Blend | 目标 RMW + `dst = (src*a + dst*(255-a)) >> 8` 混合（每像素 1 DSP/乘法） | 需 per-pixel alpha 源格式 |
| Scale / StretchBlit | 行缓冲 + 最近邻抽取（先）/ 双线性（后） | 资源与时序风险显著，单独评估 LUT/时序余量 |
| 多格式 Blit | 源 bpp/format 可配置、格式转换 | 与 scanout 的 RGB888/XRGB8888 对齐 |

## 8. 与现有路径的关系

- BIOS TTY（`firmware/bsp/mega138k_ae350/video/ae350_tty.c`）：已接入（Step 4a）——
  每 cell 单次 `IMAGE_BLIT`、滚动时 `COPY_AREA` 搬移（Step 4a 滚动优化）、首帧清屏 `FILL_RECT`，失败回退 CPU 路径；
- Linux（`firmware/linux/riscv_mini_ae350_fb.c`，Step 4b）：`fb_fillrect/fb_copyarea/fb_imageblit` 直接下发 GFX2D 描述符，
  不满足条件（无引擎/越界/深度不符）回退 `sys_*`；`riscv_mini_fb.c` 保留为 20K 480×272 面板专用驱动。
- U-Boot（`firmware/uboot/drivers/video/riscv_mini_lcd.c`）：video uclass 加速路径。
- DMAC：保持不变，仍服务 Audio/SD/Loopback 等线性传输。
