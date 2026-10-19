# AE350 P2 工程审查

审查日期：2026-10-19（Asia/Tokyo）。对象是基于 `872bc1c981b1486dc696cc06feb5af77687213f9` 的当前未提交工作树，包含 P2 新文件及对公共构建、镜像、板端工具的修改。

**结论：当前版本证明了硬件集成及若干实板路径可行，但尚不具备可作为后续平台底座的工程质量。建议本次 review 为 request changes。** 不能用“P2 complete”代替软件接口、失败恢复、兼容性及旧平台回归审查。以下不是要求重新跑一遍既有验收，而是指出验收没有约束的实际缺陷。

本次没有连接板卡、综合、编程 SRAM 或写 Flash；没有修改实现源码。审查覆盖 CLI→host operation→产物选择→构建依赖→镜像协议→ROM/载荷→AHB/DDR UI/CDC 及对应测试、回执。厂商加密宏内部不在可审查范围，检查了其公开封装和本项目连接。测试和复现记录在 `build/reports/ae350-engineering-review/`，其中 `checks.json` 保存被审查源码哈希，`reproduce.py` 可以重复执行，不访问硬件。

**应保留的成果。** 原生 AHB 接入、小 ROM 响应修复、DDR UI 的明确读写/掩码语义、零基址 DDR 的实际接线、不可变 store 的哈希校验，以及现有实板读写证据都有价值。问题在于这些实现外围仍由阶段编号、绝对地址、测试载荷和手工回执拼接；并且错误路径还有确定的协议缺陷。整改不需要推翻已验证的硬核/DDR 连接。

**R01 [P1] 公共选线器被改成 138K 专用，破坏 20K 自动发现。**

`scripts/programmer_cable.py:37` 仅接受 ID `0x0001081B`；`scripts/mini_ops/board.py:60` 和 `scripts/boot_upload.py:125` 的 20K 编程仍调用同一个函数。连接正确的非 138K FPGA 也会被拒绝。mock 扫描已复现；现有 cable 与 Board 回归各有一项 error。即使把旧测试 mock 更新到新扫描序列，也不能解决硬编码 ID 的产品回归。

目标设备身份应由已选 Board/产物传入；公共发现层不能自己决定平台。多块匹配设备必须拒绝歧义，显式 location 也应在真正操作前验证目标身份。验收需同时覆盖 20K、138K、CPU JTAG、UART 通道及两块相同 FPGA，不能只修 138K 扫描。

**R02 [P1] DDR 超时可以让在途计数下溢，最终永久阻塞恢复。**

`gateware/machines/ddr3_ahb.py:331` 只在命令握手时增加 `outstanding`，但 `FAIL` 在 `:379–384` 无条件减一。命令尚未接受就超时的情况，0 变成 15，`IDLE` 又要求在途计数为 0 才取请求。复现结果为 `response=1, outstanding=15, idle=0`。恢复等待不再结束，最终 `DRAIN→DEAD`；`:233–235` 的 DEAD 永久输出 HREADY=0，与“每个访问有界、失败后可恢复”的接口声明相反。

需要区分“尚未发命令”“已发读命令”“已返回错误但仍可能有迟到数据”；不能用同一个计数减一处理所有失败。离线设备应对后续请求产生有界 ERROR，恢复和取消需有明确协议，不应通过永久拉低总线 ready 表示死亡。覆盖命令不 ready、数据不 ready、UI 停钟、读不返回及第二笔请求，使用实际超时大小关系复验。

**R03 [P1] A 超时完成时，紧随其后的 B 可以被吞掉并返回成功。**

`ddr3_ahb.py:206–211` 的 ERROR2 输出 HREADY=1，却不接收该完成沿上的下一地址；下一拍直接进入 DRAIN。互连按 HREADY 将这个地址判为已接受。独立仿真已得到：A 返回 ERROR；B 返回 OK、数据 0；UI 只收到 A 的一个命令，完全没有 B。现有单笔错误恢复测试没有覆盖这一形状。

恢复状态必须与互连的地址接受规则共同设计。可选择在错误完成沿保存下一地址，恢复后处理；或用合法总线协议阻止接受，但不能对上游宣布接受、下游又遗忘。需补超时→DDR、超时→SRAM及错误期间连续请求的联合测试，而不是再加一个独立等待常量。

**R04 [P1] ROM 根本不校验镜像 ABI，所谓 ABI 目前只是 host 自己算给自己看。**

