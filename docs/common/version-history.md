# 发布版本与溯源

版本是沟通标签，不证明兼容性或验收资格。发布记录必须区分源码提交、组件版本、准确产物身份和实际验证范围；不同机器不因版本相同而兼容。

组件版本记录在 `VERSIONS.yaml`，系统包的明确组合记录在 `RELEASES.yaml`。只有明确要求正式 release 时才更新版本和 CHANGELOG；普通提交不递增版本，不自动打 tag、构建或归档。具体流程见 [提交与发布 SOP](pre-commit-sop.md)。

发布包应记录完整 Git hash、机器、组件组合、参数、工具、二进制哈希和匹配验收证据。未构建或未验证的外部组件应明确列出，不用系统版本暗示已验收。引用 current、性能 baseline 与发布 tag 是不同的选择，分别记录。

20K 的合并版本、组件拆分、tag 与历史包实现见 [20K 发布里程碑](../platforms/primer20k_vexriscv/version-history.md)。其中 CSR ABI tag、BIOS、OpenSBI/U-Boot 与旧系统包的规则属于 20K；AE350 的交付范围见 [AE350 平台](../platforms/mega138k_ae350/README.md)。
