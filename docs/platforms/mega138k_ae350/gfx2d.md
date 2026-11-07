# AE350 2D 像素处理流水线（GFX2D）计划

> 适用机器：`mega138k_ae350`。状态：**一期骨架（Step 1）已落地，DDR 数据面未接**。
> 本文档是计划与寄存器合同；实现状态以代码和构建回执为准，不要把计划当作已验收能力。

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
| 0x1C | LAST_ERROR | 最近错误码（0=无，1=UNSUPPORTED/BUSY，2=ADDR，…） |
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

约束：

- 描述符寄存器在 `BUSY=1` 时写禁止（写被拒绝并置 `ERRORS`），避免半更新描述符被引擎采样。
- `START` 只在描述符完整提交后有效；骨架阶段（Step 1）`START` 直接置 `ERROR=UNSUPPORTED`，
  不产生任何 DDR 流量。Step 2 起改为真正的引擎启动。
- 所有 W1C/锁存语义同 HPD 中断：新事件优先于同拍清除。

## 5. 地址与格式约束（目标合同，引擎实现时强制）

- `SRC_ADDR/DST_ADDR`：DDR aperture 内，32B 对齐（与 Video reader 同要求）。
- `SRC_STRIDE/DST_STRIDE`：32B 对齐，`>= WIDTH * bpp`，`< 64KiB`，整帧不越 aperture。
- Blit 源为 1bpp packed，`BLIT_SRC_WB >= ceil(WIDTH/8)`；展开按字节内 MSB-first（与 `font5x7` 一致）。
- 颜色格式一期固定 RGB565（与 BIOS TTY 一致）；RGB888/XRGB8888 在引擎支持后开放。
- 重叠：`COPY_AREA` 当源/目标同缓冲且 `SRC_ADDR < DST_ADDR` 时引擎从末行向前复制；
  驱动层不做重叠检测，合同由硬件保证。

## 6. 分阶段落地

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| **Step 1（本提交）** | 计划文档；`peripherals/gfx2d.py` APB 骨架（只响应 ID/ABI/CAP/寄存器读写，START 返回 UNSUPPORTED）；`address_map.py` 地址/ID/寄存器/设备表；`fabric.py` APB decode + `EXT_INT[10]`；`soc.py` 实例化。**不接 DDR，无 PnR 风险增量之外的改动。** | `mini.py build gateware` PnR 通过（`--pnr-mode full`，run `20261105T204542678685Z-build-gfx2d-step1-skeleton-irqreg-81fdd7`；setup slack +0.002ns，余量极小，Step 2 改动需复核时序）；host 测试读 ID/ABI/CAP；SYSINFO 设备数 12。 |
| **Step 2（本提交）** | DDR 数据面：`DDRMemoryArbiter` 新增 gfx2d 端口；引擎状态机 FILL_RECT（mask 写、每行 first/last mask 预计算、引擎输出寄存器化）；BSP `ae350_gfx2d_fill_rect`（含 cache sync） | gateware PnR 通过（`--pnr-mode full`，run `20261106T062458439090Z-build-gfx2d-step2-fill-timingfix6-6a7acd`；setup slack +0.036ns）；host 测试对齐/非对齐填充逐像素校验通过；BSP 单文件编译通过。诊断 suite 逐像素校验待 Step 3 板级验收时一并覆盖。 |
| **Step 3（本提交）** | COPY_AREA（stride + 重叠，`src<dst` 时逆序逐 beat 复制；要求 `(src%32)==(dst%32)` 且 `(src_stride%32)==(dst_stride%32)`，同 FB 滚动天然满足）与 IMAGE_BLIT（1bpp MSB-first 位流→RGB565 展开；dst 须像素对齐即偶地址/偶 stride，行内字节偏移吸收为 `doff/2` 个 dummy 像素；源 beat 按行跨 beat 取字节） | gateware PnR 通过（`--pnr-mode full`，run `20261106T090149635451Z-build-gfx2d-step3-copy-blit-b59f33`；setup slack +0.003ns，**余量已耗尽，Step 4 前必须做时序加固**）；host 测试 COPY 基本+重叠下移、BLIT 8×2+非对齐 16×1 通过；BSP `ae350_gfx2d_copy_area`/`ae350_gfx2d_image_blit` 编译通过。板级诊断 suite 覆盖重叠/逐字待 Step 4 实板验收。 |
| **Step 3 板级修复（本提交）** | 实板发现 IMAGE_BLIT 源字节偏移 ≥4 读错字节：根因是 `(src_byte_off[0:5] << 3)` 在 Migen 生成的 Verilog 中被截断为 5 位（移位结果宽度=左操作数宽度），硬件按 `(off<<3)%32` 移位，而 Python 仿真语义正确故 host 测试漏检。修复：移位量改为显式 8 位 `Cat(Constant(0,3), src_byte_off[0:5])`（off*8）；同时做时序加固：INIT 拆为 INIT/INIT2/INIT3（限值/增量/校验各一拍，`dst_stride<row_bytes` 比较预注册到 INIT2）、ROW_SETUP 拆为范围检查与 ROW_PASCAL 两拍。host 测试新增源偏移 16 的 BLIT 回归用例。 | gateware PnR 通过（`--pnr-mode full`，run `20261107T074146873302Z-build-gfx2d-blit-rowsetup-split-bed245`；setup slack +0.001ns，GFX2D 内最差 +0.009ns）；实板（同 run bitstream + dbg4 payload，run `20261107T080619590330Z-board-ae350-upload`）：8×1/16×1 BLIT 逐字正确；完整 suite（run `20261107T080723432810Z-board-ae350-upload`）stage=`0xC3100005` 全过（FILL/COPY/BLIT）。 |
| Step 4 | BIOS TTY 接入（`ae350_tty_flush` 走 blit/copy/fill）；Linux fbdev `fb_fillrect/fb_copyarea/fb_imageblit`；U-Boot LCD 加速 | TTY 帧时间对比、Linux 控制台、视频并发压力 |
| Step 5 | 二期评估：Alpha Blend（读-改-写 + 混合运算）、Scale（行缓冲 + 最近邻/线性插值） | 按 DE/灰度需求单独立项 |

每步都复用同一 `--workspace`（默认 `default`），见 AGENTS.md 构建铁律。

## 7. 二期扩展（不在一期范围）

| 功能 | 硬件增量 | 备注 |
| --- | --- | --- |
| Alpha Blend | 目标 RMW + `dst = (src*a + dst*(255-a)) >> 8` 混合（每像素 1 DSP/乘法） | 需 per-pixel alpha 源格式 |
| Scale / StretchBlit | 行缓冲 + 最近邻抽取（先）/ 双线性（后） | 资源与时序风险显著，单独评估 LUT/时序余量 |
| 多格式 Blit | 源 bpp/format 可配置、格式转换 | 与 scanout 的 RGB888/XRGB8888 对齐 |

## 8. 与现有路径的关系

- BIOS TTY（`firmware/bsp/mega138k_ae350/video/ae350_tty.c`）：当前逐像素 CPU 绘制，
  Step 4 后改为每字符一次 `IMAGE_BLIT`、滚动用 `COPY_AREA`、清屏用 `FILL_RECT`。
- Linux（`firmware/linux/riscv_mini_fb.c`）：`fb_ops` 三个加速回调直接下发描述符。
- U-Boot（`firmware/uboot/drivers/video/riscv_mini_lcd.c`）：video uclass 加速路径。
- DMAC：保持不变，仍服务 Audio/SD/Loopback 等线性传输。
