# Ethernet 描述符 ring DMA

适用机器：`primer20k_vexriscv`。机器 JSON 的 `rtl.features.eth_dma` 启用独占 ring，依赖 Ethernet；关闭时使用独立 PIO 后端。两种配置使用各自匹配的硬件导出和固件，不在运行失败后自动回退。

实现位于 `peripherals/ethernet_ring.py`；内部 `EthernetDMA` 负责 DDR 与 MAC packet SRAM 的搬运。MAC SRAM 在 ring 模式下由引擎私有访问，不再映射给 CPU；控制与完成通过 `eth_dma` CSR 和事件导出。

## 描述符与所有权

描述符为16字节：buffer 地址、容量/TX 长度、ownership/status、cookie。OWN=bit31、DONE=bit30、ERROR=bit29，完成长度位于 status 的低12位。硬件支持1/2/4/8/16项 ring，RX/TX 区域不能重叠；地址和范围由实际机器布局检查。

CPU 在 fence 与必要的私有 L1 维护后发布 TX producer / RX consumer 序号。RX 完成须等待载荷写入提交；TX 完成表示 MAC SRAM reader 已消费，不表示对端已经收到。停止时排空已接纳事务，超时不得提前回收仍由硬件持有的缓冲。

描述符与载荷经过带缓冲的 native 内存端口和共享 L2，不分配流式缓存行。L2 可见性不取代 CPU 私有 L1 维护；启动 RAM、描述符区域及越界缓冲受到硬件检查，不能以历史固定地址绕过布局合同。

## 软件与验证

HAL 根据所选硬件导出编译唯一后端；ring 模式管理 DDR buffers、序号与所有权，MAC SRAM 只作 staging。它不是 MAC 直接零拷贝访问 DDR 的 NIC。U-Boot 接口见 [U-Boot ring](uboot-ethernet-ring.md)。

回归包括 `tests/primer20k_vexriscv/ethernet_dma_verilog_test.py` 与 `ethernet_ring_verilog_test.py`；运行参数按各脚本 `--help`。验证字节顺序、边界、描述符发布、取消排空与重启，外部网络验收仍须匹配位流/固件并核对丢包、超时和完成计数。
