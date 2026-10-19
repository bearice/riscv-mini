# AE350 写聚合验收复核

## 复核更新：272f52128db0（2026-10-19）

本轮两项发现 R-W01 / R-W02 已复核关闭。以下原始评审结论仅描述旧候选 `03c3768cd4aa`，保留为缺陷与失败复现的历史证据，不适用于修复后候选。

- 新候选 `272f52128db04966b5ead1e5c5f0f30fbd802a5628a9aba300e079257b5bbba3` 将已分派但尚未执行的事务存为 owned 地址/控制/写数据，ADDRESS/COMMIT/WAIT/DRAIN 路径保留其归属；后台排空时不以 HREADY 假完成，成功后回放，失败由该请求接收 ERROR。
- 上次独立失败复现 `build/work/review-write-aggregation/regression.py` 本轮 PASS：accepted=2、handled=2、UI=[write,read]，读回 `0xabcdef`。
- 67 项 AE350 正式测试 PASS，包含 BUSY 后转读、同块独立写、跨块写、排空超时、校准失效与恢复；现有 adapter 和 interconnect 检查亦 PASS。
- 另对 BUSY→IDLE 启动后台排空后，新请求延迟 0/1/2/3/4/5/8/12/20 周期到达作真实 AsyncFIFO + 寄存译码定向检查，9/9 PASS，均保持接收/处理计数相等、UI 写读顺序和正确读值。脚本 `build/work/review-write-aggregation/arrival_sweep.py`。
- `DDR_PROFILE.adapter` 和新候选 `build.lock.json` 已使用聚合写、提前应答、排空及请求归属语义，R-W02 关闭。
- 四个相关源文件（adapter/native/SoC/ddr_inputs）与 store 快照一致，FS SHA256 与 `build/runs/20261019T113159146665Z-board-ae350-ddr-suite/result.json` 一致。STA setup=0 / hold=0 / min pulse width slack=+0.183 ns。
- 实板回执核对通过：12 checks / 0 failures，mcause=0，SRAM 编程，未写 Flash；写计数 48453→48453→48454，读计数 45187→45188→45188→45189，仍支持正常 32 B write-back 一条 UI 写。

本轮写聚合修复验收通过；故障分支的新增证据来自仿真，正常硬核 cache 写回来自既有新候选实板回执。本次复核没有重新编程板卡。M4 数据口频点选择、M5 最终结构的独立复位等剩余边界、M7 全区间与重复校准、M8 同条件性能、M9 P1 恢复交付仍按现行合同推进，不能将本结论写成 P2 全部完成。

## 原始评审（03c3768cd4aa）


2026-10-19。评审基准为 Git index（上一轮改动已暂存），本次检查 `git diff` 的未暂存写聚合改动。只进行源码审阅、主机/RTL 仿真与既有回执核对；未重新编程板卡，未修改或暂存实现文件。

## 结论

`03c3768cd4aa` 的正常 WRAP4 cache write-back 合并为一条 UI 写，有相符的实板证据，可以认可这一局部结果。M3 完整验收暂不通过：生产寄存译码路径在后台排空时会丢失已接收的下一请求，能够静默返回错误数据。先修复这一协议缺陷，再推进最终频点和全容量验收。

## R-W01 / P1：后台排空丢失已接收的请求

位置：`gateware/machines/ddr3_ahb.py:370–375`（ADDRESS 的 must_flush 分支），以及 `WAIT:441–443`。

合法序列：INCR 写一拍 `0x2000=0xabcdef` → BUSY（下一地址为 `0x2008`）→ 结束旧 burst，NONSEQ 读 `0x2000`。第一写拍已提前应答，写缓冲尚未提交。

生产 `MemInterconnect(registered=True)` 在 IDLE 时允许接收新地址，不依赖后台 DDR 从设备 HREADY；地址接收后只分派一拍 HTRANS。适配器在 ADDRESS 发现 must_flush 时优先转 COMMIT，没有保存本拍 `incoming`，而且 COMMIT/WAIT 也不保存新接收的数据相位。排空成功回到 ADDRESS 时 HTRANS 脉冲已经结束，pending 仍为 0，适配器重新拉高 HREADY，互连便把未执行的请求当作完成。

