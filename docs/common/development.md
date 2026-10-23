# 开发环境

Linux/NixOS 开发环境使用仓库根目录的 Nix flake：

```sh
nix develop
python scripts/mini.py --help
python scripts/mini.py doctor
```

flake 使用锁定的 Nixpkgs 26.05 提供 uv，由 uv 按 `.python-version` 安装和管理 Python 3.10.20、虚拟环境及 `requirements.lock` 中的 LiteX/Migen 依赖；同时提供 xPack RISC-V GCC 15.2.0-1、JDK 8 和 sbt-launch 1.9.7。首次进入时同步依赖并配置忽略提交的 `.tools.local.json`。CPU RTL 生成所需的 `JAVA_HOME`、`SBT_LAUNCH_JAR` 和兼容的 `VEXRISCV_SOURCE` 也会自动设置，可用 `sbt` 命令启动 sbt。Gowin EDA 使用 [Gowin IDE Wrapper](https://github.com/bearice/gowin-ide-wrapper) 提供的 FHS 启动器；无 X/Wayland 显示时自动使用 Qt offscreen。EDA 本体因需注册下载，不放入仓库或 Nix store。将发行包解压到 `~/Gowin_V1.9.12.04_linux`，或在进入 shell 前设置 `GOWIN_HOME` 指向解压目录。使用其他已许可版本时也通过 `GOWIN_HOME` 指定。

Windows PowerShell 仍可使用独立 Python 环境、Gowin FPGA Designer 和 xPack RISC-V GCC。机器路径写入忽略提交的 `.tools.local.json`。

```powershell
.\scripts\bootstrap.ps1
. .\scripts\env.ps1
& $MiniPython .\scripts\mini.py doctor
& $MiniPython .\scripts\mini.py plan gateware --machine <machine>
```

`bootstrap.ps1` 可重复执行；机器硬件参数由 JSON 指定，构建入口自动生成受影响 IP。各机器的 CPU/启动镜像要求见平台页。

所有目标都通过 `mini.py build <target>` / `mini.py plan <target>` 调用，旧独立构建脚本已删除。组件默认保存到 `build/store`，临时对象在 `build/work`，请求、工具环境、完整 flags 和结果在 `build/runs/<run-id>`。`--output-dir` 可创建系统输出视图或指定上游输出目录。跨 build 复用、CPU 与上游目标、导出和复现详见 [组件构建](component-builds.md)。历史 catalog/archives/recipes 保留，查询使用 `mini.py builds list`，发布使用 `mini.py release create/export/verify`。

全新 checkout 在 Windows 上的两个环境前提：先执行 `git config --global core.longpaths true`，
否则 `uv pip sync` 克隆 `pythondata-cpu-vexriscv` 的嵌套子模块会因路径过长失败
（`Filename too long` → `invalid index-pack output`）；若全局 uv 缓存不可用
（`Failed to initialize cache ... sdists-v9\.git`），把 `UV_CACHE_DIR` 指到短路径，例如
`$env:UV_CACHE_DIR='C:\uv-cache-mini'`。构建把编译器临时目录重定向到组件 work/tmp，
`%TEMP%` 不可写时也不会失败。

系统输出视图的 `validation.json` 记录两级固件的二进制大小；ELF 保留 text/data/BSS 信息，综合构建还记录
实际 PnR 的 Logic、Register、CLS、BSRAM、I/O 和所有时钟资源。缺失必要资源或 setup/hold 报告会使
构建失败。`board_test` 字段只表示构建脚本未做上板测试，实板结果记录在独立验收 JSON 和验证文档中。
软件构建不产生新的 PnR 证据。相同 machine/target 的重复构建复用同一 workspace；不同尝试以 purpose 和 run 收据区分。
