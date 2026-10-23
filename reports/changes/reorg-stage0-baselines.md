# docs: record reorg stage-0 baselines and adopt the execution plan

生成：2026-10-21T09:33:05+00:00；模式：staged review。

## 改动

- Stage 0 of the multi-platform reorg (plan revision 3, adopted unchanged as docs/history/repo-reorg-execution-plan.md): record two-machine plan identities (AE350 plan system stays BLOCKED P1 as-is), the host-test inventory 42 pass / 16 fail / 6 skip with every failure classified (14 driver-style needing --iverilog/artifacts plus missing iverilog role; modular_reuse closure-import regression from mini.py:80 since e3c5931; stale fpu assumption in sim/test_features), the 20K timing picture (v0.11.0 last board-qualified reference at +0.031ns margin; HEAD full-profile PnR red with 754 CLKDIV-domain + 3 usb_ulpi setup endpoints, a pre-existing RTL-growth timing regression outside reorg scope, stage-A full gate held for user ruling; minimal full-flow green at 0/0 +4.214ns store 030dc2c45ab4), and the AE350 baseline adopted from archive 7339ab23d5cb (0/0/0.142, git diff 7339ab2..HEAD gateware+firmware empty) cross-validated by a current-tree rebuild f56527dc3d28 with identical timing. Untracked build/reorg-manifest.json (280 entries, 0 unmapped, 656 scanned files) is the stage A-D checklist.

## 验证

对应源码的检查：20 项，PASS；命令和输入身份见同名 JSON。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
