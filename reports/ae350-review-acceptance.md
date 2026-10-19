# AE350 P2 独立复核、修复与验收

2026-10-19。评审中复现的协议、CDC、构建合同、运行判定与证据聚合缺陷已修复，修复后的候选已重新构建并上板。当前交付配置为 **fabric/AHB/APB/CPU 数据口 80 MHz、DDR CK 400 MHz、DDR UI 100 MHz、CPU 目标 800 MHz**。CPU 目标频率不等于独立测得的核心频率。

这是本轮工程整改的验收记录。P2 的 1 GiB 全遍历、10 次连续重新校准、长时间压力和独立 P1 位流并未在本轮全部重验，生成汇总保留相应 partial/untested 状态。没有默认接受 CDC 风险，也没有把局部回归改名为整个阶段通过。

## 当前身份

| 项 | 内容 |
| --- | --- |
| gateware artifact | `842482a17fb71465bf9c8efda5c2971c821cea0d16b87f3570b0a0cccd70aea8` |
| 构建回执 | `build/runs/20261019T014959262469Z-build-candidate-0ef917/result.json` |
| 实际 ROM 依赖 | `3a8b986695fb9f6c0123206231687e31efcea474f2de70ae76057f63a4dc6a43` |
| ROM banner/build ID | `5d65335fcf00` |
| 诊断 payload artifact | `8380ee5acb18d17ce90cf96b50993498ce8d305440f91aaa2a4eee1586cd3c5f` |
| 独立示例 payload artifact | `118c0fce8b08`，精确引用在对应 build/board 回执中 |
| 时序 | setup=0、hold=0、minimum pulse width slack=**+0.610 ns** |
| 再构建 | `20261019T015320304819Z-build-candidate-7fe8ff`：ROM、gateware 均 REUSE |

当前 gateware 的归档源文件哈希已与工作树核对，无漂移。ROM、payload 和 gateware artifact digest 与各自 build ID 分开记录，不能混用。

## 已修复的实际行为

**错误恢复现在遵循已接受的事务。** DEAD 状态正确输出两周期 ERROR；DRAIN 结束不再凭空创造 pending 请求。请求由互连接受后，即使 DDR 正在后台恢复，也会保留其数据相位。已提交的读超时后，UI 引擎保持所有权并隔离迟到响应，直到旧数据被吞掉；无恢复条件时进入 DEAD，后续请求仍得到有界错误。默认 ErrorSlave 的错误完成拍可以接收下一地址。32 位接口的块内字选择与非法 HSIZE 也补了回归，实际 CPU 仍使用 64 位端口。

**跨域数据有明确的传递协议。** 校准与 PLL 状态用两级同步器；活动 recorder 数据和计数器通过 BusSynchronizer 的 request/acknowledge 握手形成一致快照。数据保持、采样与确认都属于该接口，CPU halt 不再被当作跨域一致性的依据。

**构建消费地址和应用合同。** 地址模块在缓存查询前被哈希，ABI、窗口和 loader 范围进入 config，C 头与链接片段存入 ROM artifact。payload 有独立的公共启动代码、弱 trap handler 和链接脚本，真正消费 `--source`/`--source-dir`、宏、声明的头文件目录等输入。诊断程序只是默认应用；二进制位置由 artifact 指定，主机不再绑定 tests.bin。源变化影响 payload 的键，而不会隐式重建 ROM。20K 默认 payload 源选择保持原行为，已实际编译验证。

**操作按阶段报告证据。** 不带 CPU JTAG 的 UART 上传报告 uploaded-and-handoff。执行核验要求 PC 落在实际镜像字节范围内且 mcause=0；整个装载窗口不能代替代码范围。payload 清单、文件哈希、machine、ABI、参数和用途先验证，再获得硬件会话锁；普通用户应用不能被 ddr-suite 当成诊断执行。显式 FPGA 位置贯穿请求、会话和编程器。

上传、恢复、DDR suite 和 JTAG 探针共用串口/JTAG/日志/编程器的会话所有权。异常路径释放资源，清理失败写入回执并使操作失败，D2XX Close 状态不再被吞掉。恢复入口先比对实际依赖 ROM，再设置 DPC。退役的 monitor 备份脚本已移除。

**检查数与测试覆盖分开。** 10 个性能采样及信息输出不再计入功能 PASS；普通读改写命名为 nonatomic.rmw，不冒充原子支持。性能第三组采样与压力结果原先复用 SRAM 槽位，已分开。主机保留三轮原始周期、指针追逐周期和压力结果，不按未标定 CPU 时钟发布“实测带宽”。

遍历从 DDR 0 开始，排除参数、完整运行镜像（text/data/bss）与保留栈，并发布实际覆盖字节。汇总按 gateware/payload 身份筛选，使用真实完整回执路径；运行中的记录与失败记录分开；普通 handoff 不覆盖执行证据，缺少的实验保持未测试。

