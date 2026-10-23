# 20K 板端操作与更新

适用机器：`primer20k_vexriscv`。通用 CLI 见 [操作入口](../../../scripts/README.md)，现场连接见 [测试条件](lab-environment.md)。先选择准确的20K硬件/系统身份，核对位流、应用、CSR ABI 与 PnR；`current` 必须属于本机器。

## 临时运行与恢复

```sh
python scripts/mini.py board status
python scripts/mini.py board recover --build <20K系统身份> --port <串口>
python scripts/mini.py board run --build <20K系统身份> --port <串口>
python scripts/mini.py board verify --build <20K系统身份> --suite firmware
```

`status` 被动观察 UART，静默表示 unknown。恢复/运行使用 FPGA SRAM 和 UART→DDR，不写 Flash。替换应用可传 `--image <app.img>`，入口检查 ABI 和范围；启动后先同步新的完整横幅，再判定 DDR 与应用就绪，不把旧日志或读回数据内的报错字符串当成本次故障。

操作请求、结果、UART 与工具日志写入 `build/runs/<run-id>/`。详细固件命令见 [固件测试](firmware-tests.md)，性能见 [benchmark](benchmark.md)。

## Flash 更新与验证

只有明确要求持久更新时才执行 `board update`。先检查完整 dry-run：

```sh
python scripts/mini.py board update --build <20K系统身份> --dry-run
```

去掉 `--dry-run` 才写入。配置、XIP 与应用区域来自所选构建的元数据；入口在写入前检查输入哈希、CSR ABI、分区和长度。ROM 与 XIP 的启动存储不同，旧 UART install 不能代替完整 XIP 更新。

| 路径 | 写入与验证边界 |
| --- | --- |
| 默认 programmer | op8 写 FPGA 配置、op31 Erase/Program 写应用/XIP、op1 reload；不执行 SPI Verify 或独立读回 |
| 显式 `--method auto` 的软件 SPI 流程 | XIP 情况先 SRAM 启动并上传 DDR 工具，确认关闭 XIP、busy=0、SPI CS 空闲后写应用/启动区，配置最后更新；按协议逐包校验和读回 |
| `board verify --suite flash` | 独立只读探针逐字节比较目标应用/XIP，再检查启动；不安装镜像 |
| `board verify --suite spi` | 检查 XIP 关闭与软件 SPI 交接、恢复；不写入 |
| `board repair-xip` | 单独修复 XIP 启动区并独立读回，会写 Flash，需明确请求 |
| `board verify --suite cold` | 等待外部断电重启，仅在用户明确要求时执行；不代替逐字节读回 |

软件 SPI 写入使用受限区域、JEDEC 检查、WEL/WIP、序号/CRC 和逐页确认。未确认 XIP 已静止则禁止写入；只读探针不包含写命令。读回比较完整区域并保存哈希，仅头有效、CRC 合法或下载器 Finished 不证明目标文件已精确写入。默认 programmer 的成功也不冒充独立读回通过。

单份 Flash 更新不具备原子性，失败按本次 `result.json` 给出的恢复说明处理。XIP 启动区损坏不能仅靠 SRAM 恢复完成持久修复。探针地址必须来自所选构建 CSR；旧产物缺少匹配 CSR/身份时预检失败，不猜测其他布局。

## 专项验收

firmware、boot、flash、spi、cold 各有不同前提。`--mic` 要求实际连接麦克风；综合 soak 需要相关功能启用；物理显示、听音、真实键鼠与数字合同分别记录。USB 无设备、网络不可达或未做冷启动都保留为未执行，不用其他测试结果补齐。

Linux、DOOM、外设专项入口分别见 [Linux](linux-port.md)、[DOOM](doom.md)、[Ethernet](ethernet.md)、[SD](native-sd.md) 和 [音频](audio.md)。复用现有工具，不临时拼接下载器或串口探针。

## TFTP 引导服务器

`boot net <文件>` 的名字须与主机导出的文件名一致；主机 UDP/69、接口地址和解释器须实际可达。RPB、OSB 与 bootloader `app.img` 是不同镜像格式，按各自校验和地址范围装载；`fetchraw` 保存原始数据，不用于绕过载荷头验证。

TFTP 和应用格式见 [BIOS](bios.md)，OS 交接见 [OpenSBI](opensbi-port.md)。服务端不默认修改主机防火墙、路由或网卡配置。
