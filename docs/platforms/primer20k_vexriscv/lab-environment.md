# 20K 实板测试条件

适用机器：`primer20k_vexriscv`。本页规定运行前应确认的外部条件；不保存某次 USB 枚举、网卡状态、工具绝对路径或历史端口号。

## 主机、串口与下载器

工具路径通过 `.tools.local.json` 与环境发现，先运行 `mini.py doctor`。串口使用实际设备和 `--port`，115200 8N1；FPGA location 默认每次编程前扫描，多候选时显式选择。Linux 上只处理 JTAG interface A 的占用，UART interface B 保持可用。板卡与 USB 的供电关系须按实际接线确认。

## SD、USB 与音频

- SD 测试前确认卡存在及测试文件；SPI 模式切回 native 时可能需要整机断电或重新插卡，见 [SD](native-sd.md)。写测试须明确选择，不覆盖已有文件。
- USB 按测试范围接入键鼠或 Hub；不能因 Host 无设备或不支持的速度而判为已有设备枚举通过，见 [USB](usb-light.md)。
- 麦克风按 [接线与格式](microphone.md) 核对后再执行对应测试。数字计数、录音采集、物理听音与声学响应分别记录；不默认启动主机录音。

## Ethernet 与 TFTP

测试前核对直连板卡的接口、ifindex、IPv4、链路与板端 server 设置。传给验收脚本的 `--host-ip` 必须属于该接口，不照搬文档中的示例地址。

```powershell
Get-NetAdapter | Select-Object Name,ifIndex,Status,MacAddress,InterfaceDescription
Get-NetIPAddress -AddressFamily IPv4 | Select-Object IPAddress,InterfaceIndex,PrefixOrigin
```

Linux 使用 `ip -o -4 addr` 与 `/sys/class/net/<接口>/ifindex` 查询。TFTP 的 UDP/69 放行可能绑定解释器路径；服务进程须使用已放行且实际可达的解释器。不默认修改防火墙、路由或网卡配置。网络不可用时记录限制，不把零字节传输视为吞吐结果。

固件功能、外部输入/输出与恢复范围见 [固件测试](firmware-tests.md)、[Ethernet](ethernet.md) 和 [板端操作](operations.md)。
