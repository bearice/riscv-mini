# scripts: extract shared boot, framing and compile symbols (stage P)

生成：2026-10-21T11:38:20+00:00；模式：staged review。

## 改动

- Stage P of the multi-platform reorg: pull every symbol that the AE350 side imports out of the 20K root scripts before any per-machine directory split. BootSession and programmer_succeeded move to scripts/tools/boot_session.py; the STATE= protocol module becomes scripts/tools/uart_state.py; programmer_cable.py becomes scripts/tools/programmer_cable.py; the machine-neutral image framing (MAGIC/VERSION/HEADER/LOAD/FLASH_OFFSET/FLASH_SIZE/MAX_PAYLOAD/CHUNK/packet/pack_image/unpack_image) moves to scripts/tools/boot_image.py while the 20K-specific abi_tag stays in scripts/boot_image.py; the board Operation receipt moves to scripts/mini_ops/common.py; BASE_FLAGS/ROLES/source_files/quote_command/ninja_path/ninja_build move to scripts/modular/compile.py (generated_headers deliberately stays in the 20K firmware backend: it calls generate_csr and gateware.features/memory_map, and the dead system.py import is dropped). All 40+ import sites on both machines were updated in this commit, the hardware.py and firmware.py source closures now name scripts/modular/compile.py and scripts/tools/boot_image.py, and machine_build_test.py copies the new closure member. No directory moves. Verification on this tree: host sweep 42 pass / 16 fail / 6 skip, identical to the stage-0 baseline set; 20K plan system and AE350 plan system/gateware behave as recorded in stage 0 (AE350 system still BLOCKED verbatim); mini.py build bios --hardware current rebuilt hal-sdk and bios through the extracted compile helpers. Build keys change because the closure gained compile.py and tools/boot_image.py, which the reorg decision accepts.

## 验证

对应源码的检查：20 项，PASS；命令和输入身份见同名 JSON。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
