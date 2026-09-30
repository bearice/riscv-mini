# 基础系统验证记录

日期：2026-10-01。本页对应去除阶段/提频分支后的单一 Flash/UART 基础系统；历史 M4 压力测试不作为本页当前验收的替代。

## 构建与分离

| 项目 | 当前结果 |
| --- | --- |
| CPU / sys / DDR | VexRiscv lite，60 / 60 / 120 MHz，DDR DLL-off CL6/CWL6 |
| boot ROM / SRAM | 8 KiB / 8 KiB |
| ROM 中 boot.bin | 7,040 字节；`-Os -flto`；没有 SD/FatFs/LCD 软件驱动 |
| DDR app.bin / app.img | 19,420 / 19,468 字节；含 Flash、SD/FatFs、两块 LCD 驱动 |
| 应用地址 / ABI | `0x40800000` / `0x008772f2` |
| Logic / Register | 7383/20736 / 3885/16173 |
| BSRAM / rPLL | 16/46 / 2/4；旧 M4 使用 46/46 BSRAM |
| PnR setup / hold 违例 | 0 / 0 |

boot.bin SHA256：`019344ef903eeffc79a3e4f902649d507329cc61a2d03534f059307f4f21ec13`。

app.img SHA256：`bc0b462a9660b98228d6c71e0dbd5034b3faca779a4a4229492d64107a39f158`。

bitstream SHA256：`e8c09051591231633671438c72bc29b851c20fe232c7ebab0c465199641111eb`。

环境 doctor、Python 语法、SPI 事务、共享 native DDR 调度、LCD 扫描及注入 underrun 恢复仿真通过；镜像协议 4 个测试及 Gowin 错误日志识别 2 个测试通过。验证脚本不把退出码零加 `Finished` 当作下载成功：观测到的 `SPI Verify failed` 必须拒绝。

## Flash/UART 板级验收

最终优化 ROM 的 `boot_verify.py --program --install` 已通过：

- DDR 初始化/训练、有界启动检查。
- 实测 Flash JEDEC `0x0b4017`，物理容量 8 MiB。
- UART 分包接收、DDR 全 payload CRC、DDR 应用执行，`SYSTEM READY` 中 SD / SPI LCD / RGB LCD 均为 1。
- SD 只读根目录列出已有 `RVTEST00.BIN`；基础命令 help/status/ls 返回提示符。
- Flash 页写入、payload 读回 CRC、最后提交镜像头，从 Flash 载入 DDR 执行。
- 两次自动 Flash 软件重启；视频欠载为零。
- CPU 固件更新前后配置保留区 CRC 同为 `648c5100`；此值对应首次持久配置更新前的内容，配置更新后应重新记录。
- 拒绝坏头 CRC、错误 ABI、ROM/视频 load、零长度、超大长度、不对齐/越界 entry、非零 flags、坏包 CRC、UART 截断超时、payload CRC 不一致；均返回恢复提示符。

普通 Gowin exFlash Erase/Program/Verify 和普通 Verify 均报 `SPI Verify failed`，不能把工具退出码或 `Finished` 当作校验成功；该路径的错误根因未确认。最终通过 boundary-scan 写入配置，并由 CPU 独立读取完整前 2 MiB：CRC `4c189167` 与 Gowin `project.bin` 补 `0xff` 到 2 MiB 后的 CRC 完全一致。Gowin Reprogram 从 Flash 重新配置 FPGA 后，DDR 初始化、固件安装和 Flash→DDR 应用执行通过；安装前后配置区 CRC 都为 `4c189167`。随后最终持久配置再次通过配置 CRC、Flash 应用装载、两次自动 Flash 软件复位、SD 根目录只读检查；三个应用启动的欠载均为零。

本轮未追加五分钟持续运行；用户要求跳过断电检查并提交，当前验收范围为启动/功能回归。已提供可选 `--soak-seconds 300`，范围为 SD 根目录读取、Flash ID/状态、视频 DMA 帧计数持续增长与零欠载；不宣称做历史的全内存或整帧内容校验。

断电冷启动：**按用户明确要求跳过**；软件复位和 FPGA Reprogram 不替代这个项目。实体 RGB LCD 当前初始画布是黑色，旧色条/灰阶/角标已移除；SPI LCD 显示 RISCV MINI / DDR 128 MB / SD CARD READY。本轮没有新的人工画面确认。

原始证据：`build/base/validation.json`、`boot-verification.json`、`boot-verification-uart.log`、`configuration-programmer.log`、`configuration-verification.json`、`configuration-reload.log`、`configuration-reload-uart.log`、`configuration-resume-console.log`、`persistent-boot.json` / `persistent-boot-uart.log`、`cold-boot.json` / `cold-boot-uart.log`。构建目录忽略提交，可用 README 中的命令再生成。
