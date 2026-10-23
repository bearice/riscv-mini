# 20K 构建契约与 BIOS ABI

适用机器：`primer20k_vexriscv`。以下能力位、HAL 快照、BIOS 服务与旧 loader 边界属于此机器；AE350 的缓存、地址和 DMA 合同见其平台文档。

硬件 ABI major 和接口行为修订由 provider 在 `gateware/machines/primer20k_vexriscv/config.py` 定义。追加寄存器不改已有接口修订；改变访问语义、IRQ 分发或缓存一致性规则必须改对应修订，即使地址没变也会拒绝。配置同时记录 SD/USB 后端、音频时钟等已有语义约束。源码审查仍须维护这些声明，哈希无法自动证明硬件行为相同。

当前 `scope=configured-sdk-snapshot` 保守保存所选硬件的完整 CSR/内存/IRQ 快照，**尚未自动分析每个消费者最小访问集合**；不能把它描述为“实际使用的寄存器”。因此追加未在快照中的 CSR 可以通过，快照内接口变化会拒绝。`requirements` 支持显式声明子集，但现有 HAL/BIOS 默认没有缩小范围。ABI 相容用于检查不同产物的组合，不能冒充精确缓存命中。

构建契约 capabilities 的位号固定在 `contracts.CAPABILITIES`：0=mmu，1=fpu，2=flash，3=spi_lcd，4=sd，5=filesystem，6=video，7=board_io，8=ws2812，9=audio，10=mic，11=mic_stereo，12=eth，13=eth_dma，14=usb。`required_capability_flags` 必须全部提供，未知必需位拒绝；缺少的 `optional_capability_flags` 只返回诊断。`mandatory_flags` 属于契约格式扩展，目前所有位保留，非零拒绝。可选位不自动生成降级代码，消费者仍须实现相应行为。

BIOS → payload 另有既存运行时 ABI：`firmware/bios/include/bios.h` 定义 ILP32 M-mode ecall、magic、函数号、结构和 `BIOS_ABI_VERSION=1`；`payload.h` 校验载荷头/版本/地址范围/CRC。`bios_info.features` 的位号是 0=SD、1=video、2=USB、3=Ethernet、4=audio、5=INDEX8，与构建契约位号不同。payload、OpenSBI/U-Boot/Linux 的完整依赖尚未接入上述 store 组合检查；不能声称版本相同就已验证兼容。

现有 v1 镜像和旧 loader 的校验算法保持不变。主机契约通过不能让旧 loader 自动理解新格式，也不绕过其 ABI tag；新 wire format 与板端能力协商需按提案后续迁移并验证。本次构建管理迁移不扩大已安装 loader 的接受范围。


## 20K 组件依赖边

| 依赖边 | 组合时的规则 |
| --- | --- |
| CPU → SoC 描述 | 必须是生成描述时锁定的 CPU digest；禁止按文件名或版本猜参数 |
| SoC 描述、ROM → gateware | 必须是生成位流时锁定的描述及 ROM digest；XIP gateware 不嵌入 bootloader |
| SoC → bootloader / HAL SDK / BIOS | 同平台、ABI major、C ABI、时钟；CPU ISA 覆盖 consumer 指令集；所需 CSR 地址/宽度/类型、内存、IRQ 相同；约定的后端和行为修订相同；必需能力齐全 |
| HAL SDK → BIOS | 必须是链接时的库 digest；当前为静态链接，没有可在运行时替换的共享库 ABI |
| bootloader → BIOS 镜像 | v1 镜像格式、范围和 CRC 合法，tag 与该 bootloader 构建时的 tag 相等；BIOS 记录的 bootloader 引用也必须一致 |
