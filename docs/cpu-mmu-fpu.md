# CPU 与 MMU/FPU

默认 full 使用已提交的 `gateware/vendor/vexriscv/VexRiscv_Mmu.v`，RV32IMA + Zicsr/Zifencei，CPU 与 Wishbone 均为 60 MHz；C、B、D 扩展未启用。软件使用 ilp32 ABI，整数调用约定，FPU 指令按构建能力启用。

CPU 包含 2 KiB I-cache、2 KiB D-cache，32-byte cache line，各一 way；I/D TLB 各四项。移位与整数乘除均为 single-cycle 实现，不开 debug。单精度 FPU 支持加减、乘法/FMA、除法、平方根和转换，不提供双精度；FPU 变体不随仓库提交，需要时本地生成。生成参数使用 `withDouble=false, withDivSqrt=false, withDiv=true, withSqrt=true`。

MMU 与 FPU 由独立 flag 选择：

| MMU | FPU | 核 |
| --- | --- | --- |
| 关闭 | 关闭 | 内置 VexRiscv lite，RV32IM |
| 关闭 | 开启 | VexRiscv_Fpu.v |
| 开启 | 关闭 | VexRiscv_Mmu.v（full 默认） |
| 开启 | 开启 | VexRiscv_MmuFpu.v |

MMU 核使用 Linux CSR 配置，硬件提供 M/S/U 特权级及 Sv32、页权限和特权返回。当前应用在 M mode 裸机运行，`test mmu` 只检查 MPRV 下的 Sv32 数据 load；没有实现内核、用户进程、系统调用或隔离验收。设备 DMA 不受 CPU MMU 管理，没有 IOMMU。RTOS 浮点上下文保存尚未实现，IRQ 代码不使用浮点。

## 生成与构建

生产核（MMU、无 FPU）与其它第三方硬件一样以“出厂物 + 参数记录”入库：[`gateware/vendor/vexriscv/`](../gateware/vendor/vexriscv/) 保存 `VexRiscv_Mmu.v`、`VexRiscv_Mmu.yaml`、`generator.json`、`PROVENANCE.json` 与 `LICENSE.VexRiscv`。`generator.json` 记录生成命令、`resolved_flags`、`fixed_parameters`（2048 B I/D cache、3G 堆、high-MMIO/XIP 内存图）和 `source_lock`（生成器脚本与模板哈希、上游 checkout 文件哈希、Java/sbt 身份）；`mini.py` 每次构建前用 `locked_cpu` 校验 sha256 与参数，文件缺失或被改动就报错并提示重新生成。表里其余三种核不随仓库提交，需要时本地生成。

```powershell
# 调整生产核参数后重新生成并入库（字节不一致时拒绝覆盖，确认后加 --force）
.venv/Scripts/python.exe scripts/mini.py build cpu --generate --source C:\Users\bearice\Workspace\TangPrimer-20K\tools\vexriscv --java C:\Users\bearice\Workspace\TangPrimer-20K\tools\jdk8\jdk8u504-b01\bin\java.exe --sbt-launch C:\Users\bearice\Workspace\TangPrimer-20K\tools\sbt-launch-1.9.7.jar --vendor-dir gateware/vendor/vexriscv
# 其它能力组合只在本地生成，用 --cpu-verilog 覆盖
.venv/Scripts/python.exe scripts/mini.py build cpu --generate --source <源码目录> --java <JDK8>/bin/java.exe --sbt-launch <sbt-launch.jar> --output-dir build/cpu-features
.venv/Scripts/python.exe scripts/mini.py build system --profile full --cpu-verilog build/cpu-features/VexRiscv_MmuFpu.v --feature mmu=on --feature fpu=on --synthesize
```

CPU RTL 的生成入口是 [`scripts/builders/cpu_generator.py`](../scripts/builders/cpu_generator.py)（`mini.py build cpu --generate`）；上游 VexRiscv 固定为 `b6118e5cc2a33323425df6455697139021d50c72`，Java 8 与 sbt-launch 1.9.7 由 `mini.py` 自动发现，仅供重新生成时使用，构建与复现不再依赖它们。当前资源总量见 [系统设计](system-design.md)，缓存路径见 [L2](l2-cache.md)。不同 CPU/外设组合需要重新综合、布局布线和上板检查；full 通过不能替代其他组合的验证。

生成器使用原生 VexRiscv cache 和 fence 实现，不再注入外部 fence/atomic 补丁或导出握手端口。`--compressed` 可启用 C 扩展；能力元数据记录 `compressed=true`，构建据此使用带 `c` 的 ISA 编译 ROM 和应用，MMU 核的 `misa.C` 同步启用。默认生成仍关闭 C。使用 C 时 I-cache 的上游配置会关闭 `twoCycleCache`，因此必须重新检查 CPU 时序，不能把它当作只有编译器的改动。

```powershell
.venv/Scripts/python.exe scripts/mini.py build cpu --generate --source <源码目录> --java <JDK8>/bin/java.exe --sbt-launch <sbt-launch.jar> --output-dir <C核输出目录> --compressed
.venv/Scripts/python.exe scripts/mini.py build system --cpu-verilog <C核输出目录>/VexRiscv_Mmu.v --rom-size 4096 --output-dir <构建输出目录> --synthesize
```

如果完整外设组合的 C 版本在取指路径上出现 setup 违例，可在生成阶段加
`--pipelined-fetch`，恢复两周期 I-cache 并加入 injector 寄存器；这只是时序实验选项，
不会改变默认的非 C 配置。C + 取指流水线在 ACK 延迟 1/9/31 周期时通过压缩指令、跨 cache line
取指、FENCE.I、AMO 和 LR/SC 的 RTL 仿真；独立 I/D 总线模型的 127-cycle ACK 自修改代码测试仍失败。
已验收的完整外设 C 配置及其 PnR/实板结果见 [调试与验收纪要](debug-c-pnr-handoff.md)，不能把有限实板测试扩展成任意总线延迟下的保证。
