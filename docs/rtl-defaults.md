# Verilog 模块边界与转换语义

默认按 SoC 直接子模块输出独立 Verilog 文件，模块内部 FSM/FIFO/CSR 内联。CPU 与 USB 等外部核仍使用独立源文件；默认 full 当前生成 32 个 SoC 文件模块。`--deep-verilog` 保留内部完整层级，`--flat-verilog` 输出整体对照。文件映射见构建的 `gateware/rtl-manifest.json`。

`gateware/rtl.py` 在层级转换期间维护三个语义约束：

- `preserve_signal_defaults` 为没有实际赋值、但跨模块变成 wire 的内部 Signal 生成 reset 常量驱动，排除顶层 IO、时钟/复位和已有驱动；尊重 `regs_init=False`。
- `normalize_clock_domains` 与临时 `_prepare_fragment` 包装保留最终 FHDL 同步语句顺序，防止 inline 合并移动复位条件，改变最后一次非阻塞赋值的优先级。包装在 finally 恢复，不修改依赖安装包。
- `remove_shared_aliases` 按真实实例所有权只序列化模块一次，防止 SharedIRQ 别名过滤删除真正的 OR 逻辑。

`tests/rtl_defaults_test.py` 检查常量驱动、复位优先级与共享 IRQ；深层路径也必须保留这些语义。静态无未驱动线不等于功能正确，常量初始化可能掩盖缺失逻辑。

Gowin 设置 `netlist_hierarchy=0`，保留源文件边界，同时允许综合跨模块优化。默认 `timing_driven=1`、place=3、route=2。Retiming 仅在明确传入并检查工具实际设置后才可认为启用；默认不开启。资源与时序见 [系统设计](system-design.md)。
