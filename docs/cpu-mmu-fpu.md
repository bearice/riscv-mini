# CPU 与 MMU/FPU

默认 full 使用生成的 `VexRiscv_MmuFpu.v`，RV32IMAF + Zicsr/Zifencei，CPU 与 Wishbone 均为 60 MHz；C、B、D 扩展未启用。软件使用 ilp32 ABI，整数调用约定，FPU 指令按构建能力启用。

CPU 包含 2 KiB I-cache、2 KiB D-cache，32-byte cache line，各一 way；I/D TLB 各四项。整数乘除与移位使用迭代实现，不开 debug。单精度 FPU 支持加减、乘法/FMA、除法、平方根和转换，不提供双精度。生成参数使用 `withDouble=false, withDivSqrt=false, withDiv=true, withSqrt=true`。

MMU 与 FPU 由独立 flag 选择：

| MMU | FPU | 核 |
| --- | --- | --- |
| 关闭 | 关闭 | 内置 VexRiscv lite，RV32IM |
| 关闭 | 开启 | VexRiscv_Fpu.v |
| 开启 | 关闭 | VexRiscv_Mmu.v |
| 开启 | 开启 | VexRiscv_MmuFpu.v（full 默认） |

MMU 核使用 Linux CSR 配置，硬件提供 M/S/U 特权级及 Sv32、页权限和特权返回。当前应用在 M mode 裸机运行，`test mmu` 只检查 MPRV 下的 Sv32 数据 load；没有实现内核、用户进程、系统调用或隔离验收。设备 DMA 不受 CPU MMU 管理，没有 IOMMU。RTOS 浮点上下文保存尚未实现，IRQ 代码不使用浮点。

## 生成与构建

`scripts/cpu_generate.py` 使用兼容的 VexRiscv checkout（固定版本 `b6118e5cc2a33323425df6455697139021d50c72`）、Java 8 和 sbt-launch 1.9.7，生成四种 RTL 与能力/哈希元数据。构建检查显式 CPU RTL 与 feature flags 是否一致；不要只改编译器 ISA 而不改硬件。

```powershell
.venv/Scripts/python.exe scripts/cpu_generate.py --vexriscv-source <源码目录> --java <JDK8>/bin/java.exe --sbt-launch <sbt-launch.jar> --output-dir build/cpu-features
.venv/Scripts/python.exe scripts/build.py --profile full --l2-size 4096 --synthesize
```

CPU RTL 和能力元数据由 [`scripts/cpu_generate.py`](../scripts/cpu_generate.py) 生成；Java 8、sbt-launch 与 VexRiscv checkout 是外部构建依赖，不是仓库内归档。当前资源总量见 [系统设计](system-design.md)，缓存路径见 [L2](l2-cache.md)。不同 CPU/外设组合需要重新综合、布局布线和上板检查；full 通过不能替代其他组合的验证。