`firmware/bsp/mega138k_ae350_p2/main.c:157–168` 校验 magic、版本、CRC、长度和 load/entry，却不读取 ABI 字段 `h[3]`。`scripts/ae350_image.py:23–30` 另有一份手写 ABI 字典，既不来自已选硬件产物，也未生成进 ROM。给不兼容镜像重算正确 CRC 后，ROM 不会按 ABI 拒绝。`ABI_NAME`、声明的 ISA 和构建 `-march` 也没有同一个能力合同。

应把 loader 格式版本、平台执行合同、受支持的镜像能力和精确 build identity 分开，并由硬件/固件产物发布、两端共同消费。原子操作在 fabric 上受限这一事实必须进入软件能力声明。必须有“CRC 正确、ABI 错误”的拒绝测试；不能只测坏 CRC。

**R05 [P1] “统一地址合同”没有贯通消费者，构建产物已发布错误映射。**

`scripts/modular/ae350.py:66–70` 的 bootloader 配置仍发布 SRAM=0、DDR base=0x2000；`:181–188` 生成的 P2 `memory-map.json` 仍写 SRAM=0。实板 UART 回执引用的 `78e4753d5b6c…` store 中，SRAM 与 DDR 确实同时从 0 开始。RTL 的真实 SRAM 则是 0x40000000。C、linker、host ops、镜像 ABI 继续各自硬编码地址。

`ae350_map.py:33–36` 的 WINDOWS 只有 DDR/SRAM，ROM/XIP 不参与检查；把 ROM_BASE 改成 SRAM_BASE，`check()` 仍返回空。模块声称存在的 C 头 `firmware/bsp/ae350_address_map.h` 和测试 `scripts/tests/test_ae350_address_map.py` 实际不存在。`check()` 也没有在 SoC 构造或构建发布时调用。

地址描述应有实际生成消费者和发布前校验，包括 C 头、linker、RTL、host、镜像合同、memory-map。保留窗口也需参与冲突检测并标记未实现。更改一个地址后，所有消费者随之更新或构建失败，才算单一合同。

**R06 [P1] SRAM 栈与 mailbox/results 没有隔离，linker 允许使用重叠空间。**

ROM linker `firmware/bsp/mega138k_ae350_p2/link.ld:13–14` 把栈顶设为 0x40002000，并要求保留 2 KiB 栈；这段栈预算是 0x40001800–0x40002000。`ae350_map.py:23–24` 却把 mailbox 放在 0x1800，results 放在 0x1900，宣称后者可用到 0x1f00。没有任何 linker section 或 ASSERT 将其从栈中划走。

当前浅调用能运行不代表后续 ROM 加一层解析、日志或中断就安全。应明确 data/bss、POST workspace、mailbox、结果、正常栈与 trap 栈的区间和所有者，由 linker 与合同双重断言；测试应覆盖实际栈预算和结果区 canary。

**R07 [P1] 上传入口把 ROM 的预执行提示当作载荷执行成功。**

ROM `main.c:212–215` 在调用 entry **之前**打印 `SYSTEM READY\r\n> `。`BootSession.upload()` 等到这个字符串就返回；`scripts/mini_ops/ae350_upload.py:112–115` 随即置 passed=True。可选 JTAG 分支只记录 PC、mcause 和结果，没有断言执行状态或失败。banner 也只匹配正则，不比较期望 build_id。

mock 真实 operation 已复现：期望身份与 banner 身份不同，载荷从未执行，也会输出 `PASS AE350 UART upload and execution`。这不否定已有回执中 PC 确实落在 DDR 的观察，但这个入口无法可靠产生它声称的验收结论。

需要区分上传/校验完成、已交接执行、应用就绪、诊断结束。普通 run 可只保证交接，但必须如实命名；verify 应由载荷自身的版本化结果或应用应答证明完成。固件身份必须按选择的产物核对。

**R08 [P2] ROM 与测试载荷被打包为同一个构建依赖，破坏增量开发流程。**

`scripts/modular/ae350.py:21` 将 P2 BSP 与 PAYLOAD 同时作为 firmware 输入，`:72–75` 用这个联合 key 生成 ROM build ID，`:96–109` 把 tests.bin 发布在 bootloader artifact 中。修改测试算法、输出或压力参数处理都会改 ROM 字节，继而失效 gateware 的 bootloader 依赖。独立载荷不能固定已选硬件单独构建，AE350 `build payload`/普通应用构建仍被 `machines.execute()` 拒绝。

