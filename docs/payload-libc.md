# BIOS app 的静态 Newlib 运行库

app 链接工具链的 Newlib、libm、libgcc，并链接 `firmware/libc/runtime.c`。BIOS 固件仍按原来的 freestanding 流程构建；不增加 BIOS 服务号、不导出驻留 libc、也不绑定 BIOS 内部符号地址。

## 构建与入口

```powershell
.venv/Scripts/python.exe scripts/mini.py build payload --libc newlib --soft-float `
  --source firmware/examples/primer20k_vexriscv/libc_test.c --output-dir build/libc-test-app
```

`--soft-float` 选择 RV32IM/ILP32，适用于当前无 FPU 的已验收 gateware。`--libc` 默认仍是 `none`，旧 freestanding app 可以继续使用 `--helper string`；Newlib app 不使用该 helper，也不提供替代标准头文件。

app 的入口采用以下约定：

```c
#include "runtime.h"
static int app_main(const struct bios_info *info) {
    /* 标准 C 头文件、malloc、stdio 等由 Newlib 提供。 */
    return 0;
}
int payload_main(const struct bios_info *info) {
    return bios_libc_run(info, app_main);
}
```

runtime 先建立 Newlib 的 `setjmp` 返回点，再初始化标准输出、运行构造函数、登记析构函数，最后调用 app。app 返回或调用 `exit(status)` 时，Newlib 执行 `atexit`、析构和 stdio 清理；runtime 的 `_exit` 关闭 app 的剩余原始文件描述符，并用 Newlib `longjmp` 返回入口。每次载荷装入后只调用一次 `bios_libc_run`；下次装入由 BIOS 重置载荷 BSS。退出后的 stdio 已关闭，入口若需再打印返回信息，应使用 `BIOS_WRITE`，而不是 `printf`。

默认堆为 app BSS 内的 8 MiB；构建时用 `--define 'BIOS_LIBC_HEAP_BYTES=(48u*1024u*1024u)'` 等覆盖。Doom 使用 48 MiB。`_sbrk` 检查边界，Newlib 负责分配器。不是驻留 BIOS 的堆。

生成的 `libc-inputs.json` 保存 ISA、ABI、实际选择的 libc/libm/libgcc multilib 路径及 SHA256，还有 runtime 源码哈希；缺少匹配库时构建失败。库成员按需抽取，并启用链接器段回收。库源码与许可证随工具链维护，参考 [Newlib 文档](https://sourceware.org/newlib/libc.html)。

## 文件、控制台与限制

- fd 0/1/2 对应 BIOS GETC/WRITE。标准输入只在 app 显式读取时取字节；runtime 的常规 poll 不取输入。Doom 仍只把 USB HID 作为游戏输入。
- 其余最多六个 fd 对应现有 BIOS FS 句柄。读写按最多 4096 B 拆分，文件操作使用现有 FS_OPEN/READ/WRITE/SEEK/CLOSE/STAT 等服务。stdio 的缓冲、EOF、格式化和数值转换使用 Newlib。
- 支持只读、只写、创建、截断、排他创建和追加。现有 BIOS 将 READ|WRITE 打开成 write-only，所以 `O_RDWR` 及 `r+/w+/a+` 返回 `ENOTSUP`；本次不修改 BIOS。
- FatFs 的 rename 不替换已有目标；适配层返回 `EEXIST`，不先删除目标。ABI v1 将大量 FatFs 错误折叠为 -1，runtime 能报告自己检测到的具体错误，底层无法区分的错误使用 `EIO` 等保守映射。
- 路径遵循现有 SD 根目录约定，剥离开头的 `./` 和 `/`，把反斜杠转为 `/`；最长 63 字节。不是完整 POSIX 路径/权限实现。
- `gettimeofday` 提供 BIOS 的毫秒 uptime，不能当作真实 UTC 时间。进程、硬链接等不支持。
- 这是单个前台 app 的运行库，不提供线程、多进程或保护边界。

可选弱钩子用于 poll、底层读盘观察和控制台占用判断。Doom 的 `shim.c` 只实现读 CRC、进度和调试钩子，不再实现任何标准库算法。CRC 覆盖 `_read` 返回的字节，包含 Newlib 的预读；不再等同于引擎每次 fread 请求的字节流，不能与旧 shim 的累计值混比。相同库、相同工作负载下的板子/QEMU 仍可对比。

## 验证

```powershell
.venv/Scripts/python.exe tests/primer20k_vexriscv/bios_libc_test.py
.venv/Scripts/python.exe tests/primer20k_vexriscv/doom_test.py
.venv/Scripts/python.exe scripts/primer20k_vexriscv/doom_debug.py --boot `
  --image build/libc-test-app/BOOT.RPB --tftp-name LIBCTEST.RPB `
  --host-ip 169.254.25.153 --boot-marker 'LIBC TEST PASS' --command boot-check
```

`libc_test.c` 是可重复运行的 app 合约测试：标准数值/字符串函数、堆边界、9000 B 文件读写、EOF、seek、追加、排他创建、rename/remove、构造/析构、atexit、退出状态与原始句柄回收。它仅排他创建 `LIBCTEST.TMP`，测试前拒绝同名文件及 `LIBCTEST.MOV`，完成后删除测试产物；不覆盖已有用户文件。句柄回收按进入前/退出后的可用数量比较，不关闭原先已占用的 BIOS 句柄。

2026-10-12 未修改的 filecmds2 BIOS 上两次测试均 `LIBC TEST PASS`、`handles before=4 after=4`，并返回 BIOS 提示符；日志 `build/libc-board-retry.log`、`build/libc-board-repeat.log`。原始检查假设六个句柄全空，因此第一次报告 leaked handles；该失败日志保留在 `build/libc-board.log`，不作为 runtime 泄漏证据。完整 BIOS 固件套件不是本次 app 验收范围。

Doom 最终静态载荷 541876 B，SHA256 `388de4b7ef55d2de21407bb15c2f12a38caab4d65e4052cb14a134a78531d598`。11 项 Doom 回归、libc 合约回归、配置/镜像/workflow 检查通过。实板 `build/doom-debug/newlib-board/` 显示正常场景和菜单；最终产物 `build/doom-debug/newlib-final/` 的 frame/source 比对为 0/128000 字节差异。载荷退出后返回 BIOS，状态仍为原 filecmds2 BIOS，再次 TFTP 启动成功。完整证据索引是 `build/doom-newlib-verification.json`。只传输并运行 DDR 载荷，未写 Flash、未替换 SD 上的旧 DOOM.RPB。
