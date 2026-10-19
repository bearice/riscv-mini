# AE350 mem 133 MHz 时序复核

2026-10-19。只读复核用户提供的 work 目录与 Gowin 报告，并查阅 PLL/SDC 手册；没有重新构建或上板。work：`build/work/ae350-rtl/06105baef97b911e40bfda93687effa90e1e4d30ed0bb3348751fc0b7aa229bb/`。

## 可确认的失败

`gateware/impl/pnr/project.tr` 给出 mem_clk 周期 7.500 ns、上升 0、下降 3.750 ns，来自 `ae350_mem_pll/CLKOUT0`，器件 GW5AST-138 B、工具 V1.9.12.04。

- MPW1：late rising = PLL 5.278 + network 3.340 = 8.618 ns。
- early falling = nominal edge 3.750 + PLL 2.471 + network 1.550 = 7.771 ns。
- Actual Width = 7.771−8.618 = −0.847 ns。
- Required Width = 0.225 ns，slack = −1.072 ns。
- 差值 4.597 ns 分为 PLL 的 2.807 ns 与后续 network 的 1.790 ns。不能全部归因 BUFG/全局布线。
- 同一报告还有 9 个 setup 失败终点、TNS −0.454 ns、最差 slack −0.079 ns，逻辑 Fmax 131.941 MHz。因此当前 133.333 MHz 同时不满足 MPW 和 setup，而非“逻辑完全通过，只受 MPW 限制”。

## 上限表述需要保留的边界

报告对比的是 late/early 时序路径，不是同一芯片同一条件下测得的两条边沿。4.597 ns 可以解释本次 STA 失败；不能据此宣称实际时钟占空比畸变 4.597 ns 或 GW5AST 全局时钟物理上限约 104 MHz。

若假定上述 late/early 差值保持不变、50% 源波形和相同最小高宽要求，则计算阈值是 1 / [2 × (4.597 + 0.225) ns] = 103.69 MHz。这只是当前模型/路径假设下的推算，不是器件规格、实测极限或全局时钟资源的一般上限。

独立 PLL 实验的参数为 FCLKIN=50、IDIV=1、FBDIV=1、MDIV=24、ODIV0=9（VCO 1200 MHz / 9），DE0_EN=FALSE、duty trim step=0；不是 800 MHz / 6 的同一 PLL 配置。SDC `-divide_by 9 -multiply_by 24` 给出的 133.333 MHz / 50% 名义波形相符。当地 UG306-1.0.1E PDF 第 49 页（印刷第 40 页）对 PLL DE0_EN=FALSE 明确给出 50% duty cycle，因此不能仅凭奇数 ODIV 判定配置必然占空比错误。

共享 PLL CLKOUT2、换 CLKOUT0 和独立 PLL 都保留相同工具/器件时序库、约束生成方式与全局网络使用模式；重复现象可以排除部分单一输出位置问题，不能排除共同建模/约束问题。尚未证实具体工具缺陷，也没有证据允许豁免该 MPW 失败。

## 建议的交付与后续调查

当前已验证 100 MHz 可以作为 P2 交付运行点；把高频失败保存为 M4 选择依据，准确标记“现有配置/工具约束下 133 MHz 失败，上限未证明”。100 MHz 的 +0.183 ns 也只是 STA 裕量，后续还需最终候选实板验收。

若继续定位 MPW，先构建使用相同器件、相同 PLL、BUFG 与 generated-clock 约束的最小寄存器时钟工程，并与厂商生成 PLL/时钟约束做单变量比较。检查 late/early 的 PLL 与 network 延迟分别如何变化。该最小工程或对比若不能收敛，可作为厂商支持问题的复现材料。不要先改适配器来解释时钟 MPW，也不要通过删除检查、夸大占空比或把 PLL 输出直接声明成理想 base clock 来判定交付通过。

若仅为定位而改变分析起点，须标记为诊断实验；PLL 输入、实际输出波形和所有相关时钟关系仍需正确建模，最终完整 STA 不继承被裁掉的时钟路径结果。

`ae350_map.py` 的数据口注释已改为：100 MHz 是当前交付运行点；试过的 133.333 MHz 在完整 STA 中同时有 MPW 和 setup 失败；该报告没有证明器件或全局时钟的物理上限。

## 构建证据归属

用户指向的 run `20261019T124655837337Z-build-candidate-e14d09/result.json` 实际 `passed=false`，错误为 `Workspace already in use: ...06105...`。该回执保持原样，不当作这次 PnR 的正式构建记录。

同目录 `gowin.log` 写到布线、时序分析、位流和功耗分析结束，末行时间是 Mon Oct 19 21:48:32 2026，并生成了 `gateware/impl/pnr/project.tr`。仓库里没有另一份 `result.json` 把这次 PnR 记成完成的 `mini.py` 构建。持有该 work 锁的进程没有留下单独回执，因此补不出它的输入闭包和正式失败状态。这次 PnR 的证据就是该 work 目录里的网表、约束、SDC 和 `project.tr`。

## 手册来源

- 本地 `TangPrimer-138K/docs/07_Datasheet/EN/UG306-1.0.1E_Arora V Clock User Guide.pdf`，PLL 参数、DE0_EN 与 duty trim。
- 本地 Gowin `IDE/doc/EN/SUG940-2.0.5E_Gowin Design Timing Constraints User Guide.pdf`，create_generated_clock 的 divide/multiply/duty/edges 语义。
- 在线官方 UG306（不同修订，仅作交叉核对）：https://cdn.gowinsemi.com.cn/UG306E.pdf 。