应让 ROM 只依赖自己的源码和执行合同；诊断程序和用户程序是独立 payload 产物，依赖 BSP/合同并记录 requires；镜像由选定 payload 生成。改诊断程序后应复用 CPU、ROM 和位流，只重建载荷与镜像。这是产品操作模型的问题，不能靠再添一个参数解决。

**R09 [P2] 多套 host-op 是历史验收脚本的并列暴露，普通用户操作没有接通。**

`scripts/mini.py:27–30,147–156` 分派 ddr、jtag-test、ddr-suite、ae350-upload；各自重复选择 artifact、编程、重启、日志及结果判定。`ae350-upload.py:35` 固定取 `outputs/tests.bin`，没有用户镜像输入。正常 `board run/recover/verify --machine mega138k_ae350` 仍进入 P1 专用实现、等待 P1 banner，并要求 DDR=off。

旧 `ae350_ddr.py:135–138,181–202` 还等待已经不存在的旧 P2 banner 和 `l/p/x` monitor 命令，`:261` 仍用 `0x2000+offset` 做外部读回。它是可发现的公开操作，却不能消费当前 ROM。

建议将日常 run/recover/verify 做成按 Machine 选择的实现；JTAG/串口是内部 transport，DDR/loader 是 verify 的 suite。历史调试工具要么明确归档为精确历史产物的工具，要么移除，不再宣传为当前支持入口。

**R10 [P2] 参数不是受验证的请求，公开选项有忽略、隐含和不可执行组合。**

CLI 对 ddr-suite/ae350-upload 缺少范围与组合校验。`--repeat 0` 已复现为零次上传仍 PASS；repeat>1/self-test 会使用 JTAG restart，但 CPU JTAG 参数是可选的，直到编程之后才因 None location 失败。timeout、poll interval、region 等参数没有统一约束，负数和过大值被延后报错或载荷静默替换默认值。

`ae350_ddr_suite.py:71–81` 把五个裸整数按位置写内存；参数块没有 magic/version/size。flags 只公开说明 1/2，代码又使用 4/8。`--stack` 写进 mailbox 后，被 payload/start.S:12 重新设置的固定栈覆盖；`--entry` 不检查是否在实际上传镜像内。入口参数看似可配，实际并不承担合同。

应在触碰硬件前生成并验证明确的 RunRequest/DiagnosticRequest；测试配置用具名模式和字段，版本化编码只放在协议层。删除不起作用的选项；共享配置从所选产物读取，调用者不必知道五个整数的排列顺序。

**R11 [P2] 会话生命周期、失败回执与恢复语义不统一。**

upload 的串口和日志在 `:47–48` 打开，但保护它们的 finally 从 `:81` 才开始；扫描或编程失败可能不释放。restart 的临时 JTAG 没有 try/finally。upload 和 ddr-suite 的失败没有统一记录 error/最终状态；ddr-suite 的 finally 捕获并吞掉全部恢复异常。`CPUJTAG.close()` 又隐含 resume，调用者无法表达“保留故障现场并关闭连接”。多处 host-op 直接操作 DMI 4/5 和 command 常量，重复底层知识。

公共硬件会话应拥有串口/JTAG/编程器及独占锁，明确 halt/resume/close 策略；读写内存、校验 ROM、发布参数等由接口封装。主故障、cleanup 故障、最终设备状态及可执行恢复方式都应进入回执，不能通过 except: pass 消失。

**R12 [P1] CDC 与诊断采样没有形成完整接口，0/0 时序不能证明安全。**

DDR UI 被声明为独立时钟域。`mega138k_ae350_p2.py:269–283,301–303` 直接将 `init_calib_complete` 用于 sys 状态机/访问准入；DDR 域的 recorder 二进制计数和多位数据在 `:338–348` 直接进入 sys ROM 响应 mux。这里只有 alive 和 ui_idle 使用 MultiReg。SDC `:381–384` 将这些域整体设为异步，因而 0/0 不会验证这些跨域路径。

应同步单比特校准状态；诊断使用握手快照、保持数据后确认或明确设计的计数跨域机制，而不是给动态多位总线逐位加同步器。现有仿真用 StubFIFO 和同周期同相时钟，不构成真实异步 FIFO/复位验证。需要实际 FIFO、不同相位/频率及独立复位测试；若工具模型不支持，明确替代仿真或形式验证的覆盖。

**R13 [P2] 调试窗口偷用了 ROM 容量，构建却允许固件占满整个窗口。**

