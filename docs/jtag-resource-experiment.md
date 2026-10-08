# JTAG CPU 调试资源实验

实验日期：2026-10-08。源码基点：`61b6f9ea423f8f1aeec499fe2b084b6adc75122f`。
分支：`experiment/jtag-resource-test`。本次修改仅在独立 worktree 中。

## 比较对象

三组使用同一份 full 外设、60 MHz sys / 120 MHz DDR、Flash XIP（无 boot ROM）、
4 KiB shared L2、2 KiB I/D cache、RV32IMAF、MMU/FPU、无 C、两周期 I-cache 与
injector 配置。Gowin V1.9.12.04，GW2A-18C，place=3 / route=2，层级 RTL，综合网表展平。
采用 `--generate-only` 生成硬件，不构建或运行新固件。

| 组 | `--debug-mode` | CPU 输入 | 内容 |
|---|---|---|---|
| baseline | none | v0.8.0 发布包的 CPU RTL | 原系统 |
| transport | transport | 与 baseline 完全相同 | Gowin GW_JTAG + JTAGPHY CDC + JTAGBone Wishbone 主机 |
| debug | cpu | 同一生成器增加 `--debug` | transport + VexRiscv DebugPlugin + LiteX debug bus 适配 |

基线 CPU SHA256：`d8a525f4eed08fcd7a8f198885849343f0531559946805108e06bff348b55f70`。
调试 CPU SHA256：`be8090cd7395de947f33bdb3689a9e0988ad22026b24538e9b26d80b7c2a4d2b`。
两者上游生成器 SHA256 均为 `f186eefe789a22ceb04061b74a1bbd30e84a27947ab3ab10a24c558dd1b0410a`。
调试 CPU 的硬件断点数量为 **0**；本次数据不包含硬件断点比较器。

GW_JTAG 使用芯片原有 JTAG 接口。CPU 调试通过 JTAGBone 的 Wishbone 通路访问
`0xf00f0000` 调试寄存器，并非另加一组 FPGA GPIO，也不是 VexRiscv 原生 JTAG 协议。
尚未验证主机驱动、OpenOCD/GDB 接入、板上停机/单步/寄存器读写。

## 综合结果

来源：各组 `gateware/impl/gwsynthesis/project_syn.rpt.html`，三组均实际综合完成。

| 资源 | baseline | transport | debug | debug 相对 baseline |
|---|---:|---:|---:|---:|
| LUT | 16175 | 16470 | 17045 | +870 |
| Register | 11366 | 11584 | 11708 | +342 |
| ALU | 2439 | 2466 | 2468 | +29 |
| SSRAM | 107 | 111 | 111 | +4 |
| BSRAM | 40 | 40 | 40 | 0 |

JTAG 接入本身为 +295 LUT / +218 Register / +27 ALU / +4 SSRAM。
CPU 调试部分在 transport 之上增加 +575 LUT / +124 Register / +2 ALU。
SSRAM 是工具报告的 RAM16 实例计数，不能直接当成等量 LUT。
综合 LUT/ALU 计数与布局报告的 Logic/CLS 是不同口径，不能直接混用。

## 布局布线结果

baseline 重建：Logic 19562/20736，Register 11398/16173，CLS 10180/10368，BSRAM 40/46。
完整 PnR 通过，Setup/Hold 均为 0，资源与 v0.8.0 发布基线一致。

| 布局资源 | baseline | transport | debug | debug 相对 baseline |
|---|---:|---:|---:|---:|
| Logic（上限 20736） | 19562 | 19916 | 20497 | **+935** |
| Register（上限 16173） | 11398 | 11616 | 11740 | +342 |
| CLS（上限 10368） | 10180 | 10284 | 10347 | **+167** |
| BSRAM（上限 46） | 40 | 40 | 40 | 0 |

transport：Logic 19916/20736，Register 11616/16173，CLS 10284/10368，BSRAM 40/46。
相对 baseline：**+354 Logic、+218 Register、+104 CLS、0 BSRAM**。
PnR 失败：`ERROR (PR0004): There are total 334 unrouted nets`。
未生成 bitstream。资源计数没有超上限，但该参数下布线不成功。

debug：相对 baseline **+935 Logic、+342 Register、+167 CLS、0 BSRAM**。
相对 transport 的 CPU 调试部分为 **+581 Logic、+124 Register、+63 CLS**。
PnR 失败：`ERROR (PR0004): There are total 273 unrouted nets`；未生成 bitstream。
资源计数为 Logic 98.85%、CLS 99.80%，分别剩余 239 Logic 与 21 CLS。
本次选择的 place=3 / route=2 无法生成可用全外设调试系统。没有进一步扫布局参数，
也不能把资源计数未超上限当成可布线或时序通过的证明。
失败组的数字来自失败 PnR 输出的资源报告，仍可用于本次开销比较，不能用来验收硬件。

