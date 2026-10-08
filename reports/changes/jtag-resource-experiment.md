# JTAG CPU debug resource experiment archive

用户要求“提交分支供日后参考”。本次为失败实验分支的归档，不是可用 gateware 的验收或发布。

保留可选 JTAGBone/CPU debug 参数、测量提取脚本与实验报告。完整调试增加 935 Logic、342 Register、167 CLS；两组候选均布线失败，没有调试 bitstream，也没有板测、性能验收或发布版本递增。详见 docs/jtag-resource-experiment.md 与 docs/jtag-resource-results.json。

检查：18 项 workflow 回归、配置回归、Python 编译、暂存 diff 检查通过；实验三组综合/PnR 结果按原始状态保留。暂存指纹在本地保存并用 prepare_commit --verify 校验。

生产 gateware SOP 的 board-qualified 构建、性能基线和最终发布重建不适用于用户明确要求保存的失败实验；没有伪造这些验收。提交后仅封存本次原始证据和输入，保留原始 dirty 构建身份，并关联最终实验提交。
