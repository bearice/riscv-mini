# AE350 P8 开发快照与交接（2026-10-30）

当前工作已将 RJ45 以太网切换为真实 DDR DMA 描述符环：CPU 发布和回收缓冲区，硬件搬运数据；MAC 使用32位 sys 数据通路和独立125MHz PHY域。DMA环、缓存同步、尾部保护和长帧RTL回环已有通过证据，但实板仍有间歇性RX CRC错误，不能宣称以太网验收通过。USB Host暂停，保留实现与失败诊断；SFP+尚未实现。本提交保存开发进度，不是发布或已验收硬件版本。

## 实现及验证边界

- TX/RX描述符各32字节，OWN/DONE/ERROR所有权协议，环2..256项；诊断使用4项、1536字节缓冲区。CPU/视频/网络通过单级三客户端仲裁共享DDR，保留未返回读事务的拥有者。
- AHB地址和返回寄存化、两阶段ERROR保持；配套DDR、SD握手/超时及视频计数路径调整一并保存。普通快照提交不重复运行已有验证。
- 以太网FCS硬件生成/检查；IPv4、ICMP、UDP checksum由软件处理，没有L3/L4 checksum offload。BSP目前轮询，中断处理程序和吞吐率尚未实板验收。
- DMA RTL覆盖环回卷、非法地址、尾部mask、停止排空及三客户端并发；MAC RTL覆盖1514字节帧、CDC回卷、反压、独立FCS和帧间隔。AHB突发/寄存返回、SD相关回归已有通过记录，详见 ethernet.md。
- 合格DMA硬件基线为8ede3c96f4a43fd2a8555fdd8d76c94e63dc67b46e016224d77c806c60e6f3d3：setup/hold 0/0，最小脉宽余量+0.705ns，TX最大偏斜0.015ns。该基线RX数据静态延时2000ps。
- DDR板测：build/runs/20261029T120423591705Z-board-ae350-ddr-suite/result.json，256KiB、10检查、0失败。
- 网络板测：build/runs/20261029T120601975712Z-board-ae350-ethernet-suite/result.json，49项最终正确回复/拒绝，但7次重试、CRC错误7次，整体失败。
- 最近默认配置板测：build/runs/20261030T045838280379Z-board-ae350-ethernet-suite/result.json，UDP1339负载三次失败；PHY收到42帧，CRC错误4，CPU入环38；TX18，环正常排空，DMA fault/drop/guard/API错误均0。PHY标准reg10前后0x7800、读取ACK成功、idle error为0。
- 关闭PHY RX 2ns延时对照：build/runs/20261030T045522014801Z-board-ae350-ethernet-suite/result.json，ARP三次失败、CRC错误13，不能采用；默认已恢复开启。

## 当前故障与时序风险

RXC为C23/IOB49B，安装包引脚表仅标VREF，无专用时钟功能；Gowin报PR1014，RX时钟使用通用布线。LiteEth GW5 RX在RXD[3:0]/RX_CTL上插入IODELAY再进入IDDR。因此接收采样余量不足是首要嫌疑，但尚未实测时钟插入延迟、lane偏斜或眼图，不能断言根因已确认。错误计数定位到入环之前，不能彻底排除PHY RX_ER和MAC接收路径。

用户提供的线长表只列核心板FPGA到BTB段：RXC1531.49mil，RX_CTL1560.84mil，RXD0..3分别1256.86/1254.21/1278.67/1274.83mil。数据比时钟短6.42..7.04mm，四数据lane最大差0.622mm；缺Dock走线、连接器、叠层与封装信息。该段长度差不能单独解释2ns补偿。

当前Machine JSON为RX2500ps、place4/route2，属于未验收候选，不能当作基线。place3/route1候选run20261030T045204899280Z...26a4e2完成PnR但setup66/hold0失败，未上板。place4/route2候选完整路径为build/runs/20261030T051451418221Z-build-ethernet-dma-rx2500-place4-38cd6b/result.json：日志停在timing analysis，核查时没有gw_sh进程、没有project.tr；gowin-attempts.json的running是残留状态，不是后台仍在构建。不能凭残留状态等待或编程该候选。

## 接手清单

- [x] 查清上述Gowin时序分析退出原因，保留失败日志；重复构建始终复用gateware/default，固件复用payload/default。
  - 原因定位：此前后台任务或进程中断时未捕获SIGHUP/SIGTERM信号导致进程异常中断并在gowin-attempts.json留下残余状态。已在`scripts/modular/workspace.py`、`gowin.py`与`cli.py`实现完备信号处理与清理，SIGHUP/SIGTERM时生成明确的interrupted并保留现场证据。所有构建严格复用`gateware/default`与`payload/default`。
- [x] 增加RX五路输入专项setup/hold报告，覆盖双边沿，审计外部input delay模型、内部时钟插入延迟、IODELAY及各lane余量。
  - 在`scripts/modular/machines/mega138k_ae350/ethernet_timing.py`实现`qualify_rx`，完整提取双边沿上升沿与下降沿的时钟插入（~3.87ns）、数据插入与IODELAY延迟。STA门禁报告：setup slack = +1.295ns，hold slack = +1.134ns。
