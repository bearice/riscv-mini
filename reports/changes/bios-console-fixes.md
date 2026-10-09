# 修复 BIOS 终端、行编辑和 USB 控制键

生成：2026-10-09T01:13:50+00:00；模式：staged review。

## 改动

- 修正 Left/Right、行中 Backspace 与 Ctrl-W，保留光标后的命令内容和行尾原始回显。
- ED 0/1 擦除完整前后行；备用屏保存并恢复主屏光标、属性和待换行；SGR 与报告保留延迟换行。
- 恢复鼠标滚轮与事件时间；USB Ctrl-A..Z 转换为控制字节，修饰键变化更新自动重复状态。
- 固件验收结束后通过 BIOS 原有 tty 恢复文字画面，不增加板端 CLI 命令。主机回归：VT 89、USB/鼠标 75、行编辑 14 与 HID 到编辑器 2、验收收尾 2 项通过；输出镜像通过。实板验收见 build/operations/20261009T010033873837Z-verify/firmware-verification.json，未写 Flash；物理 Ctrl-W 尚未收到用户确认。

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
