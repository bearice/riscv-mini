"""Compile the DDR Flash utility; only explicit update builds permit writes."""
from pathlib import Path
import os
import subprocess

from boot_image import FLASH_OFFSET, pack_image
from build_records import ROOT


def compile_probe(build, directory, tools, run=subprocess.run, writable=False):
    csr, csr_path = build.csr()
    regs = csr['csr_registers']
    names = {'UART_RXTX':'uart_rxtx', 'UART_TXFULL':'uart_txfull',
             'UART_RXEMPTY':'uart_rxempty', 'UART_EV_PENDING':'uart_ev_pending',
             'CTRL_RESET':'ctrl_reset'}
    names.update(FLASH_MOSI='flash_spi_mosi', FLASH_MISO='flash_spi_miso',
                     FLASH_CONTROL='flash_spi_control', FLASH_STATUS='flash_spi_status',
                     FLASH_CS='flash_spi_cs', FLASH_DIVIDER='flash_spi_clk_divider')
    if build.mode == 'xip':
        names.update(XIP_ENABLE='flash_xip_enable', XIP_BUSY='flash_xip_busy',
                     FLASH_CS='flash_spi_cs')
    definitions = {macro:regs[name]['addr'] for macro,name in names.items()}
    if any(regs[name]['size'] != 1 for name in names.values()):
        raise ValueError('Probe requires 32-bit CSR registers')
    definitions.update(PROBE_WRITE=int(writable and build.mode=='xip'),
                       PROBE_XIP=int(build.mode == 'xip'), PROBE_BASE=0,
                       PROBE_OFFSET0=FLASH_OFFSET, PROBE_LENGTH0=len(build.image),
                       PROBE_OFFSET1=0, PROBE_LENGTH1=0)
    if build.mode == 'xip':
        xip = build.validation['xip']
        definitions.update(PROBE_BASE=xip['base'], PROBE_OFFSET1=xip['flash_offset'],
                           PROBE_LENGTH1=build.validation['firmware_bytes'])
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'probe_config.h').write_text(''.join(
        f'#define {name} 0x{value:x}u\n' for name,value in definitions.items()),encoding='ascii')
    gcc = Path(tools['gcc'])
    objcopy = gcc.with_name(gcc.name.replace('gcc', 'objcopy'))
    if not gcc.is_file() or not objcopy.is_file():
        raise ValueError('Configured GCC/objcopy not found')
    elf = directory/'probe.elf'
    temporary=directory/'tmp';temporary.mkdir(exist_ok=True)
    environment=dict(os.environ,TEMP=str(temporary),TMP=str(temporary),TMPDIR=str(temporary))
    command = [str(gcc), '-march=rv32im_zicsr_zifencei', '-mabi=ilp32', '-Os',
               '-Wall', '-Wextra', '-Werror', '-ffreestanding', '-fno-builtin',
               '-nostdlib', '-nostartfiles', '-msmall-data-limit=0',
               '-DMINI_BOOTLOADER=1', '-DMINI_FEATURE_MMU='+str(int(build.validation.get('cpu_capabilities',{}).get('mmu',False))),
               '-DMINI_CPU_DCACHE='+str(int(build.validation.get('cpu_capabilities',{}).get('dcache',False))),
               '-I',str(directory),str(ROOT/'firmware/boot/start.S'),
               str(ROOT/'firmware/tools/flash_readback.c'),
               '-T',str(ROOT/'firmware/bootloader/app.ld'),'-o',str(elf)]
    with (directory/'compile.log').open('w',encoding='utf-8') as log:
        run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60,env=environment)
        run([str(objcopy),'-O','binary',str(elf),str(directory/'probe.bin')],
            stdout=log,stderr=subprocess.STDOUT,check=True,timeout=30,env=environment)
    image = pack_image((directory/'probe.bin').read_bytes(),build.validation['boot_image']['abi_tag'])
    (directory/'probe.img').write_bytes(image)
    return image, str(csr_path)
