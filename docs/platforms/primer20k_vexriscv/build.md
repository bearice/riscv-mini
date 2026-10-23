# 20K 组件构建

适用机器：`primer20k_vexriscv`。使用前确认 current 或传入的硬件身份属于本机器。共同规则见 [组件构建](../../common/component-builds.md)。

## 常用操作

```text
python scripts/mini.py configure --target bios
python scripts/mini.py plan bios --hardware current
python scripts/mini.py build bios --hardware current
python scripts/mini.py build system --hardware current
```

`build bios` 固定所选硬件，构建当前源码的 HAL SDK 和 BIOS，创建候选系统。未受软件改动影响的硬件组件从选定组合复用，不运行 Java 或 Gowin。`build` 和 `plan` 都必须明确指定目标。修改源码或版本只影响相关组件的输入闭包；不会因为全仓 HEAD 改变而强制重建所有组件。

`plan` 打印 REUSE/BUILD 及原因。首次使用旧 catalog/归档时会校验并导入 store，不移动或删除原目录，不改变 current。初次 HAL SDK 需要编译，因为旧系统中的 HAL 是静态链接进 BIOS 的，不能从旧 ELF 假装取得一份可复用库。


重新生成硬件使用 `mini.py build system --machine primer20k_vexriscv`，参数见 [配置说明](configuration-profiles.md)。