## 实验实现与约束

`scripts/cpu_generate.py --debug` 保留原生成器参数，仅添加 DebugPlugin。
`scripts/build.py --debug-mode none|transport|cpu` 默认为 none；
CPU RTL 的调试端口必须与 cpu 模式匹配，避免产生未连接的调试逻辑。

新增 JTAGBone 会改变 Migen 自动命名。以太网约束现在兼容 `minisoc_core_` 与 `core_`
两种实际前缀，并继续核对恰好 8 个第一层同步寄存器；没有放宽为全时钟域豁免。
Gowin JTAG IP 显式加入层级工程，避免仅扫描顶层的 LiteX hook 漏掉实例，以及 Windows
路径分隔符处理导致错误 IP 路径。LiteX 默认 30000000 ns JTAG 周期修正为 6 MHz 的
166.666 ns。该频率是布局检查的实验约束，尚未经板测验收。
Gowin 布局日志仍有 `TA1132`，指出 JTAG 延迟链末端的内部时钟未创建。
因此本次记录资源与布局结果，不将新调试链路称为完整时序验收或板测通过。

## 复现与证据

CPU 生成使用工作区外层现有 `.venv/Scripts/python.exe`：

```powershell
$Python = 'C:/Users/bearice/Workspace/TangPrimer-20K/riscv-mini/.venv/Scripts/python.exe'
$Tools = 'C:/Users/bearice/Workspace/TangPrimer-20K/tools'
$env:PYTHONUTF8 = '1'
$env:PATH = 'C:\Gowin\Gowin_V1.9.12.04_x64\IDE\bin;' + $env:PATH
& $Python scripts/cpu_generate.py --vexriscv-source "$Tools/vexriscv" `
  --java "$Tools/jdk8/jdk8u504-b01/bin/java.exe" --sbt-launch "$Tools/sbt-launch-1.9.7.jar" `
  --pipelined-fetch --debug --output-dir build/cpu-debug
$BaselineCPU = 'C:/Users/bearice/Workspace/TangPrimer-20K/riscv-mini/build/releases/system/v0.8.0/full-rv32imaf-rom0k-l24k/inputs/cpu/VexRiscv_MmuFpu.v'
foreach ($Case in @(@('baseline','none'), @('transport','transport'), @('debug','cpu'))) {
  $Name, $Mode = $Case
  $CPU = if ($Mode -eq 'cpu') { 'build/cpu-debug/VexRiscv_MmuFpu.v' } else { $BaselineCPU }
  & $Python scripts/build.py --generate-only --profile full --boot-mode xip `
    --rom-size 4096 --debug-mode $Mode --cpu-verilog $CPU --place-option 3 --route-option 2 `
    --output-dir "build/resource-$Name"
  if ($LASTEXITCODE -ne 0) { throw "RTL generation failed: $Name" }
  Push-Location "build/resource-$Name/gateware"
  try {
    (Get-Content run.tcl -Raw).Replace('run all','run syn') | Set-Content synthesis-only.tcl
    & C:/Gowin/Gowin_V1.9.12.04_x64/IDE/bin/gw_sh.exe synthesis-only.tcl
    if ($LASTEXITCODE -ne 0) { throw "Synthesis failed: $Name" }
    (Get-Content run.tcl -Raw).Replace('run all','run pnr') | Set-Content pnr-only.tcl
    & C:/Gowin/Gowin_V1.9.12.04_x64/IDE/bin/gw_sh.exe pnr-only.tcl
  } finally { Pop-Location }
}
& $Python scripts/jtag_resource_report.py
```

当前工作区的工具配置已复制至本 worktree 的忽略文件 `.tools.local.json`。
完整本地证据在 `build/resource-{baseline,transport,debug}`；
`build/jtag-resources.json` 保存原始资源摘要、CPU/RTL/约束/报告哈希和布局状态。
各组有 `build-info.json`，对应 `build/recipes/resource-*` 保存源码 patch、未跟踪输入、
CPU RTL/元数据与复现参数；recipe 文件哈希已验证。它们是资源实验输入，没有 pin 为 current。
Gowin `run pnr` 本次还会重新执行综合，不能据其名称推断跳过综合。
许可证服务间歇返回 `Connection timeout` 或 `Server not responding`，重试才继续运行。

配置回归 `tests/config_test.py`、修改文件 Python 编译检查与 `git diff --check` 通过。
另行检查调试 RTL/模式不匹配时拒绝生成、调试 MMIO 地址、两组 JTAG 6 MHz pad 约束通过。
没有编程板卡、写 Flash 或变更 current 引用。此分支按用户要求作为失败实验归档提交；不创建发布版本。
关键测量摘要同时保存在可随 Git 检出的 [jtag-resource-results.json](jtag-resource-results.json)。
