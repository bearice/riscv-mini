# BIOS 提供文件服务与原始按键流，SD 读路径加块缓存

生成：2026-10-11T07:46:58+00:00；模式：staged review。

## 改动

- 新增文件系统服务 22-30（FS_OPEN/CLOSE/READ/WRITE/SEEK/STAT/MKDIR/UNLINK/RENAME，句柄 0..5，卷保持挂载）：二级程序需要按偏移反复读 SD 上的大文件，原来的 FILE_READ 每次都要重新给完整路径、无法保持打开状态。
- 新增服务 20 KEY（原始 USB HID usage、按下/松开、修饰键的独立环形队列）：TTY 的 GETC 只有字符，游戏需要松开事件；GETC 行为不变，既有载荷不受影响。
- 新增控制台命令 fetchraw FILE：TFTP 原样落卡、不做 RPB1 校验，用来把 IWAD 这类数据文件送上卡；fetch 仍只接受校验通过的镜像。
- 文件服务改为幂等挂载、不再每次操作都 f_mount：原来每次读都触发整卡重新初始化（每次约 12 ms 并刷一行 SD init PASS），按 4 KiB 读 4 MiB 文件被拖到分钟级；同时消除跨重初始化保留 FIL 造成的读数据损坏。
- SD 读路径加 4 KiB x 32 全相联 LRU 块缓存（写直通后刷新缓存、控制器复位后失效、SPI 回退共用）：一次 FS_READ 的 4096 字节上限正好一个缓存块，未命中只多付一次本来就要发的命令。bench sd 进入时临时关闭缓存，保证 io.sd-read 回答的是卡的速度。
- 镜像上限 BIOS_IMAGE_MAX 从 4 MiB 提到 8 MiB（scripts/bios_image.py 的 MAX_FILE 同步）：暂存区有 14 MiB 空间，镜像与 fetchraw 各 8 MiB 且不会同时占用。
- 新增 test sd cache 并纳入 board verify --suite firmware：按累计计数器的增量断言单扇区读=1 次未命中+预读、紧邻扇区命中且不再增加未命中，并与关闭缓存的直读逐字节比对。
- 验收：实板 mini.py board verify --build current --suite firmware --soak-seconds 60 加载 build/runs/20261011T072428892938Z-30a9356-dirty-full-rv32ima-rom0k-l24k-doom-bios-cache2/firmware/app.img（120 356 B，ABI 71da004d）全项 PASS，含新增 test sd cache；收据 build/operations/20261011T072524305091Z-verify。本机 RTL 未变，按提交 SOP 跳过 PnR、时序与性能基准。

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
