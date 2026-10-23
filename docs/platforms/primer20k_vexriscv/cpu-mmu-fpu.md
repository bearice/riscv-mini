# CPU 与 MMU/FPU

> 适用机器：`primer20k_vexriscv`。配置以机器 JSON 和所选硬件产物的导出为准。

当前 CPU 配置来自机器 JSON 的 `ip.cpu.parameters`。MMU、单精度 FPU、压缩指令、
I/D-cache 大小、整数移位/乘除和取指流水线均由该输入驱动单核 VexRiscv 生成器。
完整字段与能力组合见 [CPU、SD 和音频配置](configuration-profiles.md)。

```sh
python scripts/mini.py build cpu --machine primer20k_vexriscv
python scripts/mini.py build system --machine primer20k_vexriscv
```

MMU 与 FPU 独立配置。前端从同一快照推导 CPU variant、ISA/ABI 和软件功能头文件，
再验证生成 RTL 与元数据一致。不存在 CLI 特性覆盖或按文件名选择外部 CPU 的路径。
带 FPU 不自动改变机器 ABI；调用约定由 `cpu.abi` 明确规定。

生成器消费锁定的 LiteX CPU 模板与 VexRiscv 上游源码，使用 Java 8/sbt。
源码、模板、工具及参数改变会使对应 IP 失效；生成失败不继续消费旧配置的 RTL。
缓存、生成记录和不可变 IP 产物分别保存在工作区与 store。历史 vendor 文件及旧命令
仅用于其对应的归档重放。

支持 Sv32/M/S/U 或 FPU 不表示已有完整操作系统、隔离机制或所有软件的浮点支持。
当前资格必须绑定到具体硬件和软件产物；改变 CPU/外设组合后需要相应的生成、
编译、时序与实板验证，不能继承其他组合的结果。