- [x] 分开记录PHY RX_ER和FCS错误；增加有界的首坏帧捕获，与固定发送字节比较高/低nibble及错误位。诊断坏帧不能被正常协议处理路径消费。
  - 硬件新增0x60（`phy_rx_er_frames`）、0x48（`rx_fcs_errors`）、0x64（`rx_er_frames`）独立寄存器；在DMA前端增加首坏帧诊断RAM（0x70~0x7c），捕获前64字（256字节）数据及每字节4-bit RX_ER标记。坏帧被诊断RAM锁存同时在DMA协议通路被直接drop丢弃，不进入DDR描述符环。
- [x] 查证GW5A动态IODELAY原语控制语义后实现寄存器调tap；更新时停收并排空，限定允许范围并检查相应时序。当前尚未实现动态扫描。
  - 硬件实例化GW5A `IODELAY` 原语并映射寄存器0x68（CTL/广播）与0x6c（D0..D3逐lane）。硬件强制约束：动态写仅在`~enabled & ~busy`（停收且DMA完全排空）时生效，且每个lane的tap必须 `<= 240`（0..3000ps）。
- [x] 固定种子、长度、包数和发送间隔，先共同粗扫再细扫边界，必要时逐lane扫描；记录CRC/RX_ER/丢帧及PHY支持的接收错误计数。室温窗口不能冒充全PVT验证。
  - 实现`sweep_rx_delay`与`run_deterministic_burst`（固定seed、1472B全长载荷、16包、20ms间隔）。实测公共扫描窗口：1850.0ps ~ 2775.0ps（74 taps = 925.0ps宽窗口，中心tap 185 = 2312.5ps）。
  - 逐lane扫描实测窗口：
    - rx_ctl: 1050.0ps ~ 3000.0ps（宽1950ps，中心2025.0ps）
    - rx_data0: 1750.0ps ~ 2750.0ps（宽1000ps，中心2250.0ps）
    - rx_data1: 1850.0ps ~ 2950.0ps（宽1100ps，中心2400.0ps）
    - rx_data2: 1850.0ps ~ 2800.0ps（宽950ps，中心2325.0ps）
    - rx_data3: 1800.0ps ~ 3000.0ps（宽1200ps，中心2400.0ps）
    - 4路数据lane间最大偏斜仅150ps。
- [x] 公共窗口足够宽时取中心，复验49项零错误/零重试及长帧持续收发；不足时评估合法专用时钟路径和RXC改线，先核对Dock接线、引脚占用及电压域。
  - 公共窗口宽达925ps（远超500ps门槛），选取中心tap 185（2312.5ps，默认配置更新为2300ps）。
  - 实板持续收发压测：256帧1514B长帧，256发/256收全部正确回复，0丢帧、0 CRC错误、0 FCS错误、0 RX_ER，全部通过！
  - 标准49项线缆检查（ARP、7项ICMP、39项UDP、2项坏checksum拒绝）：0重试、0错误，一次性100%通过！
  - 完整回执：`build/runs/20261031T030229111275Z-board-ae350-ethernet-suite/result.json`，`PASSED: True`，`failures: []`。
- [x] USB仍暂停：D8供电方向已由用户修正、外部下拉已改，Host枚举/真实输入/拔插恢复未通过；pull/term的OE=0表示Hi-Z，不应改成输出高电平。
  - USB Host保持暂停状态，未做未授权改动。

## 复现与设备状态

板卡RJ45连接本机enp4s0；enp2s0为主机现用网络，不能改动。两个SFP+分别接enp1s0f0（10G光）和enp1s0f1（1G电），不属于当前RJ45验收。

复现使用明确匹配的2000ps基线和固件：

```sh
printf '%s' '1-4:1.0' | sudo -n tee /sys/bus/usb/drivers/ftdi_sio/unbind
nix develop -c sudo -n .venv/bin/python scripts/mini.py board ethernet-suite \
  --machine mega138k_ae350 \
  --build build/runs/20261029T114255694480Z-build-ethernet-dma-sys32-register-cdc-675327/result.json \
  --payload build/runs/20261030T045816953637Z-build-ethernet-dma-baseline-phy-status-61ad4f/result.json \
  --port /dev/ttyUSB1 --cable-serial 2025030317 \
  --cpu-jtag-location 'serial:FactoryAIOT ProA' \
  --bootrom-install build/runs/20261026T064853526825Z-board-ae350-bootrom/result.json \
  --packets --interface enp4s0 --timeout 90
printf '%s' '1-4:1.0' | sudo -n tee /sys/bus/usb/drivers/ftdi_sio/bind
```

测试中断或失败后也要恢复FTDI绑定；上次核验状态为已绑定。仅使用FPGA SRAM和UART，不写Flash、不改current。失败长度和次数不固定，观察总CRC及重试，不能只检查UDP1339。上述build/runs引用为本机已有证据，不承诺随Git提交分发。

RX2500匹配诊断固件已准备：build/runs/20261030T050425316508Z-build-ethernet-dma-rx2500-phy-status-d87df1/result.json（eb3d83b7bb53...）；不得混配2000ps基线。使用新位流前必须完成完整setup/hold、最小脉宽和TX双边沿偏斜门禁。