独立定向复现使用生产寄存译码和真实 AsyncFIFO，时钟 sys=10 / ddr=14：

```text
interconnect accepted = 2
adapter n_req = 1
UI commands = [write]          # 应为 [write, read]
read response = OKAY, value 0  # 应为 0xabcdef
```

复现与断言（预期当前源码 exit 1）：

```powershell
.venv/Scripts/python.exe build/work/review-write-aggregation/regression.py
```

修复完成条件：互连一旦接收地址，适配器必须完整拥有其地址/控制/数据相位，或由互连明确延迟分派，直至适配器可接收。复用 pending 或增加握手需要同时保存对应写数据，不能仅把 HTRANS 延长几拍。覆盖 BUSY→读、BUSY→独立写、不同块、后台排空成功/超时/校准失效、真实 FIFO 与寄存译码，并断言 accepted/request/response 数量和顺序一致。把该复现迁入正式测试，修复后重新做 STA 和同一 CPU cache 写回实测。

## R-W02 / P2：产物描述仍宣称 writes per beat

`scripts/modular/ddr_inputs.py:46–47` 的 `DDR_PROFILE.adapter.burst` 仍是 `transaction-scoped 32-byte read buffer; writes per beat`。实际 `03c3768cd4aa/build.lock.json` 也保存了同一旧描述，与现在的 390-bit 聚合请求和单条行写不符。

源码哈希闭包正确，因此不是误用旧 RTL 的证据；但构建产物接口描述不再可信。修复时将适配器能力描述与真实读/写语义同步，包括聚合边界、提前应答及异步故障口径，并生成新的候选。交接文档 M3 的待办不能只剩“后续改动后重测”，应保留本次协议缺陷和相应恢复验证。

## 已核实的证据

- 回执 `build/runs/20261019T105117821696Z-board-ae350-ddr-suite/result.json`：artifact 为完整 `03c3768cd4aa8c16caccbeba25f6525f32b5d8003979855ffee73fc0437532c1`；Programmer operation 2 SRAM 编程通过，location 107569，COM9 UART 上传，CPU JTAG 6673；12 checks / 0 failures，mcause=0，flash_written=false。
- 回执 fs_sha256 与 store 内 `project.fs` 一致；三个修改的 gateware Python 文件与该 store 的源码快照逐字节哈希一致。
- timing.json：setup=0、hold=0、min pulse width slack=+0.183 ns。
- observer 读计数 45187→45188→45188→45189，写计数 48453→48453→48454；write/read_control=0x1f6b/0x0f6b，支持当前正常 write-back 一条 UI 写的结论。
- 遍历仍为 1 MiB 区间，实际覆盖 1,039,328 字节，32,479 块；压力为配置核心周期预算，不把整套约 23.9 秒当压力时长。

## 本轮重新执行的检查

- `python -m unittest discover -s tests -p 'ae350*test.py'`：62 tests PASS。
- `scripts/tests/test_ddr3_ahb.py`：现有协议检查 PASS。
- `scripts/tests/test_ae350_interconnect.py`：现有互连检查 PASS。
- 将六个已有写聚合边界测试额外放在生产 RegisteredDUT + 真实 FIFO 上运行：6 PASS；脚本 `build/work/review-write-aggregation/registered_cases.py`。
- 新增 BUSY→NONSEQ 读定向断言：FAIL，accepted 2 / handled 1，静默读回 0。原有测试没有覆盖这条“写缓冲仍有效，下一请求与后台排空重叠”的路径。

本轮未更改 staged 实现，也未更改未暂存 RTL。复现脚本位于被忽略的 build/work，后续修复者需迁入正式回归。当前实板正向证据保留有效，不能据此宣布所有 BUSY/提前结束/后台故障组合已经验收。
