# CPU 移位与乘法选项资源实测

2026-10-13，Gowin V1.9.12.04，GW2A-LV18PG256C8/I7。四组使用相同完整系统、MMU、无 FPU/C、I/D cache 各 2 KiB、16 KiB L2、8 KiB ROM、CPU60/DDR120 MHz。只改变两个算术选项。

下表是四组单纯综合的资源对照，不是 CLS 或实板结果；后文另记录两个选项都开启时的完整 PnR。板上镜像没有被本实验更换。

| shift / muldiv | Logic（增量） | LUT（增量） | Register（增量） | BSRAM | DSP 原语 |
| --- | ---: | ---: | ---: | ---: | --- |
| false / false | 18757 (+0) | 16042 (+0) | 10476 (+0) | 40 (+0) | 0 |
| true / false | 18852 (+95) | 16136 (+94) | 10504 (+28) | 40 (+0) | 0 |
| false / true | 18412 (-345) | 15705 (-337) | 10468 (-8) | 40 (+0) | 4 MULT18X18 + 2 ALU54D |
| true / true | 19003 (+246) | 16292 (+250) | 10496 (+20) | 40 (+0) | 4 MULT18X18 + 2 ALU54D |

Logic 是 Gowin 的综合资源口径，包含 LUT、ALU 和 RAM16 的等效逻辑；不是纯 LUT 数。所有组 ALU/RAM16 明细、CPU 与报告 hash 保存在配套 JSON。

只开移位增加 95 Logic、94 LUT、28 Register。只开乘法减少 345 Logic、337 LUT、8 Register，代价是 4 个 MULT18X18 和 2 个 ALU54D 原语。两个都开增加 246 Logic、250 LUT、20 Register。BSRAM 不变。

DSP 部分已经在映射网表中确认属于 CPU 的 execute_MUL_* 和 memory_MUL_LOW_*。DSP 原语可能共用物理 DSP 宏资源，不把上述六个原语直接称为六个 DSP block。

重新生成的关闭基线与当前归档 VexRiscv_Mmu.v 逐文本比较，仅 Git 注释不同，硬件内容一致。四组 CSR JSON 完全相同。两个开启选项不改变 RV32IMA ISA、缓存容量或 BIOS ABI。

singleCycleShift=true 选择 FullBarrelShifterPlugin，false 选择 LightShifterPlugin。singleCycleMulDiv=true 选择 MulPlugin 与 DivPlugin；MulPlugin 在 execute/memory/writeback 分段计算乘积，DivPlugin 仍然是 divUnrollFactor=1 的迭代除法器。因此不把这个选项理解为退休延迟都为一个周期。

两个选项的组合增量不是两个单项增量的简单相加。本次仅记录实际结果，未唯一定位造成资源交互差异的具体综合优化。

只开移位第一次工具启动失败（退出1、空日志），保留在 build/cpu-arithmetic-resource/s1m0；同参数串行重跑在 s1m0-retry 综合通过，不把启动失败当成资源超限。

复现：使用 scripts/cpu_generate.py --arithmetic-matrix --pipelined-fetch 生成四种 MMU CPU；然后 scripts/cpu_qualify.py --synthesis-only --profile full --without fpu --sd-profile lite --usb-backend ultra --audio-clock dds --l2-size 16384 --rom-size 8192 --boot-mode rom，指定同一 boot.bin 和每个 CPU RTL。完整 CPU 命令位于 generator.json。工具源码快照位于本地 build/cpu-arithmetic-resource/inputs。

证据目录：build/cpu-arithmetic-matrix、build/cpu-arithmetic-resource/s0m0、s1m0-retry、s0m1、s1m1。各目录 qualification.json、project_syn.rpt.html 和 project.vg；配套 summary.json 保存 hash。

## 两个选项都开启的完整 PnR

同配置的新 captured build 已完成编译、综合、布局布线：place2/route2，setup/hold 为 0/0。

| 资源 | 实际使用 / 总量 | 剩余 |
| --- | ---: | ---: |
| Logic | 19190 / 20736 | 1546 |
| Register | 10528 / 16173 | 5645 |
| CLS | 10066 / 10368 | 302 |
| BSRAM | 40 / 46 | 6 |
| DSP | 4 / 24 | 20 |

PnR 的 DSP 使用量为 4/24；其原语为 4 MULT18X18 和 2 ALU54D，不能把原语数量相加当作物理 block 数量。

构建 ID：a50a747-full-rv32ima-rom8k-l216k-doom-fast-arithmetic-15a6d0d6-5381debf。

目录：build/runs/20261012T152652866716Z-a50a747-dirty-full-rv32ima-rom8k-l216k-doom-fast-arithmetic。

FS SHA256：15a6d0d6e554d8e33a95687bb2b23956be4435174e7fa44ac451b2bad9b68df1。

实际 ABI 仍为 a1e130bc。新 CPU 只改变实现，不改变 RV32IMA 指令集；BIOS 编译完成。

该候选已证明资源和时序通过，尚未实板验收，也没有测量新 CPU 的 Doom 帧率。本实验未编程板卡、未写 Flash、未修改 current，板上仍运行此前 16 KiB L2 的迭代算术 CPU。

配置、镜像 hash、PnR 报告 hash 及综合矩阵见配套 JSON。