`mega138k_ae350_p2.py:338–348` 将 ROM 尾部至少 56 字节覆盖为动态诊断；`AE350ROM` 无保留区/重复地址检查。linker 仍允许 8 KiB ROM，构建也只检查 boot.bin<=8192。将来代码/rodata 正常增长到尾部，会通过链接与尺寸检查，但 CPU 读到状态字而非固件内容。

诊断布局必须独立声明为保留区，限制 ROM 可用区并生成链接断言。将其迁到规划的诊断 CSR 可后续做，但当前至少要准确发布容量、禁止窗口重叠，不应继续以追加 mux 条目作为调试接口设计。

**R14 [P2] 测试通过数没有覆盖生产接口，且出现自洽的弱断言。**

DDR 61 个检查和互连 14 个检查本轮都通过；它们分别使用 StubFIFO，互连使用在地址相位取写数据的 SRAM model，且仍测试 SRAM=0/DDR=0x2000 的旧布局。它们没有联合实例化生产 AHBMemory，也不覆盖上述超时完成沿吞请求。所谓“每个请求到达一个 slave”的断言是 `n_slave` 之和等于 `n_accepted`，二者由同一 accept 表达式更新，并非独立观测实际从设备接收数。

新增主机上传/镜像接口没有相应正式测试；声称存在的地址合同测试缺失。测试文件分散本身不是错误，但没有统一、可发现且绑定产品合同的 suite。应减少重复 PASS 行，加入独立地址/数据相位记账、真实从设备、接口失败组合与旧平台回归。

**R15 [P2] 默认 traversal 模式的写读偏移不一致，保留区也是硬编码。**

`firmware/payload/ae350_p2_tests/main.c:499–500` 在两字模式写块偏移 0/28，`:508–511` 却读偏移 0/4；公开的 flags=2 会测试错误的第二个字。八字模式的成功不能覆盖默认分支。`:482–484` 又把代码排除固定为 0x3000–0x6000，载荷增长超过这个长度时会覆盖自身，但 linker 允许更大的镜像。

测试范围与排除范围需从实际链接符号/已选镜像取得，结果应记录精确区间和覆盖定义。两字/八字模式应共享同一个偏移表，做主机级算法测试。不能再用预先挑选的 flag 组合绕开缺陷。

**R16 [P2] 主机暂停计时遗漏真正暂停的大部分时间，774 MHz 推算不成立。**

`ae350_ddr_suite.py:95–107` 只把调用 halt 花费的时间加进 paused，后续 read_memory/register 和 resume 前的整个停核区间不计入；wall 则包含全部诊断、最后一次读结果及轮询粒度。再用“stress 配置 300 秒的 mcycle 预算 / 全流程 wall”推算核心频率，混入了非 stress 工作量。

应从已确认 halted 到已确认 resumed 计量整段暂停，并对 payload 的 stress 起止单独取样。配置频率、mcycle 周期预算、主机耗时和独立时基测频必须分别保存；现有 774 MHz 不能作为测频验收，已有数据可保留为端到端耗时观察。

**R17 [P2] POST 没有按它宣称的能力检测或阻止故障。**

`main.c:79–83` 只计算 ROM 前 256 字节滚动和，从不比较期望值，无法得出 ROM 完整性通过。`:235–247` 得到 POST 错误后仍进入同一个 DDR loader；DDR 不可用时只是换一条 banner，ROM 本身没有拒绝加载 DDR 的路径。host wait_banner 拒绝错误不能替代独立启动时的板端策略。

POST 的目标需要精确声明：只展示 ROM 摘要则不要称校验通过；要检查完整性则应有受保护的期望值和覆盖范围。DDR 不可用时应有明确的受限恢复模式，加载与执行请求必须在板端检查准入条件。

**R18 [P2] 汇总报告是手写拼接，没有“最终候选”的一致性检查。**

`reports/ae350-p2.json` 同时保留旧候选的 30 分钟/全窗口记录、新布局的 64 MiB 覆盖、不同 ROM/载荷的缓存/上传回执，顶层 UART 字段仍写全部静默；acceptance/stage_task/open 与 verdict 也有冲突。部分记录只有短 digest 或 `per receipt`。这不说明那些实测不存在，但无法机器判断一个给定 build 完成了哪些验收，哪些证据只适用于历史实现。

测试实现本身应与硬件身份分离，兼容复用证据必须明确声明依赖未变的合同与字节；汇总由准确 artifact、payload、suite/config 及结果引用生成，不能仅用“同一候选族”作为合并规则。失败、豁免、不支持、未测要有独立状态。

