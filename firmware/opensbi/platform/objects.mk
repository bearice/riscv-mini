platform-objs-y += platform.o
PLATFORM_RISCV_XLEN = 32
PLATFORM_RISCV_ABI = ilp32
PLATFORM_RISCV_ISA = rv32ima_zicsr_zifencei
FW_DYNAMIC = y
FW_JUMP = n
FW_PAYLOAD = y
FW_TEXT_START = 0x41000000
FW_PAYLOAD_OFFSET = 0x100000
FW_PAYLOAD_ALIGN = 0x1000
platform-cflags-y += -Wno-error=sometimes-uninitialized
FW_PAYLOAD_FDT_OFFSET = 0x80000

# VexRiscv has no Smdbltrp or mstatush; CLEAR_MDT must not access CSR 0x310.
platform-asflags-y += -DFW_MDT_UNSUPPORTED
