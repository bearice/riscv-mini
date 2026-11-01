# Gowin GW5AST GTR12 SerDes 配置机制与 `serdes.csr` 说明

本文档记录高云（Gowin）GW5AST-138B FPGA 上 GTR12 高速收发器（SerDes Quad）的底层配置原理、`serdes.csr` 文件的来历、内部机制以及双 SFP+ 异构速率（10G + 1G）的实现方法。

---

## 1. 背景与基本概念

### 1.1 GTR12 SerDes 硬核与 UPAR 机制
在 Xilinx FPGA（如 7-series / UltraScale+）中，高速收发器（GTX/GTH）的大部分电气与时钟属性直接暴露在 Verilog 原语的 `defparam` / 参数列表中。
但在高云 GW5AST 芯片中，GTR12 SerDes 硬核（`GTR12_QUAD` 原语）采用了**上电寄存器写入初始化（UPAR, User Peripheral Access Register）机制**：
- GTR12 内部拥有成百上千个底层的控制与状态寄存器（CSR），用于配置 PMA 模拟偏置、CPLL/QPLL 分频比、CDR 时钟数据恢复环路、FFE 发送预加重、CTLE 接收均衡、收发位宽转换以及 PCS 协议状态机。
- 芯片上电或复位释放后，由内置的 UPAR 状态机微驱动（在 [`serdes.v`](file:///home/bearice/workspace/fpga/riscv-mini/gateware/vendor/gowin_gw5ast/sfp_serdes/serdes.v) 中实例化）通过专用的 32 位内部总线逐条写入寄存器：
  ```verilog
  upar_write_driver(32'h00908608, 32'h0000121A); // 配置 Q1 Lane 0 CPLL
  upar_write_driver(32'h00908620, 32'h00000006); // 配置 Q1 Lane 1 CPLL
  ```
- **`serdes.csr` 就是包含了整套上电寄存器初始化指令序列的文本文件**。在 Gowin TCL 编译脚本中通过 `set_csr "serdes.csr"` 传入，Gowin 工具链据此将初始寄存器值注入 bitstream。

### 1.2 官方文档为什么是“黑盒”？
- **无公开寄存器手册**：在高云公开发布的官方用户指南（如 IPUG1024《Customized PHY IP 用户指南》、IPUG1022《10G Ethernet IP 用户指南》）中，官方只提供图形界面向导（GUI Wizard）的输入项说明和顶层 Verilog 端口说明，**没有任何公开文档给出 GTR12 CSR 寄存器的地址分配表、位定义或模拟寄存器说明**。
- **官方使用假设**：官方要求用户“所有参数变动都必须在 Gowin EDA IP Catalog GUI 中重新运行向导生成”，把 `serdes.csr` 当作工具黑盒算法输出的“专用微码”。

---

## 2. 工具链内部架构与解构

深入 Gowin EDA 安装目录（`$GOWIN_HOME/IDE/`）可以发现其底层的完整映射与转换工具链：

1. **参数字典：`serdes_param_idx.txt`**
   - 路径：`IDE/share/device/GW5AST-138B/serdes_param_idx.txt`（89,943 行）。
   - 该文件记录了高层配置参数编号（`param`）到物理寄存器（如 `Q1_908608`）及 bit 位的精确对应关系。
2. **转换引擎：`serdes_toml_to_csr`**
   - 路径：`IDE/bin/serdes_toml_to_csr.dist/serdes_toml_to_csr_138k.bin`。
   - 高云内部通过一个由 Python 编写并使用 Nuitka 编译的工具，将高层 `.toml` 配置文件编译为底层 `serdes.csr` 文件。该工具甚至自带 `--comment` 参数用于生成带注释的寄存器 dump。
3. **寄存器地址与通道分布规律**（经 `serdes_toml_to_csr_138k.bin --comment` 严格验证）：
   GW5AST GTR12 Quad 1 的寄存器分布遵循模块化的十六进制基址：
   - **Quad 公共与 MCU 控制**：
     - `0x908104`, `0x908000`: QPLL0 / QPLL1 复位控制
     - `0x908760`, `0x908764`: 参考时钟选择与分配网络（`cfg_refclk_mux`）
     - `0x908900` ~ `0x90892c`: Quad MCU 微码引擎（`cfg_quad_mcu`）
   - **通道 CPLL (RingPLL) 寄存器**（基址按 `0x200` 偏移）：
     - **Lane 0 (SFP0)**：`0x90a0xx`（PMA 分频参数寄存器为 `0x90a00c`，10.3125G 值为 `0x10A50408`）
     - **Lane 1 (SFP1)**：`0x90a2xx`（PMA 分频参数寄存器为 `0x90a20c`，1.25G 值为 `0x100A0108`）
     - **Lane 2**：`0x90a4xx`；**Lane 3**：`0x90a6xx`
   - **通道 CDR 时钟数据恢复环路**（基址按 `0x200` 偏移）：
     - **Lane 0 (SFP0)**：`0x9002xx` / `0x9003xx`（`0x90025b` = `0x8F000000`, `0x90025c` = `0x000000D1`）
     - **Lane 1 (SFP1)**：`0x9004xx` / `0x9005xx`（`0x90045b` = `0x8F000000`, `0x90045c` = `0x000000D1`）
   - **通道 GearFIFO 位宽适配 FIFO**（基址按 `0x08` 偏移）：
     - **RX GearFIFO**：Lane 0 为 `0x908600`/`0x908604`（`0x0000021A`）；Lane 1 为 `0x908608`/`0x90860c`（1.25G 时为 `0x0000101A`）
     - **TX GearFIFO**：Lane 0 为 `0x908620`/`0x908624`（10G 时为 `0x00000026`）；Lane 1 为 `0x908628`/`0x90862c`（1.25G 时为 `0x00000106`）
   - **通道 PCS 协议逻辑**（基址按 `0x200` 偏移）：
     - **Lane 0 (SFP0)**：`0x9090xx`（10GBASE-R 64b/66b PCS，`0x90906c` = `0x00013110`）
     - **Lane 1 (SFP1)**：`0x9092xx`（1G 8b/10b PCS，`0x909268` = `0x00000177`, `0x90926c` = `0x00020110`）

---

## 3. 为什么需要手动拆解与修改 `serdes.csr`？

### 3.1 官方向导的局限
Tang Mega 138K Pro 开发板通过 Quad 1 的 Lane 0 和 Lane 1 分别引出 SFP0 和 SFP1。
在很多实际网络场景下，用户希望：
- **SFP0 接入 10G 光模块**（10.3125 Gbps，10GBASE-R，156.25 MHz MAC 时钟）；
- **SFP1 接入 1G 电模块或千兆光模块**（1.25 Gbps，1000BASE-X / SGMII，125 MHz MAC 时钟）。

然而，**高云官方 SerDes IP 向导不允许在同一个 Quad 中为不同通道选择不同的线速率**：
- 在 GUI 中勾选 10G 时，整个 Quad 会全部被初始化为 10.3125 Gbps；
- 在 GUI 中勾选 1.25G 时，整个 Quad 会全部被初始化为 1.25 Gbps。

### 3.2 异构混合速率的实现方案
由于 GTR12 硬件每个 Lane 内部都配有独立的 CPLL（由板载 MS5351 提供的外部 125 MHz 参考时钟驱动），硬件上完全具备独立分频能力。
我们通过以下步骤实现了同一 Quad 内的 10G + 1G 异构双速率：
1. **基准提取**：分别通过官方参考设计导出纯 10.3125G CSR（[`TangPrimer-138K/example/sfp+/10G_Serial_Ethernet/XGbE_UDP/src/serdes/serdes.csr`](file:///home/bearice/workspace/fpga/TangPrimer-138K/example/sfp+/10G_Serial_Ethernet/XGbE_UDP/src/serdes/serdes.csr)）与纯 1.25G CSR（[`TangPrimer-138K/example/sfp+/customized_phy/1.25G/src/serdes/serdes.csr`](file:///home/bearice/workspace/fpga/TangPrimer-138K/example/sfp+/customized_phy/1.25G/src/serdes/serdes.csr)）。
2. **差异比对**：比对两者输出，精准提取出 Lane 1 的 CPLL 与 PMA 控制寄存器。
3. **寄存器重组（[`serdes.csr`](file:///home/bearice/workspace/fpga/riscv-mini/gateware/vendor/gowin_gw5ast/sfp_serdes/serdes.csr)）**：
   - **Line 370 (`0x908608`)**：配置为 `0x0000121A`，保持 Lane 0 CPLL 运行在 10.3125 Gbps；
   - **Line 350 (`0x908620`)**：配置为 `0x00000006`，将 Lane 1 CPLL 切换到 1.25 Gbps 分频比；
   - **Line 368 (`0x908628`)**：配置为 `0x00000106`，将 Lane 1 时钟环路锁定在 1.25 Gbps。
4. **顶层 RTL 互联**：
   - Lane 0（64 位数据位宽，161.13 MHz）连接到 10G PCS 与 10G MAC；
   - Lane 1（10 位数据位宽，125 MHz）连接到官方 1G PCS/MAC（`Giga_Serial_Ethernet_Top`）。

---

## 4. 为什么本工程的某些 IP 没有 `.ipc` 参数文件？

### 4.1 `.ipc` 的角色：仅为 GUI 元数据
- `.ipc`（IP Configuration）是 Gowin EDA IP Catalog 图形界面的工程描述文件（INI 格式）。
- **Gowin 命令行编译器（`gw_sh`、GowinSynthesis、PnR）完全不读取、也不依赖 `.ipc`**。
- 在自动化构建流水线（如 `mini.py`）中，只有 Verilog 源码（`.v`）、头文件（`.vh`）、网表（`.vg`）和约束文件（`.cst`/`.sdc`/`.csr`）参与综合与实现。

### 4.2 1G Ethernet 核（[`sfp_ge_1g`](file:///home/bearice/workspace/fpga/riscv-mini/gateware/vendor/gowin_gw5ast/sfp_ge_1g/)）的无 `.ipc` 架构
- 该核提取自 Gowin EDA 自带的成熟官方 IP 库：`$GOWIN_HOME/IDE/ipcore/SERDES_IP/IPlib/1GSERETH/data/` 中的 [`ge_pcs_wrap.v`](file:///home/bearice/workspace/fpga/riscv-mini/gateware/vendor/gowin_gw5ast/sfp_ge_1g/ge_pcs_wrap.v) 与网表 [`ge_pcs.v`](file:///home/bearice/workspace/fpga/riscv-mini/gateware/vendor/gowin_gw5ast/sfp_ge_1g/ge_pcs.v)。
- 高云官方在设计该核时，将其设计为纯 RTL 端口与宏控制：
  - `basex_or_sgmii_i`：端口控制运行在 1000BASE-X（光口）还是 SGMII（电口）；
  - `sgmii_mode_i`：端口控制 SGMII 是 MAC 模式还是 PHY 模式；
  - `basex_link_timer_i`：端口控制自协商 link timer；
  - `define.vh` / `parameter.vh`：提供状态机和缓冲区配置宏。
- 在 Migen/LiteX 顶层（[`ethernet_sfp_ge.py`](file:///home/bearice/workspace/fpga/riscv-mini/gateware/machines/mega138k_ae350/peripherals/ethernet_sfp_ge.py)）中，这些引脚直接连接到了系统 CSR 寄存器，使得工作模式可以通过固件动态切换，无需任何静态 `.ipc` 配置文件。
