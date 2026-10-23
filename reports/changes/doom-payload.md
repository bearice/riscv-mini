# feat(doom): run DOOM as a BIOS payload with zero-copy video and USB input

生成：2026-10-11T07:54:21+00:00；模式：staged review。

## 改动

- 新增 firmware/apps/doom/：doomgeneric 的 BIOS 平台层与最小 libc，DOOM 作为普通 RPB1 二级程序跑在常驻 BIOS 下；视频零拷贝（I_FinishUpdate 每行正好推进 960 B = 面板 stride，DG_ScreenBuffer 直接指向非显示帧槽，画完 VIDEO_PRESENT 翻页），输入走 BIOS_KEY 原始 HID usage，F12 返回 BIOS。
- shim 的 fread/fwrite 按 sz*n 合并成不超过 4096 字节的 FS_READ/FS_WRITE，并把 BIOS_POLL 限流到 8 ms 一次：原先逐字节发 ecall，261 KiB 要 268 127 次调用、R_Init 在板上跑不完；合并后同样 1 282 KiB 只用 1 007 次调用 / 1 642 ms。
- 新增 scripts/doom_image.py（构建 BOOT.RPB，固定 480x272、强制 --soft-float，板上 FPU=0）、scripts/doom_sim.py（QEMU 跑真实产物 + 面板抓帧 + 脚本化按键）、scripts/doom_verify.py（实板验收）、tests/doom_test.py（RPB1 布局 + QEMU 回归）。
- bios_payload.py 增加 --define/--cflag；boot_upload.py 的 until() 增加 on_chunk 回调——长等待必须逐块回显 UART，否则 15 分钟的等待看起来像死机。
- 新增 .gitattributes：只对 firmware/apps/doom/** 关闭空白检查（上游代码自带大量行尾空格，--check 会刷屏），不引入 text/eol，不改任何文件内容。
- 文档：docs/platforms/primer20k_vexriscv/doom.md（构建/上卡/运行/验证/边界，含实测 2.48 帧/秒）、docs/platforms/primer20k_vexriscv/bios-payload-porting.md（二级程序作者的 13 个坑）、docs/platforms/primer20k_vexriscv/system-design.md 指向二者。
- 验收：scripts/doom_verify.py 全项 PASS（build/doom-verify/doom-verification.json，presents=200，无 FAULT）；主机回归 tests/doom_test.py 7 项 OK、tests/workflow_test.py 18 项 OK；帧率 2.48 帧/秒（600 帧 / 241.9 s，被动观察 build/operations/*-status/uart.log）。
- 边界：鼠标不支持（i_input.c 不投递 ev_mouse）、无声音、WAD 是 1.8 shareware（吸引模式提示版本不符，非致命）、存档路径未在板上验证；本提交不动 BIOS/HAL/RTL，不递增组件版本。

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
