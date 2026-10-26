# AE350 SDK Flash boot / sys 100 MHz 候选

最新状态：官方 Flash 最小系统的100/80MHz未通过、50MHz通过。用户已决定完整系统固定sys50并清理提频妥协；音频回到sys50，删除独立audio80 CDC及额外AHB/APB流水，DDR数据域保留100MHz。当前简化与完整时序状态见[sys50记录](ae350-sys50-simplification.md)，隔离证据见[最小系统探测](ae350-minimal-flash-timing.md)。下文的sys100/旧sys50结果是此前实验记录，未写Flash。

2026-10-26。按用户最新决定采用官方配套物理布局：U4 配置位流在 `[0,0x600000)`，独立 8 KiB bootrom 在 `[0x600000,0x602000)`。CPU 复位和链接入口保持 `0x80000000`，不是 `0x80600000`。撤销原末尾 8 KiB / 自定义 ROM SPI 后端；U7 保持硬核 SPI2。

启用官方 `ENABLE_SPI_FLASH` 与 `FlashRegisterRWMode`，禁用 ITCM 和 Customized Instruction。FR 控制寄存器使用预留的 `0xf8090000` 页，经 APB 适配器转发至官方 FR 接口；内部地址采用示例的 `0xf8000000+offset`。SYSINFO 地址不变。适配器不生成 SPI 时序，保存事务并等待 FR_PREADY，只转发合法寄存器访问。

bootrom 当前为 3092 字节，代码/常量搬入硬 ILM、数据/栈放硬 DLM。构建输入限定为启动源码、最小启动合同及编译配方/工具；频率和当前硬件 image ABI 从固定 SYSINFO 读取，外设修改不会自动修改启动代码。物理分区变更后初版 boot artifact 为 `8d1e0c6ee40b`；移除旧协议元数据后的当前 artifact 为 `d96c9542d1ad`，ELF 入口核对为 `0x80000000`。

目标时钟为 sys 100 MHz、mem 100 MHz、audio 80 MHz、CPU 800 MHz、DDR UI 100 MHz、DDR CK 400 MHz。音频 BSRAM/serializer 在独立 80 MHz 域，经 APB/AHB 事务 FIFO、REQ/ACK 事件和 IRQ 同步连接 sys。官方 Flash 子系统的内部/引脚模式时钟另按参考 SDC 约束，100 MHz 兼容性尚待完整时序和实板确认。

当前已通过 RTL/固件生成、bootrom 编译、FR 等待/地址翻译/一次性转发/非法访问隔离仿真，以及 SYSINFO 与 FR 页共存和非法字节写拒绝测试。当前接口结果见 [SDK Flash tests](ae350-sdk-flash-tests.json)。历史自定义 ROM SPI 测试仅保留为旧实验记录，不能替代官方后端验收。

目前没有通过完整 STA 的新组合，也没有写入 Flash 或切换实板。完整 STA 继续复用默认 gateware workspace，不放宽 setup/hold/minimum-pulse-width 门禁，新增未创建时钟检查。首次官方后端 PnR 因发现未创建 SPI 模式时钟而停止；补齐约束后的 run `20261025T164536681269Z-build-sdk-flash-sys100-mode-clocks-3bb37e` 完成布线但失败：setup 7373、hold 3（HDMI reset removal）。最差 setup -5.180 ns，为 SDK Flash 内部真实 sys→sys 路径；DDR UI 与 mem 也存在违例，不属于跨模式时钟误报。下一轮保持默认工作区改用 route option 2 复验。

8 KiB 是当前小启动程序的预留容量，不再是 Flash 末尾位置要求。官方物理固件区从 `0x600000` 开始；本轮也移除了旧自定义 SPI 后端遗留的 `read_opcode=3` 元数据，读协议由 SDK 控制器定义。

route option 2 的 run `20261025T193750671679Z-build-sdk-flash-sys100-route2-b7ee01` 仍失败：setup 10406、hold/removal 2、worst setup -4.789ns、minimum pulse width +1.124ns。最差路径仍在官方 Flash 内部，DDR UI 与 mem 也未通过；详见 [时序摘要](ae350-sdk-flash-timing.json)。两轮布线不能证明官方后端已支持 100MHz。

HDMI reset 相位已由 72°/8ns 改为 90°/10ns，避免与 125MHz 时钟边沿重合；复位、锁丢失、停止时钟及释放边沿间隔仿真通过。临时 AHB/APB 50MHz 诊断 run `20261025T195735274220Z-build-sdk-flash-sys50-diagnostic-reset-phase90-1713a8` 已完成，CPU/mem/audio/DDR 时钟不变：sys setup 0，hold/removal 0，最小脉宽 +1.134ns；完整组合仍失败，setup 114（mem 102、DDR UI 11、audio 1），worst -0.416ns。因此仍无可上板的新候选。诊断结束后机器 JSON 已恢复 sys100 目标，未用临时降频替代原目标；boot artifact `d96c9542d1ad` 在频率变动下正确复用。

接口调查与旧布局变更依据见 [官方接口调查](ae350-official-flash-interface.md)。U7 四根数据线已通过 MOSI/MISO/WP/HOLD 连到硬 SPI 数字端口；Quad 模式仍需硬核 CONFIG 与 NOR QE/命令能力验证，当前保留单线 1 MHz 已验收基线。
