# 共享 writeback L2

默认构建使用 [`SharedL2`](../gateware/shared_l2.py)：CPU 和低带宽 Wishbone DMA 共用一个 32-bit 入口，LCD 扫描和 SD lite 使用两个 128-bit coherent streaming 入口。缓存与端口仲裁处于同一个状态机内，后端直接连接 LiteDRAM native crossbar，不再经过 128-bit Wishbone 或另一套自定义 DDR scheduler。

## 数据与所有权

- 默认容量 4 KiB、直接映射、每行 16 B。数据 RAM 为 256 × 128-bit，tag RAM 保存地址、valid、dirty；共五块 BSRAM。
- CPU 对低于 `0x47e00000` 的 DDR 访问采用 read/write allocate、writeback。读写命中在缓存完成；替换 dirty 行先完整写回 DDR，再装入新行。帧缓冲区不分配缓存行。
- LCD/SD streaming 读不分配缓存，但命中时可以读到 CPU dirty 行；写命中合并字节掩码并标记 dirty，写未命中直接提交 DDR。
- CPU、LCD、SD 按完整事务轮转。一个事务占有 RAM/后端直到提交或返回数据完成，背压期间保持数据与所有者。SD 写入的 ready 在缓存/DDR 提交后才返回。
- CPU 取消请求会排空已接受事务，不返回过期 ACK。复位初始化启动窗口的 valid/dirty tag 并清零数据，其余 tag 无效；复位会丢弃未写回的 dirty 数据，不能作为持久化操作。

LiteDRAM crossbar 保留控制器的 bank 仲裁与响应路由职责；L2 内的轮转负责共享缓存 RAM 和三个入口的可见性。这两层承担不同职责。

## fence 与维护

32-bit 写 ACK 表示修改已进入共享缓存或提交 DDR，128-bit 入口随后能观察修改。普通设备访问不要求将整个 L2 写回物理 DDR。CPU 外部 fence 握手仍等待当前事务排空；AMO 路径先清理并失效匹配缓存行，再执行后端访问。

L2 CSR 提供 enable、stats、flush、invalidate、busy。flush 写回 dirty 行并保留 valid；invalidate 先写回再失效；禁用缓存先写回并失效。软件请求维护后要等待 busy 完成。stats 的低/高 16-bit 分别为命中/未命中，按模计数。

共享 L2 没有 snoop CPU 私有 L1。DMA buffer 所有权交接仍使用 HAL 的 fence 与 D-cache invalidate；coherent streaming 入口解决的是 L2 可见性，不保证任意并发 CPU/DMA 修改或完整多核缓存一致性。音频保持 32-bit Wishbone DMA，Ethernet 保持 CPU 访问 packet SRAM。

## 构建与验证

`--l2-size 4096/8192` 控制容量，默认 4096。DDR 软件初始化依赖 L2 启动 RAM，不提供关闭 L2 的构建。启动窗口固定、解除固定与栈交接见 [DDR 启动](ddr-boot.md)。8 KiB 需要重新评估 BSRAM、布局布线和实板，不继承默认 4 KiB 的验收。

`tests/shared_l2_test.py --writeback` 检查 partial write、dirty 可见性、替换、flush/invalidate、禁用、取消、复位、三端轮转和混合参考序列。`tests/shared_l2_verilog_test.py` 用 Icarus 执行真正生成的 Verilog，检查四个 CPU word lane、十六个 byte lane 和替换；Migen 仿真不能替代它。SD 的四字打包和完整字节掩码另由 `tests/native_sd_verilog_test.py` 检查。

实际 PnR 资源、时序和 full 实板边界见 [系统设计](system-design.md)。缓存策略、容量与 coherent 入口参与镜像 ABI，必须一起重新构建 FPGA 配置和固件。
