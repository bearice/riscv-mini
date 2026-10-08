# 新增可由 BIOS 装载的 GW-BASIC 解释器

生成：2026-10-08T11:21:56+00:00；模式：staged review。

## 改动

- 新增 `firmware/apps/gwbasic/`：可由常驻 BIOS 装载的 RPB1 二级程序（约 95 KiB，入口 `0x01000000`，声明 4 MiB 堆），只通过 BIOS 的 ecall 服务使用串口/LCD、时钟、SD 与帧缓冲，正常返回时 BIOS 打印 `PAYLOAD RETURNED`。
- 覆盖 GW-BASIC 常用语句与内建函数（PRINT/USING 子集、INPUT、IF/ELSE、FOR/NEXT、WHILE/WEND、GOSUB、ON ... GOTO/GOSUB、ON ERROR、DATA/READ、DIM/DEFtype、DEF FN、图形语句、`TESTS` 自检）；类型规则、溢出与数值显示按 GW-BASIC 归一，SAVE/OPEN 等因 BIOS 无文件写服务不可用或明确报错。
- `scripts/bios_payload.py` 增加 `--source-dir`（整目录编译）、`--helper string`（复用仓库既有的 freestanding memcpy/memset/strlen）与 `--soft-float` 回退；默认改用 rv32imaf/ilp32f 以利用板上单精度 FPU（无 F 的配置用 --soft-float）。
- 把 5x7 字模抽到 `firmware/common/font5x7.h` 共用，`firmware/common/rgb_canvas.h` 只保留绘制助手，图形模式文字与既有画布使用同一份字模。
- 新增 `scripts/gwbasic_image.py`（打包 BOOT.RPB）、`scripts/gwbasic_sim.py`（在 QEMU 上运行真实目标产物，含 mstatus.FS 使能与 ecall shim）、`scripts/gwbasic_verify.py`（板端 TFTP 装载与交互验收，不写 Flash、不改主机网络）、`tests/gwbasic_test.py`（4 项镜像断言 + 4 项 QEMU 回归）。
- 文档：新增 `docs/gwbasic.md`；`docs/bios.md` 的内存表改为与 `firmware/common/memory_layout.h` 一致的零基址并补 GW-BASIC 交叉引用；`firmware/README.md` 增加 `apps/` 分类与构建入口；`docs/system-design.md` 补文档索引。
- 验证：主机 8 项 GW-BASIC 回归与 18 项 workflow 检查通过；实板在 v0.8.0 full 配置上、由 Flash 启动的 BIOS（`romf0bea070`）经 `boot net BASIC.RPB` 装载本次 98984 B 产物（sha256 745dc18c…，重新构建后字节一致），`TESTS` 报 `ALL TESTS PASSED (77 checks)`、`PRINT 1+1` 显示 ` 2 `、`LIST`/`RUN` 输出 ` 1  2  3 BOARD OK`、`SYSTEM` 回到 BIOS。未验收项：物理断电冷启动、SD 路径 `boot sd`、真实 LCD/键盘与 SD `LOAD`。

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
