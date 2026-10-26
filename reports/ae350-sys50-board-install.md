# AE350 sys50 配置 Flash 安装验收

2026-10-26。用户明确要求先刷bootloader，确认正常后再持久写入bitstream并提交。所选gateware为`eaebc3f795263b06f3fb2395c5a7fcc6dea183e5226279c21f75b2c287c25d59`，已通过完整STA；bootloader artifact为`d96c9542d1ad`，UART构建标识为`b80f3f898102`。

U4实测JEDEC为`0xef4018`，工具识别Winbond W25Q128。bootrom槽位于物理`0x600000`，8KiB内容完成逐字节读回；安装保存完整64KiB擦除块，并比较两侧各64KiB保护块。首次写入后因libusb自动回绑FTDI驱动而未完成D2XX→SRAM交接；此后统一使用序列号选择的libusb后端完成SRAM验证，成功回执为`20261026T064853526825Z-board-ae350-bootrom`。失败尝试单独保留，未当作通过。

`20261026T065736047850Z-board-ae350-jtag-test`通过CPU TAP、bootrom全量及非顺序读回、硬ILM/DLM配置、自主POST和DDR初始化检查；实际SYSINFO硬件ABI与50MHz时钟匹配所选gateware。CPU调试结束后恢复运行。

配置位流FS解码后为4701386字节，与本次Gowin生成的bin逐字节一致。持久更新run为`20261026T070028342946Z-board-ae350-update`：先备份配置，再将新内容连同保留的尾部按64KiB边界写至`[0,0x480000)`，工具verify通过后独立读回至`0x490000`；配置、末块尾部和下一保护块均与预期一致。启动邻域`[0x5f0000,0x620000)`在配置写入前后完全相同。

最终不加载SRAM位流，执行JTAG RELOAD从配置Flash重新启动，UART报告：

```text
riscv-mini AE350 ROM b80f3f898102
POST 600d0000
DDR INIT OK
BL>
```

主机已保存原配置和启动邻域备份、工具输出、完整读回和UART日志。未解除保护、未整片擦除、未写用户Flash U7或SD卡；FTDI接口驱动已恢复。未执行物理断电冷启动，也未据此宣称该镜像的全容量DDR、UART应用装载或所有外设套件通过；current引用未改变。

安装流程继续使用`mini.py board bootrom/update`统一入口，位流更新可复用相同bootloader的成功安装回执。FS转换/分区边界/擦除块尾部保护/bootloader门禁和错误FPGA身份阻止写入的测试通过，见[主机回归](ae350-flash-host-regression.json)。配置工具的RELOAD行为依据[openFPGALoader v1.0.0 Gowin后端](https://github.com/trabucayre/openFPGALoader/blob/v1.0.0/src/gowin.cpp)，实际Flash启动由本次UART回执确认。

详细身份和哈希见[机器可读安装结果](ae350-sys50-board-install.json)。
