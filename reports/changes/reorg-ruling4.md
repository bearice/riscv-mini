# docs: rule full-profile PnR out of the per-stage reorg gates

生成：2026-10-21T11:27:45+00:00；模式：staged review。

## 改动

- User ruling (plan revision 4): 20K full-profile PnR leaves stage 0/A-D verification gates because no green full baseline exists at HEAD (pre-existing RTL-growth timing regression). Stage gates become the minimal full-flow green anchor (0/0, +4.214ns) plus AE350 gateware rebuild against its 0/0/0.142 green baseline, which stays mandatory; B-step machine PnR requirements downgrade to full-profile RTL generation without PnR plus sim regressions; stage E runs one full two-machine rebuild and registers the result as-is. The timing regression itself is a separate defect project outside the reorg.

## 验证

对应源码的检查：20 项，PASS；命令和输入身份见同名 JSON。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
