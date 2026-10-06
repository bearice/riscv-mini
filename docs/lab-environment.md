# 本机测试环境

本页记录这台主机与这块 Tang Primer 20K 的实际连接与已确认的现场条件，供上板测试、基准与实板验收直接引用，避免每次会话重新查询或猜测。器件与电气事实见 [板级参考](board-reference.md)，命令语义见 [固件测试命令](firmware-tests.md)。

记录日期：2026-10-06。主机侧可变的值（接口序号、IP）在每次运行前用文末命令复核；本页给出的是当前已验证的配置，不是固件里的配置。

## 主机与工具

- 工具路径以 `.tools.local.json` 为准：Gowin `C:\Gowin\Gowin_V1.9.12.04_x64\IDE\bin\gw_sh.exe`、`C:\Gowin\Gowin_V1.9.12.04_x64\Programmer\bin\programmer_cli.exe`、xPack `C:\xpack-riscv-none-elf-gcc-15.2.0-1\bin\riscv-none-elf-gcc.exe`、`C:\ProgramData\chocolatey\bin\make.exe`、`C:\msys64\ucrt64\bin\openFPGALoader.exe`。
- Python 用仓库内 `.venv`（Python 3.10.21），即 `$MiniPython = .venv\Scripts\python.exe -X utf8`；它同时满足各历史版本的 `requirements.lock`。
- CPU RTL 生成需要 Java 8 与 sbt-launch 1.9.7，VexRiscv 固定在 `b6118e5cc2a33323425df6455697139021d50c72`，已放在仓库外的 `..\tools\`（`jdk8\jdk8u504-b01\bin\java.exe`、`sbt-launch-1.9.7.jar`、`vexriscv\`）。
- 子进程只能写 `riscv-mini\` 目录内：`%TEMP%` 与 `..\tools\` 对它们只读。因此构建/上板脚本必须把 `TMP`/`TEMP` 指到工作目录内的 `build\.tmp`，sbt 的 Coursier 缓存必须落在仓库内（`build\java-cache\coursier`）。历史版本在 detached worktree 里运行时用 `build\worktrees\<tag>\build\.tmp`。

## 串口与配置下载

- 控制台：`COM4`，115200 8N1，沙箱内的子进程可以直接打开。
- 下载参数（`scripts/boot_upload.py` 的 `program()`）：`--cable-index 4 --location 107569 --frequency 2MHz --device GW2A-18C --operation_index 2 --fsFile <bitstream>`。判定成功要求输出含 `Finished` 且无 error/failed。

## SD 卡

- 卡常驻插在核心板 J2，SDHC，`sectors=0747b000`；`test sd` 的验收文件存在。
- 跑过使用 SPI SD 的构建（例如 v0.1.0）之后，卡被留在 SPI 模式，必须整机断电重启或重新拔插才能回到 native SD；板上没有 CPU 可控的卡电源开关，见 [native SD](native-sd.md)。按版本顺序做实板测试时，这一点是 v0.1.0 与 v0.2.0 之间的固定步骤。

## 麦克风与音频

- 两只麦克风已连接，`test mic` 与 `test mic stereo` 可以执行。
- 音频输出不做主机侧听音采集：不运行 `scripts/monitor_external_verify.py --audio`，也不接 Line In。验收边界是 `test audio`、`test audio start/pause/resume/stop` 无 error，underrun 计数符合测试预期。听音确认是另一件事，需要显式安排。

## USB Host 口

- 做历史版本实板测试时 USB Host 口不接 hub 或设备。已确认：接 hub 时 v0.2.0 的 TinyUSB 主机枚举走到 `TU_ASSERT` 失败路径，该路径执行 `ebreak`，而这一 CPU 配置不含 EBREAK 支持（生成时即有 "without software ebreak instruction support" 警告），于是 `FAULT cause=00000002 pc=4080a150 value=00100073`，应用停在异常处理里。拔掉 hub 后同一构建全部通过。
- v0.4.0 起有 hub 拓扑支持（`test usb tree`），需要 hub 时才接。

## Ethernet 与网络基准

- 板侧 BIOS 设置默认 `IP=169.254.20.20`、`server=169.254.25.153`；`set ip` / `set server` 只改内存值，不做 `settings save`。
- 主机侧用 USB 网卡：`Realtek USB GbE Family Controller #2`，接口名 `Ethernet 8`，ifIndex 80，MAC `00-E0-4C-09-07-44`。它必须持有 `169.254.25.153`（link-local /16，与板子的 `169.254.20.20` 同链路），`--host-ip 169.254.25.153` 才可用。
- 不要误用其它接口：`Ethernet 5`（Intel X550-T2 #2，ifIndex 5，`192.168.10.128/24`，走 DHCP）和 `vEthernet (Default Switch)`（`172.30.224.1`）都不是板卡链路。
- 2026-10-06 实测：该 USB 网卡没有分配 IPv4，`MediaConnectionState=Unknown`、`LinkSpeed 0 bps`，所以 `--suite net` / `--host-ip` 的 TFTP 基准当时不可用；历史版本的基准只跑 `--suite all`。运行前用下面的命令确认。

```powershell
Get-NetAdapter | Select-Object Name,ifIndex,Status,MacAddress,InterfaceDescription
Get-NetIPAddress -AddressFamily IPv4 | Select-Object IPAddress,InterfaceIndex,PrefixOrigin
```

## 各版本的主机侧入口

历史版本在 `build\worktrees\<tag>` 的 detached worktree 里运行，脚本用该提交自己的版本，不借用 HEAD 的脚本。

| 版本 | 上板验收 | 基准 |
| --- | --- | --- |
| v0.1.0 | `scripts/boot_verify.py`（该提交没有 `firmware_verify.py`） | 无 |
| v0.2.0 | `scripts/firmware_verify.py` | 无 |
| v0.3.0 | `scripts/firmware_verify.py` | 无 |
| v0.4.0 | `scripts/firmware_verify.py` | `scripts/benchmark.py --suite all`，可选 `cpu/mem/io`，无 `cache` |
| v0.5.0 | `scripts/firmware_verify.py` | `scripts/benchmark.py --suite all`，可选 `cpu/mem/cache/io/net` |
| v0.6.0 | `scripts/firmware_verify.py` | `scripts/benchmark.py --suite all`，可选 `cpu/mem/cache/io/net` |
| v0.7.0 | `scripts/firmware_verify.py`（HEAD） | `scripts/benchmark.py --suite all` 与 `--suite cache` |

`--suite net` 需要 `--host-ip`；`benchmark.py` 结束时总会执行 `test bios` 并要求 `BIOS TEST PASS`，所以它只能在已运行 monitor 的板子上跑，且要求该版本有 BIOS。

## 已知的现场抖动

- 连续多次下载 bitstream 后，boot ROM 偶发 `Hardware DDR initialization failed; reset required`（`scripts/boot_upload.py` 的 `menu()`）。断电重启或重新复位后同一构建正常，属于现场状态而不是构建缺陷；记录它以免把一次重试误判成版本回归。
