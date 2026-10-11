# feat(bios): SD file commands and the block-cache sizing measurement

生成：2026-10-11T14:41:22+00:00；模式：staged review。

## 改动

- Console gains ls [DIR], rm FILE, cat FILE [MAX], size FILE, mkdir DIR, mv FROM TO over the existing FatFs layer; fetch/fetchraw deliberately keep creating without overwriting, and rm is the explicit delete primitive they need.
- ABI unchanged (71da004d): the commands are console-side, no new ecall service numbers.
- blockcache.h records why the cache stays at 32 x 4 KiB: 512 blocks (2 MiB) left the same 1007 FS_READ calls for the same 1282 KiB of DOOM WAD traffic, cost ~250 ms more in the read path, and did not change rgb_lcd underflows (148 vs 137).

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
