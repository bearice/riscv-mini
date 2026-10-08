# BIOS 控制台支持 VT100/ANSI 终端与键盘自动重复

生成：2026-10-08T16:23:12+00:00；模式：staged review。

## 改动

- BIOS TTY 重写为完整的 VT100/ANSI 终端：新增 firmware/bios/vt.c 与 vt.h 实现不依赖 HAL 的终端状态机（80x34 网格、每单元字符+16 位属性、ANSI 16 色调色板），支持光标定位、ED/EL/ECH、IL/DL/ICH/DCH、DECSTBM 滚动区域、SGR 全属性、备用屏 ?1049、ESC 7/8 与 CSI s/u、DSR/DA 报告、RIS、制表位与 DECAWM 延迟换行；console.c 改为 VT 前端并按帧整屏重绘（双缓冲帧槽各自独立，增量渲染会露出陈旧像素），同时补回被重写丢失的 USB HID 报告排空循环与 bios_mouse_take；main.c 增加命令行编辑（方向键/Home/End/Insert/Delete/Ctrl-AEKUW，行尾追加与退格保持逐字节回显以免打破脚本的明文提示符匹配）与 md/mdb 内存读诊断命令（限 RAM 窗口，尾随 u 只走 UART 不动文字画面）；USB 键盘按 HID usage 正确解码并实现 typematic 自动重复（500 ms 首延迟、60 ms 周期，CapsLock/ESC 不重复）；新增示例载荷 tty_color_demo.c 与主机回归 tests/vt_console_test.c、tests/usb_key_map_test.c。

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