**本轮检查结果。**

| 检查 | 结果 | 限制 |
| --- | --- | --- |
| DDR adapter | 61 个检查通过 | StubFIFO，同频同相；新增失败复现另存 |
| 互连 | 14 个检查通过 | 模拟 SRAM，旧布局 |
| ROM response | 通过 | 非顺序读/byte offset/status |
| Native AHB | 3/3 | 原有主机仿真 |
| JTAG host | 3/3 | mock，不访问硬件 |
| 模块化 build | 37/37 | 原有 host 回归 |
| Cable host | 4/5，1 error | 新实现未更新旧接口测试，另有 R01 实际回归 |
| Board host | 37/38，1 error | 扫描 runner 调用合同改变 |
| firmware_verify host | 0/2，2 error | fixture 缺 csr.json；相关实现/测试未在本次 P2 修改中变化，单列为既有测试债务，不归因于 P2 |

`reproductions.json` 另保存七类负面复现：ROM 重叠漏检、host 接受超过 ROM 上限的镜像、非 138K 被公共 resolver 拒绝、错误身份/无执行证据仍 PASS、repeat=0 仍 PASS、在途计数下溢、超时后下一地址丢失。后两项采用小超时常量缩短仿真，验证的是同一生产状态机；它们不是实板故障复现。

**整改方向：先确立产品接口，再让验收消费它。**

这与现有 `docs/multi-platform-architecture-proposal.md` 的方向一致；当前实现没有完成其中的合同与消费者分离。无需再制定一套平行框架，也不应将所有参数塞进一个“万能 context”。建议收敛为以下真实拥有行为的模块：

| 模块 | 对外接口应承担的责任 | 内部隐藏的知识 |
| --- | --- | --- |
| MachineContract | 内存区间/保留区、执行能力、时钟、loader/runtime 版本；生成消费者并验证 | C/linker/RTL/host 的具体输出形式 |
| Payload 构建 | 固定所选机器合同，单独产出 ELF/bin/image/requires | 编译器、链接布局、镜像 CRC、测试程序源码 |
| BoardSession | 明确绑定目标、独占设备、编程/传输/恢复、可解释的最终状态 | Programmer/D2XX/COM 生命周期及字节协议 |
| run/recover/verify | 接收已验证的具名请求；返回分阶段结果 | 按 Machine 选择 Adapter，按 suite 选择诊断，不增加阶段专属顶层 op |
| DiagnosticProtocol | 版本化请求/结果，精确覆盖量、时钟来源、失败与完成状态 | SRAM 偏移、ACK、结构编码、JTAG/串口观察机制 |

DDR UI 与 AHB 适配器仍保留独立逻辑测试，但恢复/CDC/总线接受规则应作为它们真实 Interface 的一部分。外设和 DMAC 尚未接入，本轮不要求为了抽象而预先建立未用的继承树。

**整改顺序与完成条件。**

1. 先修 R01–R07/R12 的正确性问题，保留已验证位流作基线；失败请求不能卡死恢复、不能丢地址、不能误报执行、不能接受错误 ABI，不能破坏 20K。加能先失败再通过的定向回归。
2. 建立实际消费的 MachineContract，修正所有元数据和链接保留区；去掉声称存在但未实现的生成/测试描述。改地址时不能再人工搜索替换十个位置。
3. 将 ROM/BSP 与 payload 构建分开，接通“固定硬件、改一个用户载荷、UART run”的正常流程。证明改 payload 不重建 ROM/RTL/PnR；run 接受独立镜像，不再固定 tests.bin。
4. 合并 host 操作的公共会话与请求验证；移除失效的旧 monitor 操作。错误配置在打开串口/编程前失败，cleanup 失败保留，设备最终状态明确。
5. 让 loader/cache/memory/stress 等验证通过这些同一产品接口执行；修正测试偏移、真实 FIFO/CDC 和最终候选证据聚合。既有硬件证据只在准确身份或声明兼容条件下复用。

达到上述条件后再进入 P3。否则引入 DMAC、HDMI reader 和中断会扩大当前地址、缓存、错误恢复及参数生命周期的隐含耦合，后续每增加一个验收场景都可能再派生一个 host-op。

位宽、AHB burst、DDR 物理 BL8 与流式路径的补充分析见 `reports/ae350-memory-throughput-review.md`；接口模型已复现读取同一 32 B 块产生 4 次或 8 次重复 DDR 命令。该问题补充上述工程结论，不改变此前正确性发现的整改优先级。
