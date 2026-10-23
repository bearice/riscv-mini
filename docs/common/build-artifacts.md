# 构建目录与产物身份

所有机器共用产物管理机制；目录与锁的完整规则见 [组件构建](component-builds.md#目录所有权)。

| 位置 | 职责 |
| --- | --- |
| `build/store/<component>/<digest>` | 不可变交付产物与依赖清单 |
| `build/work/<machine>/<target>/<workspace>` | 稳定工作区与增量缓存；默认 `default` |
| `build/runs/<run-id>` | 构建、测试、板端操作的请求、结果与日志 |
| `build/refs/` | 明确选择的现役、基线与候选引用 |
| `build/releases/` | 明确导出的交付包 |
| `build/catalog/`、`build/recipes/`、`build/archives/` | 保留的历史登记、重放配方与归档 |
| `reports/` | 提交到 Git 的改动、验收和性能摘要 |

同一 machine/target 的重建沿用同一个 workspace，保留 Ninja、编译器和 Gowin 缓存；用 `--purpose` 和 run 收据区分尝试。普通构建只保存实际产物与增量元数据，不复制源码归档。

## 选择与验证身份

```sh
python scripts/mini.py builds list
python scripts/mini.py builds show current
python scripts/mini.py plan <target> --machine <machine>
```

`current` 是显式选择的已验收引用，不是最新候选。引用与输入产物必须属于目标机器；特别是已有 20K 的 current 不能作为 AE350 的硬件。跨平台操作优先传准确的 artifact/system digest 或 run 的 `result.json`，具体支持形式见各机器操作页。

产物 digest 标识实际内容，输入 key 决定复用；目录名和版本号不证明兼容性。PnR、板测、性能各自关联准确镜像，不能互相替代。比较必须显式选择同机器候选与基线，保留工作量、时钟和原始数据；具体套件见平台文档。

新操作写入 `build/runs/`；`build/operations/` 是保留的历史日志。旧目录不自动搬动、不改写绝对路径或验收结论。本地 `build/README.md` 是登记索引，不能替代产物校验。

## 清理、导出与历史复现

`mini.py clean` 和 `mini.py builds clean work` 先显示计划，`--execute` 才执行。保留 store、runs、固定引用及其依赖、release、recipe、vendor 和 Git 工作树。旧产物清理使用 `builds clean obsolete` 或明确 catalog ID；先审阅计划，未知目录保留。

交付包使用 `release export` / `release verify`。源码归档和正式发布只在明确请求时执行，见 [提交与发布 SOP](pre-commit-sop.md)。历史构建使用 `mini.py builds reproduce` 在其保留 checkout 执行自己的配方，不用当前机器参数补写历史事实。
