# Add Nix Linux development environment

生成：2026-10-15T05:57:15+00:00；模式：staged review。

## 改动

- Add a locked stable Nix development shell with nixpkgs uv, uv-managed Python, the Gowin IDE wrapper, and xPack RISC-V GCC.
- Make tool discovery and RISC-V utility execution work on Linux while preserving Windows support, and document Linux build and Ethernet workflows.
- Provide disabled filesystem ecall implementations so minimal BIOS builds without filesystem support link cleanly.

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