## 100 MHz 为何撤回

旧候选 `08234b4ff228` 的 setup/hold 是 0/0，但原始 project.tr 中 BSRAM minimum pulse width slack 为 **−0.482 ns**。旧解析器没有检查该部分，因此旧的“时序闭合”结论不完整。

加入 CDC 同步后的 100 MHz 版本也能达到 setup/hold 0/0；普通全局布线、厂商 PLL 通道分配和指定全局线等实验证明，最小脉宽仍有约 **−0.48 至 −0.65 ns** 违例。这些实验没有用 false-path 或时序豁免隐藏同域脉宽问题。

80 MHz 交付点保留全部同步逻辑，脉宽裕量为 +0.610 ns。UART 分频、校准看门狗和 config 跟随同一个 FABRIC_HZ，CPU 目标和 DDR CK 保持原值。80 MHz 是本轮通过完整 STA 的运行点，不能据此声称平台物理上限就是 80 MHz。

## 实际验收

| 验证 | 结果 | 回执 |
| --- | --- | --- |
| 全量宿主回归 | 253 项 OK，2 skipped；包含新增 27 项整改回归 | `build/reports/ae350-review-acceptance-20261019/host-final.log` |
| adapter、互连、ROM 专项 | 61 项 adapter、15 项互连及 ROM 非顺序读取通过 | 同目录 `ddr-final.log`、`interconnect-final.log`、`rom-final.log` |
| 生产组件联合仿真 | 当前零基址译码 + AHBMemory + 真实 AsyncFIFO，分离地址/写数据相位，不同时钟，通过 | `tests/ae350_remediation_test.py` |
| 实板 ROM / 启动器 / DDR 8 地址 | 完整 ROM 比对、非顺序读、ACK、8/8 | `20261019T015639575003Z-board-ae350-jtag-test` |
| 独立用户应用 | 通过同一个 board run 从 DDR 执行；结果 a3500001/24681357，PC=0x30cc，mcause=0 | `20261019T015146332596Z-board-ae350-upload` |
| 当前诊断载荷 | **12 项功能检查，0 错误**，PC=0x4c58，mcause=0 | `20261019T015310142741Z-board-ae350-ddr-suite` |
| 局部八字/块遍历 | 1 MiB 地址范围，30,456 块，**974,592 实测字节，0 错误** | 同上 |
| cache 观察器 | 读计数 19404→23017→23017→30229；写计数 42016→42016→42024，errors=0 | 同上 |
| 短压力回归 | 请求 2 秒 mcycle 预算，5 轮、0 错误；整套载荷主机墙钟约 10.2 秒 | 同上 |
| UART loader | ABI/header/range/truncated/payload CRC 五条拒绝路径与正常上传/执行均通过 | `20261019T015445534314Z-board-ae350-upload` |
| ROM 恢复 | 精确依赖 ROM 读回、DPC 重启、UART 身份通过，资源关闭无错误 | `20261019T015808794385Z-board-ae350-recover` |
| 20K 宿主兼容回归 | 原默认 payload 实际编译成功，1204 字节 RPB；未编程 20K 板卡 | `20261019T015009567282Z-build-candidate-78149e` |

真实 AsyncFIFO 仿真使用 Migen 的 FIFO、指针、Gray 同步和 Memory。该版本仿真器会误读 write-only Memory 端口；测试仅为其补未使用的 dat_r sink，没有用 StubFIFO 替代这些逻辑。数字仿真不模拟亚稳态概率，也不包含加密 CPU/DDR PHY 行为。

最新生成汇总见 [ae350-p2-acceptance.json](ae350-p2-acceptance.json)。G6 局部遍历和 G11 仅当前 P2 ROM 恢复均标 partial；G3 十次重新校准和 G9 长时间压力保持 untested。400 MHz 这一交付点通过，不要求重跑 100/200/300 MHz DDR CK。

## 归档与交付边界

新的阶段快照为 `build/archives/ae350-p2-review-fixed-842482a17fb7/`，从 Store 的真实依赖闭包复制 gateware、ROM、CPU、machine description 与两个 payload，另保存本轮回执、测试和 host 源码。完整性检查重新校验所有 manifest、逐文件哈希及 gateware→bootloader 引用。

旧快照 `ae350-p2-08234b4ff228` 复制了 ROM `965fbddd0354`，该 gateware 实际依赖却为 `bc891dd94681`。旧快照保留用于审计，不能作为对应 gateware 的完整可交付闭包；当前报告已撤销该旧结论。

本轮仅写 FPGA SRAM，最终恢复到当前 ROM loader；没有写 Flash、没有修改 20K current/baseline 引用，也没有提交或推送。位宽利用率、burst 合并/流水、DMA、XIP、原子操作支持、全容量压力与独立核心频率测量均没有被本轮结果代替。
