# AE350 DMA 功能验收

2026-10-20。P3 当前运行点功能完成，允许进入 P4。sys/ITCM 80 MHz、数据口 100 MHz、DDR UI 100 MHz、DDR CK 400 MHz；core 配置 800 MHz，同候选 mcycle 实测约 800.5 MHz。频率扫描暂停，不声明器件或组合稳定上限。

## 已验收范围

硬核 ATCDMAC300 到外部 DDR 和 loopback 的轮询式传输：10000 次 32-bit、1–32 words 的递增/固定源/递减/4 KiB 边界组合，guard 与数据完整；byte/halfword 短拷贝；链表、双通道、循环 abort 后停止、延迟窗口无迟到写、同缓冲复用。FIFO 的请求/应答、满/空背压、停止后清队列均有结果断言。

公共 `ae350_dma_submit()` 对完整设备 ID、ABI、MMIO/IRQ 页身份、范围、能力和不支持宽度先校验再写通道；拒绝掩码 0xFFF。缓存记为 clean 前对 DMA 可见，不冒称脏行写回；单行、三行、非满行按范围 invalidate 的测试标志为 0x7F。

原实板回执：`build/runs/20261020T080229355702Z-board-ae350-p3-suite/result.json`。Gateware `d6a32f5add1ba782196e25204fda195c8b7a1e231aeffdcb67dce9aca9f914fd`，FS SHA256 `2b4f2fbc43842353edd44da8a8156d78f115e06601daa1322aea969272b7372a`，STA setup/hold 0/0，最小脉宽裕量 +0.183 ns。Payload `d1fd54ea32a45ccba4ac7222e9ec0ef02c57eb99bb2cf0f29ab036362d89b2d3`，binary SHA256 `76e4ca8c0efccb138af9db736916e49d3ac514439c876ee337557da85267523c`。独立校验 manifest 及当前 judge 重判通过。前一次 `20261020T080006681944Z-board-ae350-p3-suite` 正确拒绝未归类缓存策略。

## 最终接口与限制

整理后同频点重建 gateware `f6523855b9d785c37cb37995f605cb9bce33c373bb9378577639b8a22c3c3b86`，FS SHA256 `4e3f50c6954d0a33b4fc39a537a3e093722a2248f9d72cb6ead0c76aba1356cf`，STA 0/0、脉宽裕量 +0.183 ns。正式 DMA 载荷 `41cfaf505e957b7f0280ca692b48c508e53452581826299958b5884fddfaf43c` 与原验收 binary 字节一致；新入口实板通过：`build/runs/20261020T081332159992Z-board-ae350-dma-suite/result.json`。DDR 回归为 `build/runs/20261020T081410037685Z-board-ae350-ddr-suite/result.json`，13 checks / 0 failures，1 MiB 遍历、每块八字、cache 观察和短压力通过；三轮性能原始 cycle 见 [性能回归](performance/ae350-dma-regression.md)。本轮不重新做全容量/长压力验收，不扫描其他频率。

正式入口为 `board dma-suite`，载荷为 `firmware/examples/ae350_dma_suite.c`，驱动为 `firmware/ae350/dma/`。旧 P3 命令与探针退役，历史回执保留原名。命名整理后重新构建的载荷与上述已验收 binary 字节一致。

PLIC→CPU handler 没有实板证据；IRQ 仅验收 fabric pending/clear 与发布身份。专用 fabric reader 未实现且能力位清零。大于 32-bit 的 DMA 访问明确拒绝。当前 API 限单一调用者独占空闲通道；忙通道 submit 保护、wait 区分硬件错误停止尚未实现，正式 DMA 外设接入前必须修复。链表内容由调用者验证并保持生存期，部分行同步要求整行所有权。详见 [DMA 服务](../docs/platforms/mega138k_ae350/dma.md)。

这不是正式系统发布，不授权 Flash 写入。最终整理后的复验与提交快照在本地 build 回执和归档保存，不把旧实板结果重标为新源码执行。
