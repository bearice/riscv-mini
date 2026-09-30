# 基础系统验证记录

日期：2026-10-01。最终版本按用户要求移除 Flash 写后读回、配置 CRC 比对、Gowin Verify；启动装载时仍检查镜像格式和 CRC。旧阶段/提频分支及产品 monitor 中的测试命令已移除。

## 构建

| 项目 | 当前结果 |
| --- | --- |
| CPU / sys / DDR | VexRiscv lite，60 / 60 / 120 MHz，DDR DLL-off CL6/CWL6 |
| boot ROM / SRAM | 8 KiB / 8 KiB |
| boot.bin | 6,568 字节；`-Os -flto`；不包含 SD/FatFs/LCD 驱动 |
| DDR app.bin / app.img | 19,420 / 19,468 字节；含 Flash、SD/FatFs、两块 LCD 驱动 |
| 应用地址 / ABI | `0x40800000` / `0x008772f2` |
| Logic / Register | 7383/20736 / 3885/16173 |
| BSRAM / rPLL | 16/46 / 2/4；旧 M4 为 46/46 BSRAM |
| PnR setup / hold 违例 | 0 / 0 |

boot.bin SHA256：`54896fba88edf7dd5a598e9bbd53a4cab5223274f5681a72c508b368fbbb7f08`。

bitstream SHA256：`368ec442b349255a4ced4ea1dc6f784abced224d5001cb9fc7d11a1adcce250f`。

环境 doctor、Python 语法、SPI 事务、共享 native DDR 调度、LCD 扫描和 underrun 恢复仿真通过。镜像协议 4 个测试、Gowin 错误日志识别 2 个测试通过。最后的简化只涉及 Flash 写入及对应脚本，不改变 SoC 数据路径。

## 启动与写入

- 实测 Flash JEDEC `0x0b4017`，容量 8 MiB；CPU 擦写范围固定为 `[2,4)` MiB，配置区由地址边界保护。
- 最终 FPGA 配置使用 Gowin 普通 exFlash Erase/Program（operation 8），16.77 秒完成；未执行 Verify。Reprogram 从 Flash 重新配置 FPGA，DDR 初始化和训练成功。
- UART 接收匹配镜像，`FLASH INSTALLED` 返回；只擦除/编程，镜像头最后写入，无 Flash 写后读回。
- 最终从 Flash 装入 DDR 并执行的启动结果见 `simple-flash-boot-console.log`；SD / SPI LCD / RGB LCD 就绪标志均为 1，视频欠载为零。
- 简化前已经通过 UART DDR 执行、错误镜像拒绝、超时恢复、SD 根目录读取和自动 Flash 软件复位；这些接收/启动路径保持不变。写后读回和配置 CRC 诊断现已删除，不作为最终写入步骤。

断电冷启动按用户要求跳过。本轮不追加持续压力测试，也没有新的人工画面确认。RGB LCD 初始为黑色画布，SPI LCD 显示基本状态；旧色条、灰阶和角标已移除。

原始证据在 `build/base/validation.json`、`configuration-programmer.log`、`configuration-reload.log`、`configuration-install-console.log`、`simple-flash-boot-console.log`。`boot-verification.json` 的旧 bitstream hash 标识简化前的完整接收/启动验收，不能当作最终写入流程的读回校验声明。生成目录不提交。
