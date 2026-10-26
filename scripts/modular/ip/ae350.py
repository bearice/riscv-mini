"""Generate the AE350 SDK's macro-configured subsystem and TCM inputs.

This SDK plugin has no Tcl IPFlow implementation. Its encrypted subsystem and
public wrapper are parameterized by riscv_ae350_config.v and TCM includes. Keep
those SDK templates intact and generate their configuration from the machine.
"""
from pathlib import Path
import re
from scripts.modular.storage import text_file

MACROS = {
    'ENABLE_CUSTOMIZED_DATA': 'AE350_EXT_D_MEM_EN',
    'ENABLE_CUSTOMIZED_INSTRUCTION': 'AE350_EXT_I_MEM_EN',
    'ENABLE_DTCM': 'AE350_EMB_DTCM_EN', 'ENABLE_ITCM': 'AE350_EMB_ITCM_EN',
    'ENABLE_DDR3': 'AE350_EMB_DDR3_EN', 'ENABLE_SPI_FLASH': 'AE350_EMB_SPIFLASH_EN',
    'FlashRegisterRWMode': 'AE350_APB_FLASH_EN',
    'ENABLE_EXTENDED_APB_SLAVE': 'AE350_EXT_APB_SLAVE_EN',
    'ENABLE_EXTENDED_AHB_SLAVE': 'AE350_EXT_AHB_SLAVE_EN',
    'ENABLE_EXTENDED_AHB_MASTER': 'AE350_EXT_AHB_MASTER_EN',
    'ENABLE_DMAC': 'AE350_EXT_DMA_EN', 'ENABLE_PLIC': 'AE350_EXT_INT_EN',
    'ENABLE_GPIO_IO_PORTS': 'AE350_GPIO_IOPORT_EN',
    'ENABLE_SPI2_IO_PORTS': 'AE350_SPI_IOPORT_EN',
    'ENABLE_I2C_IO_PORTS': 'AE350_I2C_IOPORT_EN',
    'LOCAL_MEMORY_ACCESS_MODE': 'AE350_DDR3_PL_RW_EN',
    **{'ENABLE_' + name: 'AE350_APB_' + name + '_EN'
       for name in ('SMU', 'RTC', 'WDT', 'GPIO', 'UART1', 'UART2', 'SPI', 'PIT', 'I2C')},
    **{'PWM_CHANNEL_' + str(i): 'AE350_PWM_CH_' + str(i) for i in range(4)},
}


def generate(definition, destination, ide):
    parameters = definition['parameters']
    expected = set(MACROS) | {'LANG', 'ItcmSize', 'ItcmInstructionMemory'}
    if set(parameters) != expected:
        raise ValueError('Unmapped AE350 SDK configuration keys')
    if parameters['LANG'] != 0:
        raise ValueError('AE350 adapter requires Verilog')
    if parameters['ENABLE_ITCM'] and (parameters['ItcmSize'] != 0 or not parameters['ItcmInstructionMemory']):
        raise ValueError('Unsupported AE350 instruction TCM configuration')
    source = Path(ide) / 'ipcore/RiscV_AE350_SOC/data'
    destination = Path(destination)
    for name in ('riscv_ae350_top.v', 'riscv_ae350_soc.v', 'gw_itcm.v', 'gw_dtcm.v'):
        data = (source / name).read_bytes()
        if name == 'riscv_ae350_top.v':
            data, count = re.subn(rb'\bmodule\s+RiscV_AE350_SOC_Top\b',
                                 b'module ' + definition['module'].encode(), data)
            if count != 1:
                raise ValueError('Unsupported AE350 SDK wrapper template')
        (destination / name).write_bytes(data)
    if parameters['ENABLE_SPI_FLASH']:
        (destination / 'riscv_ae350_flash.v').write_bytes((source / 'riscv_ae350_flash.v').read_bytes())
    text_file(destination / 'riscv_ae350_config.v',
              ''.join('`define ' + macro + '\n' for key, macro in MACROS.items() if parameters[key]))
    text_file(destination / 'gw_itcm_config.v', '`define ITCM_SIZE 13\n`define ITCM_INIT_PATH "itcm_initial"\n')
    text_file(destination / 'gw_dtcm_config.v', '`define DTCM_SIZE 13\n')
    for lane in range(4):
        text_file(destination / 'itcm_initial' / ('itcm%d' % lane), '00\n' * 8192)
    return destination / 'riscv_ae350_top.v'
