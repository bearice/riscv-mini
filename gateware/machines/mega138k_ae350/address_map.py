"""AE350 register definitions and pure, explicitly instantiated machine layout."""
from gateware.io import IOBindings
from dataclasses import asdict
from gateware.machines.mega138k_ae350.video_modes import MODES, pll_config

ROM_BASE = 2147483648

BOOT_CONTRACT_ADDR = 0xf800000c
BOOT_CLOCK_ADDR = 0xf8000010
BOOT_IMAGE_ABI_ADDR = 0xf8000014
BOOT_CONTRACT_VERSION = 1
# SYSINFO device table: record count, record stride, first record.
SYSINFO_DEVICE_COUNT_ADDR = 0xf8000020
SYSINFO_DEVICE_STRIDE_ADDR = 0xf8000024
SYSINFO_DEVICES_ADDR = 0xf8000040
SYSINFO_DEVICE_BYTES = 32
# U7 holds one loader image (48-byte header + payload) from this offset.
USER_FLASH_BOOT_OFFSET = 0
USER_FLASH_BOOT_BYTES = 0x80000
# a0 at payload entry: how the ROM obtained the image.
BOOT_SOURCE_UART = 0
BOOT_SOURCE_USER_FLASH = 1

FLASH_CTRL_BASE = 0xf8090000
FLASH_CTRL_SIZE = 0x80

ILM_BASE = 2684354560

ILM_SIZE = 64 << 10

ILM_CODE_BYTES = ILM_SIZE

DLM_BASE = 2686451712

DLM_SIZE = 64 << 10

DLM_DATA_BYTES = 32768

RESULTS_BASE = DLM_BASE + 32768

RESULTS_BYTES = 1536

LOCAL_PARAMS = DLM_BASE + 34304

LOCAL_PARAMS_BYTES = 256

PAYLOAD_STACK_BOTTOM = DLM_BASE + 40960

PAYLOAD_STACK_TOP = DLM_BASE + 57344

ROM_STACK_BOTTOM = PAYLOAD_STACK_TOP

ROM_STACK_TOP = DLM_BASE + DLM_SIZE

CSR_BASE = 4160749568

CSR_SIZE = 65536

SYSINFO_BASE = CSR_BASE

IRQ_BASE = CSR_BASE + 8192

DDR_CSR_BASE = CSR_BASE + 12288

DMA_SVC_BASE = CSR_BASE + 16384

SENSE_BASE = CSR_BASE + 28672

WS2812_BASE = 4161208320

IO_BASE = 4161273856

AUDIO_BASE = 4160815104

MIC_BASE = 4160880640

SPI_BASE = 0xf8050000
I2C_BASE = 0xf0a00000
SPI_ID = 0xae350b01
I2C_ID = 0xae350a01
TWI_MUX_GPIO_SHIFT = 20
TWI_MUX_GPIO_MASK = 7 << TWI_MUX_GPIO_SHIFT

CSR_PAGE_BYTES = 4096

ETH_BASE = 0xf80a0000
SFP_BASE = 0xf80b0000
EXT_INT_ETHERNET = 9

USB_BASE = 0xf8060000

SD_BASE = 0xf8040000

SD_ID = 0xae350801

SD_CAP = 7  # native 1/4-bit, hard DMAC FIFO, card detect

SD_TX_DATA = 0xe8020000
SD_RX_DATA = 0xe8020004
SD_TX_REQ = 4
SD_RX_REQ = 5
SD_TX_DMA_SEL = 11
SD_RX_DMA_SEL = 12
SD_DMA_CHANNEL = 4  # ATCDMAC300 channel the SD drivers own

VIDEO_BASE = 4160946176

VIDEO_ID = 2922710273

VIDEO_CAP = 15 | 0x1f0  # RGB888, XRGB8888, runtime modes, HPD, HPD IRQ

VIDEO_REGS = dict(CONTROL=12, STATUS=16, IRQ_STATUS=20, IRQ_ENABLE=24, LAST_ERROR=28, BASE0=32, BASE1=36, STRIDE=40, COMMIT=44, FRAMES=48, UNDERFLOWS=52, ADDRESS_ERROR=56, TIMEOUT_ERROR=60, ACTIVE_BASE=64, PIXEL_HZ=68, GEOMETRY=72, EPOCH=76, FIFO_MIN=80, MAX_WAIT=84, READS=88, CPU_COMMANDS=92, MISSING=96, SEQUENCE=100, ACTIVE_SEQUENCE=104, CANCELLED=108, ERRORS=112, CLEAR_ERRORS=116, REQUEST_OVERFLOW=120, MODE=124, FORMAT=128, MODE_CONFIG=132, MODE_STATUS=136, MODE_COUNT=140, ENABLE=144, HPD=148, MODE_INFO_BASE=256)

AUDIO_DATA = 3892314112

MIC_DATA = 3892379648

AUDIO_REQ = 2

MIC_REQ = 3

AUDIO_DMA_SEL = 9

MIC_DMA_SEL = 10

EXT_INT_AUDIO = 2

EXT_INT_MIC = 3

EXT_INT_VIDEO = 8

AUDIO_ID = 2922710017

MIC_ID = 2922710018

AUDIO_CAP = 7

MIC_CAP = 7

GPIO_BASE = 4033871872

PLIC_BASE = 3825205248

PLIC_CLAIM = PLIC_BASE + 2097156

PLIC_ENABLE = PLIC_BASE + 8192

PLIC_PENDING = PLIC_BASE + 4096

PLIC_THRESHOLD = PLIC_BASE + 2097152

GPIO_ID_PRODUCT = 8241

PLIC_GPIO_SOURCE = 7

PLIC_FABRIC_IO_SOURCE = 0

EXT_INT_IO = 7

EXT_AHB_BASE = 3892314112

EXT_AHB_SIZE = 128 << 20

LOOPBACK_DATA = 3892641792

LOOPBACK_DEPTH = 16

DMAC_BASE = 4039114752

ABI_VERSION = 65536

SYSINFO_ID = 2922708993

IRQ_ID = 2922708994

DMA_SVC_ID = 2922708995

LOOPBACK_ID = 2922709249

DMAC_ID = 2922709505

CAP_LOOPBACK = 1

CAP_EXT_IRQ = 2

CAP_DMA_REQ = 4

CAP_FABRIC_READER = 8

CAP_DMAC_DECLARED = 16

CAP_BOARD_IO = 32

CAP_WS2812 = 64

CAP_SENSE = 128

CAP_AUDIO = 256

CAP_MIC = 512

CAP_VIDEO = 1024

CAP_SD = 2048
CAP_FLASH = 4096
CAP_I2C = 8192

FLAG_PRESENT = 1

FLAG_DATA = 2

FLAG_DMA = 4

FLAG_IRQ = 8

SYSINFO_CAP = CAP_LOOPBACK | CAP_EXT_IRQ | CAP_DMA_REQ | CAP_DMAC_DECLARED | CAP_BOARD_IO | CAP_WS2812 | CAP_SENSE | CAP_AUDIO | CAP_MIC | CAP_VIDEO | CAP_FABRIC_READER | CAP_SD | CAP_FLASH | CAP_I2C

WS2812_ID = 2922709761

IO_ID = 2922709762

WS2812_CAP = 3

IO_CAP = 7

SENSE_ID = 2922709763

SENSE_CAP = 3

TRAP_RECORD = 160

TRAP_RECORD_WORDS = 5

ERR_BUSY = 1

DMA_SVC_CAP = CAP_LOOPBACK | CAP_DMA_REQ

REQ_MAP = 256

LOOPBACK_EP = (LOOPBACK_ID, DMA_SVC_BASE, 4096, LOOPBACK_DATA, 4, ABI_VERSION, 0 | 0 << 8 | (FLAG_PRESENT | FLAG_DATA | FLAG_DMA | FLAG_IRQ) << 16, 0)

DMAC_EP = (DMAC_ID, DMAC_BASE, 0, 0, 0, 0, 255 | 255 << 8 | FLAG_PRESENT << 16, 0)

WS2812_EP = (WS2812_ID, WS2812_BASE, 4096, 0, 0, ABI_VERSION, 0 | 0 << 8 | (FLAG_PRESENT | FLAG_IRQ) << 16, 0)

IO_EP = (IO_ID, IO_BASE, 4096, 0, 0, ABI_VERSION, EXT_INT_IO | 0 << 8 | (FLAG_PRESENT | FLAG_IRQ) << 16, 0)

SENSE_EP = (SENSE_ID, SENSE_BASE, 4096, 0, 0, ABI_VERSION, 0 | 0 << 8 | FLAG_PRESENT << 16, 0)

AUDIO_EP = (AUDIO_ID, AUDIO_BASE, 4096, AUDIO_DATA, 4, ABI_VERSION, EXT_INT_AUDIO | AUDIO_REQ << 8 | (FLAG_PRESENT | FLAG_DATA | FLAG_DMA | FLAG_IRQ) << 16, AUDIO_DMA_SEL)

MIC_EP = (MIC_ID, MIC_BASE, 4096, MIC_DATA, 4, ABI_VERSION, EXT_INT_MIC | MIC_REQ << 8 | (FLAG_PRESENT | FLAG_DATA | FLAG_DMA | FLAG_IRQ) << 16, MIC_DMA_SEL)

VIDEO_EP = (VIDEO_ID, VIDEO_BASE, 4096, 0, 0, ABI_VERSION, EXT_INT_VIDEO | (FLAG_PRESENT | FLAG_IRQ) << 16, 0)

SD_EP = (SD_ID, SD_BASE, 4096, SD_TX_DATA, 8, ABI_VERSION, (FLAG_PRESENT | FLAG_DATA | FLAG_DMA) << 16, SD_TX_DMA_SEL | (SD_RX_DMA_SEL << 8))

SPI_EP = (SPI_ID, SPI_BASE, 0x28, 0, 0, 1, FLAG_PRESENT << 16, 0)
I2C_EP = (I2C_ID, I2C_BASE, 0x30, 0, 0, 0, FLAG_PRESENT << 16, 0)

DEVICE_COUNT = 11

STATUS_ADDR = DDR_CSR_BASE

CALIB_CYCLES_ADDR = DDR_CSR_BASE + 4

READ_COUNT_ADDR = DDR_CSR_BASE + 24

WRITE_COUNT_ADDR = DDR_CSR_BASE + 20

TRACE_ADDR = DDR_CSR_BASE + 16

WRITE_TRACE_ADDR = DDR_CSR_BASE + 28

READ_TRACE_ADDR = DDR_CSR_BASE + 64

RLINE_HIT_ADDR = DDR_CSR_BASE + 68

WAIT_CYCLES_ADDR = DDR_CSR_BASE + 72

XIP_BASE = ROM_BASE

XIP_SIZE = 256 << 20

DDR_CODE = 12288

DDR_CODE_MAX = 512000

SUBRANGES = (('ilm.code', ILM_BASE, ILM_CODE_BYTES), ('dlm.private', DLM_BASE, DLM_DATA_BYTES), ('dlm.results', RESULTS_BASE, RESULTS_BYTES), ('dlm.params', LOCAL_PARAMS, LOCAL_PARAMS_BYTES), ('dlm.payload-stack', PAYLOAD_STACK_BOTTOM, PAYLOAD_STACK_TOP - PAYLOAD_STACK_BOTTOM), ('dlm.rom-stack', ROM_STACK_BOTTOM, ROM_STACK_TOP - ROM_STACK_BOTTOM))

# Vendor-fixed AE350 windows used by the OS device tree.
PLIC_SIZE = 0x2000000
PLMT_BASE = 0xe6000000
PLMT_SIZE = 0x100000
PLICSW_BASE = 0xe6400000
PLICSW_SIZE = 0x400000
UART_BASE = 0xf0300000
UART_SIZE = 0x1000
UART_PLIC_SOURCE = 9
# EXT_INT[n] reaches PLIC source n + 10: board I/O EXT_INT[7] measured as 17.
PLIC_EXT_INT_SOURCE_BASE = 10

# DDR software layout (physical, DDR base 0). Phases say when a range is live:
# bios = BIOS resident, including loading OS components; app = BIOS-launched
# application; os = after `boot opensbi`; diag = a ROM-loaded diagnostic payload
# running instead of the BIOS (image and .bss in the BIOS ranges, payloads linked
# with the BIOS services also use its buffers, scratch in diag.*). Ranges live in a common phase must
# be disjoint. Device DMA into a range that is not live in the os phase must be
# stopped before the handoff. Diagnostics may also address any DDR word as memory
# under test (geometry, capacity, traversal), never as a reservation.
BIOS_NET_BASE = 0x00100000
BIOS_NET_BYTES = 0x00010000
BIOS_APP_BASE = 0x01000000
BIOS_APP_LIMIT = 0x07e00000
BIOS_STAGING_BASE = 0x02000000
BIOS_STAGING_BYTES = 0x01000000
BIOS_FB_BYTES = 0x00100000
# BIOS-private scanout double buffer; BIOS_FB0/1 are the application canvases.
BIOS_SCANOUT0 = 0x08000000
BIOS_SCANOUT1 = BIOS_SCANOUT0 + BIOS_FB_BYTES
BIOS_FB0 = 0x08200000
BIOS_FB1 = BIOS_FB0 + BIOS_FB_BYTES
# .bss of every ROM-loaded payload (the resident BIOS and diagnostics), linked
# through the generated ae350_map.ld; includes the BIOS libc heap.
BIOS_BSS_BASE = 0x08400000
BIOS_BSS_BYTES = 0x00800000
# Scratch buffers and DMA targets of diagnostic payloads, one range per suite so
# suites linked together never collide. Buffers that carry guard words in front
# start at least one page into their range.
DIAG_MEMORY_BASE = 0x00200000   # memory suite: patterns, perf, cache, exec stub
DIAG_MEMORY_BYTES = 0x00200000
DIAG_DMA_BASE = 0x00400000      # aligned beyond the suite's page/line-crossing offsets
DIAG_DMA_BYTES = 0x00400000
DIAG_IO_BASE = 0x00800000
DIAG_IO_BYTES = 0x00100000
DIAG_AUDIO_BASE = 0x00900000
DIAG_AUDIO_BYTES = 0x00100000
DIAG_VIDEO_BASE = 0x00a00000    # two RGB565 640x480 frames, then the CPU stress buffer
DIAG_VIDEO_BYTES = 0x00300000
DIAG_VIDEO_FB0 = DIAG_VIDEO_BASE + 0x00001000  # reported by the video stress result
DIAG_VIDEO_FB1 = DIAG_VIDEO_BASE + 0x00101000
DIAG_VIDEO_STRESS = DIAG_VIDEO_BASE + 0x00200000
DIAG_SD_BASE = 0x00d00000
DIAG_SD_BYTES = 0x00200000
DIAG_ETH_BASE = 0x00f00000      # descriptor rings and packet buffers
DIAG_ETH_BYTES = 0x00100000
OS_MEMORY_BYTES = 0x10000000
OS_KERNEL_BASE = 0x00400000
OS_KERNEL_BYTES = 0x00c00000
OS_SBI_BASE = 0x01000000
OS_SBI_BYTES = 0x00100000
# OpenSBI FW_PAYLOAD places U-Boot right after os.sbi; U-Boot's initial stack
# is the top of os.uboot until it relocates itself to the top of OS memory.
OS_UBOOT_BASE = OS_SBI_BASE + OS_SBI_BYTES
OS_UBOOT_BYTES = 0x00400000
OS_DTB_BASE = 0x03200000
OS_DTB_BYTES = 0x00010000
# Device tree OpenSBI hands to U-Boot; it stays intact until booti copies its
# reserved-memory nodes into the Linux tree loaded at os.dtb.
OS_FIRMWARE_FDT_BASE = OS_DTB_BASE + OS_DTB_BYTES
OS_FIRMWARE_FDT_BYTES = 0x00010000
OS_KERNEL_ALIGN = 0x00400000  # Sv32 Linux maps the kernel with 4 MiB pages
OS_PAGE_OFFSET = 0xc0000000   # must equal CONFIG_PAGE_OFFSET
OS_VMALLOC_BYTES = 0x20000000  # Linux Sv32: half of the 1 GiB kernel VA space
# The A25 ILM/DLM cannot be disabled or moved (milmb/mdlmb enable and base are
# read-only) and are matched on the virtual address before translation. Linux
# keeps this kernel VA range unmapped: CONFIG_RISCV_RESERVED_VA_START/SIZE.
OS_RESERVED_VA_BASE = ILM_BASE
OS_RESERVED_VA_BYTES = DLM_BASE + DLM_SIZE - ILM_BASE

DDR_PHASES = ('bios', 'app', 'os', 'diag')
DDR_LAYOUT = (
    ('bios.image', DDR_CODE, DDR_CODE_MAX, ('bios', 'app', 'diag')),
    ('bios.bss', BIOS_BSS_BASE, BIOS_BSS_BYTES, ('bios', 'app', 'diag')),
    ('bios.net', BIOS_NET_BASE, BIOS_NET_BYTES, ('bios', 'app', 'diag')),
    ('bios.app', BIOS_APP_BASE, BIOS_APP_LIMIT - BIOS_APP_BASE, ('app',)),
    ('bios.staging', BIOS_STAGING_BASE, BIOS_STAGING_BYTES, ('bios',)),
    ('bios.scanout0', BIOS_SCANOUT0, BIOS_FB_BYTES, ('bios', 'app', 'diag')),
    ('bios.scanout1', BIOS_SCANOUT1, BIOS_FB_BYTES, ('bios', 'app', 'diag')),
    ('bios.fb0', BIOS_FB0, BIOS_FB_BYTES, ('bios', 'app', 'diag')),
    ('bios.fb1', BIOS_FB1, BIOS_FB_BYTES, ('bios', 'app', 'diag')),
    ('diag.memory', DIAG_MEMORY_BASE, DIAG_MEMORY_BYTES, ('diag',)),
    ('diag.dma', DIAG_DMA_BASE, DIAG_DMA_BYTES, ('diag',)),
    ('diag.io', DIAG_IO_BASE, DIAG_IO_BYTES, ('diag',)),
    ('diag.audio', DIAG_AUDIO_BASE, DIAG_AUDIO_BYTES, ('diag',)),
    ('diag.video', DIAG_VIDEO_BASE, DIAG_VIDEO_BYTES, ('diag',)),
    ('diag.sd', DIAG_SD_BASE, DIAG_SD_BYTES, ('diag',)),
    ('diag.eth', DIAG_ETH_BASE, DIAG_ETH_BYTES, ('diag',)),
    ('os.kernel', OS_KERNEL_BASE, OS_KERNEL_BYTES, ('bios', 'os')),
    ('os.sbi', OS_SBI_BASE, OS_SBI_BYTES, ('bios', 'os')),
    ('os.uboot', OS_UBOOT_BASE, OS_UBOOT_BYTES, ('bios', 'os')),
    ('os.dtb', OS_DTB_BASE, OS_DTB_BYTES, ('bios', 'os')),
    ('os.firmware-fdt', OS_FIRMWARE_FDT_BASE, OS_FIRMWARE_FDT_BYTES, ('os',)),
)

class AddressMap:
    def __init__(self, machine):
        self.machine = machine
        self.io = IOBindings(machine)
        self.DDR_BASE = machine.memory.base
        self.DDR_SIZE = machine.memory.size_bytes
        self.FABRIC_HZ = machine.clocks.sys
        self.CPU_HZ = machine.clocks.cpu
        self.DATA_HZ = machine.clocks.data_memory_bus
        from gateware.machines.mega138k_ae350.boot_contract import region_size
        self.FLASH_BYTES = machine.board.resources[machine.devices.boot_flash].size_bytes
        self.ROM_SIZE = region_size(self.FLASH_BYTES)
        self.USER_FLASH_BYTES = machine.board.resources[machine.devices.flash].size_bytes
        # The SFP+ controller is built only when the machine binds both cages.
        self.SFP_PRESENT = int(bool(getattr(machine.devices, 'sfp0', None) and getattr(machine.devices, 'sfp1', None)))
        gpio = self.io.gpio_binding()
        self.GPIO_LED_COUNT = gpio['led_count']
        self.GPIO_BUTTON_COUNT = gpio['button_count']
        self.GPIO_LED_BIT = gpio['led_bit']
        self.GPIO_BUTTON_BIT = gpio['button_bit']
        self.GPIO_INJECT_BIT = gpio['inject_bit']
        self.GPIO_LED_MASK = gpio['led_mask']
        self.GPIO_BUTTON_MASK = gpio['button_mask']
        self.GPIO_IRQ_MASK = gpio['irq_mask']
        self.ROM_CODE_BYTES = self.ROM_SIZE
        self.WINDOWS = (('ddr', self.DDR_BASE, self.DDR_SIZE, 'implemented'), ('boot-xip', self.ROM_BASE, self.ROM_SIZE, 'SDK-Flash-U4-offset-0x600000'), ('flash-control', self.FLASH_CTRL_BASE, 4096, 'SDK-FR-APB'), ('sdk-flash-rest', self.ROM_BASE + self.ROM_SIZE, self.XIP_SIZE - self.ROM_SIZE, 'SDK-aperture-capacity-bounds-unqualified'), ('ilm', self.ILM_BASE, self.ILM_SIZE, 'hard-local-memory'), ('dlm', self.DLM_BASE, self.DLM_SIZE, 'hard-local-memory'), ('platform-csr', self.CSR_BASE, self.CSR_SIZE, 'implemented'), ('periph-csr', 4160815104, 524288, 'audio-mic-video-ws2812-board-io'), ('ethernet-csr', self.ETH_BASE, 4096, 'PHY-management-and-DDR-DMA-ring')) + ((('sfp-csr', self.SFP_BASE, 4096, 'SFP-10GBASE-R-controller'),) if self.SFP_PRESENT else ()) + (('ext-ahb', self.EXT_AHB_BASE, self.EXT_AHB_SIZE, 'audio-mic-loopback-data'),)

    def __getattr__(self, name):
        if name.isupper() and name in globals():
            return globals()[name]
        raise AttributeError(name)

    def constants(self):
        return {**{name:value for name,value in globals().items() if name.isupper()},
                **{name:value for name,value in vars(self).items() if name.isupper()}}

    def interface(self):
        binding = self.io.gpio_binding()
        return dict(ethernet_management=dict(csr=self.ETH_BASE, clock_hz=25000000, backend='clause22-mdio+liteeth-dma-ring', packets=dict(clock_hz=self.machine.clocks.ethernet_tx, mtu=1530, id_offset=0x10, version=2, data_width=32, tx_storage_clock_hz=self.FABRIC_HZ, descriptor_bytes=32, ring_max=256, irq=self.EXT_INT_ETHERNET, io_delays='PHY-TX-RX-2ns', rx_data_delay_ps=self.machine.rtl.ethernet_rx_delay_ps, rx_timing='routed-ddr-input-v1', tx_timing='routed-ddr-skew-v1', transport='DDR-descriptor-rings')), usb=dict(csr=self.USB_BASE, clock_hz=self.machine.clocks.usb, speed='full', backend='PIO-SoftPHY', irq='polling', diagnostic_offsets=[0x100,0x104,0x108]), cpu='ae350-rv32imac', image_version=1, load=self.DDR_CODE, management=dict(spi=dict(csr=self.SPI_BASE,backend='fabric-QSPI',register_abi=1,data_width=4,clock_hz=self.FABRIC_HZ//2,size_bytes=self.USER_FLASH_BYTES,flash='user-U7',pins={n:list(v.pins) for n,v in self.machine.board.resources[self.machine.devices.flash].signals.items()}),i2c=dict(csr=self.I2C_BASE,backend='ATCIIC100',pins={n:list(v.pins) for n,v in self.machine.board.resources[self.machine.devices.i2c].signals.items()},mux=dict(pins=list(self.machine.board.resources[self.machine.devices.twi_mux].signals['sel'].pins),gpio_shift=self.TWI_MUX_GPIO_SHIFT,reset=0))), sd=dict(csr=self.SD_BASE, abi=self.ABI_VERSION, transport='litesdcard-native-4bit', clock_domain='mem', default_hz=25000000, clock_hz=self.DATA_HZ, cdc='retained-APB-AHB-REQ-ACK', dma='hard-DMAC-AHB-FIFO', tx=dict(data=self.SD_TX_DATA,request=self.SD_TX_REQ,selector=self.SD_TX_DMA_SEL), rx=dict(data=self.SD_RX_DATA,request=self.SD_RX_REQ,selector=self.SD_RX_DMA_SEL), pins={name:list(sig.pins) for name,sig in self.machine.board.resources[self.machine.devices.sd].signals.items()}), video=dict(csr=self.VIDEO_BASE, width=640, height=480, format='RGB565', formats=['RGB565','RGB888','XRGB8888'], modes=[dict(id=i,**asdict(m),actual_pixel_hz=pll_config(m.pixel_hz)['actual_hz']) for i,m in enumerate(MODES)], runtime_modes=True, irq=self.EXT_INT_VIDEO, hpd_irq='sticky-W1C-connect-disconnect', pixel_requested_hz=self.machine.clocks.video, serializer_requested_hz=self.machine.clocks.video * 5, pins=self.io.video_binding(), commit='vblank-snapshot', memory='round-robin-single-outstanding-DDR3-UI', abi=self.ABI_VERSION, registers=self.VIDEO_REGS), audio=dict(pins=self.io.audio_binding(), sample_rate=48000, tx=dict(csr=self.AUDIO_BASE, data=self.AUDIO_DATA, irq=self.EXT_INT_AUDIO, request=self.AUDIO_REQ, selector=self.AUDIO_DMA_SEL, pcm='stereo-s16-le', abi=self.ABI_VERSION), rx=dict(csr=self.MIC_BASE, data=self.MIC_DATA, irq=self.EXT_INT_MIC, request=self.MIC_REQ, selector=self.MIC_DMA_SEL, pcm='mono-s24-in-s32-le', abi=self.ABI_VERSION)), memories=self.WINDOWS, reserved=self.SUBRANGES, registers=dict(ddr_status=self.STATUS_ADDR, calib_cycles=self.CALIB_CYCLES_ADDR, reads=self.READ_COUNT_ADDR, writes=self.WRITE_COUNT_ADDR, write_trace=self.WRITE_TRACE_ADDR, read_trace=self.READ_TRACE_ADDR, gpio=self.GPIO_BASE, ws2812=self.WS2812_BASE, board_io=self.IO_BASE, sense=self.SENSE_BASE, plic=self.PLIC_BASE), gpio=dict(leds=list(binding['led_bits']), buttons=list(binding['button_bits']), inject=binding['inject_bit'], irq_mask=binding['irq_mask'], led_active_low=[int(item) for item in binding['led_active_low']], button_active_low=[int(item) for item in binding['button_active_low']]), loader=dict(max_bytes=self.DDR_CODE_MAX, stack_top=self.PAYLOAD_STACK_TOP), uart=dict(base=self.UART_BASE, baud=115200), capabilities=dict(atomics='none', cache='write-back', loader='ae350-20k-frame-v1', dmac='exported-unproven', ext_ahb='loopback', fabric_reader='RGB565-scanout', board_io='led-register-and-hard-gpio-inputs', ws2812='shared-core', fan='pwm-percent-25khz', temperature='die-quarter-degree'))

    def image_abi(self):
        import json
        import zlib
        return zlib.crc32(json.dumps(self.interface(), sort_keys=True, separators=(',', ':')).encode())

    def check(self):
        errors = []
        if len(set(self.VIDEO_REGS.values())) != len(self.VIDEO_REGS) or any((off < 12 or off >= 4096 or off % 4 for off in self.VIDEO_REGS.values())):
            errors.append('invalid video register layout')
        for spans in ([(n, b, s) for (n, b, s, _) in self.WINDOWS], self.SUBRANGES):
            spans = sorted(spans, key=lambda span: span[1])
            for (name, base, size) in spans:
                if size <= 0 or base % 4 or base + size > 1 << 32:
                    errors.append('invalid range: ' + name)
            for (left, right) in zip(spans, spans[1:]):
                if left[1] + left[2] > right[1]:
                    errors.append('%s overlaps %s' % (left[0], right[0]))
        for (name, base, size) in self.SUBRANGES:
            (parent, length) = (self.ILM_BASE, self.ILM_SIZE) if name.startswith('ilm.') else (self.DLM_BASE, self.DLM_SIZE)
            if not parent <= base < base + size <= parent + length:
                errors.append('local subrange escapes memory: ' + name)
        if self.DDR_BASE != 0 or self.DDR_CODE + self.DDR_CODE_MAX > self.DDR_SIZE:
            errors.append('invalid DDR loader layout')
        errors.extend(self.check_software_layout())
        if not 0 < self.DATA_HZ < 200000000:
            errors.append('AE350 data clock must be below 200MHz')
        if not self.EXT_AHB_BASE <= self.LOOPBACK_DATA < self.LOOPBACK_DATA + 4 <= self.EXT_AHB_BASE + self.EXT_AHB_SIZE:
            errors.append('loopback data word escapes the extended AHB window')
        for (name, base) in (('sysinfo', self.SYSINFO_BASE), ('irq', self.IRQ_BASE), ('dma-service', self.DMA_SVC_BASE), ('sense', self.SENSE_BASE)):
            if not self.CSR_BASE <= base < base + 4096 <= self.CSR_BASE + self.CSR_SIZE:
                errors.append(name + ' page escapes the platform CSR window')
        (periph_base, periph_size) = (4160815104, 524288)
        for (name, base) in (('ws2812', self.WS2812_BASE), ('board-io', self.IO_BASE)):
            if not periph_base <= base < base + 4096 <= periph_base + periph_size:
                errors.append(name + ' page escapes the peripheral CSR window')
        errors.extend([])
        binding = self.io.gpio_binding()
        expected = {'led count': (self.GPIO_LED_COUNT, binding['led_count']), 'button count': (self.GPIO_BUTTON_COUNT, binding['button_count']), 'led bit': (self.GPIO_LED_BIT, binding['led_bit']), 'button bit': (self.GPIO_BUTTON_BIT, binding['button_bit']), 'inject bit': (self.GPIO_INJECT_BIT, binding['inject_bit']), 'led mask': (self.GPIO_LED_MASK, binding['led_mask']), 'button mask': (self.GPIO_BUTTON_MASK, binding['button_mask']), 'irq mask': (self.GPIO_IRQ_MASK, binding['irq_mask'])}
        for (name, (have, want)) in expected.items():
            if have != want:
                errors.append('%s does not match board.json' % name)
        described = self.interface()['gpio']
        if described['leds'] != list(binding['led_bits']) or described['buttons'] != list(binding['button_bits']):
            errors.append('image ABI gpio binding does not match board.json')
        if described['led_active_low'] != [int(item) for item in binding['led_active_low']]:
            errors.append('image ABI led polarity does not match board.json')
        if described['button_active_low'] != [int(item) for item in binding['button_active_low']]:
            errors.append('image ABI button polarity does not match board.json')
        return errors

    def check_software_layout(self):
        """DDR ownership by phase, OS handoff placement and OS virtual windows."""
        errors = []
        layout = {name: (base, size, phases) for (name, base, size, phases) in self.DDR_LAYOUT}
        if len(layout) != len(self.DDR_LAYOUT):
            errors.append('duplicate DDR layout name')
        for (name, (base, size, phases)) in layout.items():
            if size <= 0 or base % 4096 or size % 4096 or base + size > self.DDR_BASE + self.DDR_SIZE:
                errors.append('invalid DDR range: ' + name)
            if not phases or not set(phases) <= set(self.DDR_PHASES):
                errors.append('invalid DDR phases: ' + name)
        for phase in self.DDR_PHASES:
            live = sorted(((base, size, name) for (name, (base, size, phases)) in layout.items() if phase in phases))
            for (left, right) in zip(live, live[1:]):
                if left[0] + left[1] > right[0]:
                    errors.append('%s overlaps %s in %s phase' % (left[2], right[2], phase))
        if not 0 < self.OS_MEMORY_BYTES <= self.DDR_SIZE:
            errors.append('OS memory exceeds DDR')
        for (name, (base, size, phases)) in layout.items():
            if 'os' in phases and base + size > self.DDR_BASE + self.OS_MEMORY_BYTES:
                errors.append(name + ' is outside OS memory')
        if self.OS_KERNEL_BASE % self.OS_KERNEL_ALIGN:
            errors.append('kernel load address is not Sv32 megapage aligned')
        if self.OS_SBI_BYTES & (self.OS_SBI_BYTES - 1) or self.OS_SBI_BASE % self.OS_SBI_BYTES:
            errors.append('OpenSBI region must be a naturally aligned power of two')
        if self.OS_UBOOT_BASE != self.OS_SBI_BASE + self.OS_SBI_BYTES:
            errors.append('os.uboot must follow os.sbi: FW_PAYLOAD_OFFSET is the os.sbi size')
        page = self.OS_PAGE_OFFSET
        kernel_va = (('vmalloc', page - self.OS_VMALLOC_BYTES, self.OS_VMALLOC_BYTES),
                     ('linear', page, self.OS_MEMORY_BYTES))
        if page + self.OS_MEMORY_BYTES > 1 << 32 or page < self.OS_VMALLOC_BYTES:
            errors.append('OS kernel virtual layout exceeds Sv32')
        (rbase, rsize) = (self.OS_RESERVED_VA_BASE, self.OS_RESERVED_VA_BYTES)
        (vbase, vsize) = kernel_va[0][1:]
        if rsize <= 0 or rbase % 4096 or rsize % 4096 or rbase < vbase or rbase + rsize > vbase + vsize:
            errors.append('reserved kernel VA must be a page range inside Linux vmalloc')
        for (local, lbase, lsize) in (('ilm', self.ILM_BASE, self.ILM_SIZE), ('dlm', self.DLM_BASE, self.DLM_SIZE)):
            if lbase < vbase:
                errors.append('%s window lies below Linux vmalloc (user/fixmap VA) and cannot be kept unmapped' % local)
            for (window, wbase, wsize) in kernel_va:
                reserved = window == 'vmalloc' and rbase <= lbase < lbase + lsize <= rbase + rsize
                if lbase < wbase + wsize and wbase < lbase + lsize and not reserved:
                    errors.append('%s window overlaps Linux %s outside the reserved kernel VA' % (local, window))
        return errors

    def software_layout(self):
        """Reviewable DDR ownership and OS handoff contract."""
        return dict(ddr=[dict(name=name, base=base, size=size, phases=list(phases))
                         for (name, base, size, phases) in self.DDR_LAYOUT],
                    os=dict(memory=[self.DDR_BASE, self.OS_MEMORY_BYTES], kernel=self.OS_KERNEL_BASE,
                            sbi=[self.OS_SBI_BASE, self.OS_SBI_BYTES], dtb=self.OS_DTB_BASE,
                            uboot=[self.OS_UBOOT_BASE, self.OS_UBOOT_BYTES],
                            firmware_fdt=[self.OS_FIRMWARE_FDT_BASE, self.OS_FIRMWARE_FDT_BYTES],
                            page_offset=self.OS_PAGE_OFFSET,
                            vmalloc=[self.OS_PAGE_OFFSET - self.OS_VMALLOC_BYTES, self.OS_VMALLOC_BYTES],
                            reserved_va=[self.OS_RESERVED_VA_BASE, self.OS_RESERVED_VA_BYTES]))

    def dts_header(self):
        """Integer constants for the C-preprocessed device tree (dtc rejects C suffixes)."""
        self.require()
        constants = {name: value for (name, value) in self.constants().items() if isinstance(value, int)}
        return '/* Generated from address_map.py. */\n' + ''.join(
            '#define AE350_%s 0x%x\n' % item for item in sorted(constants.items()))

    def require(self):
        errors = self.check()
        if errors:
            raise ValueError('AE350 address map: ' + '; '.join(errors))

    def c_header(self):
        self.require()
        constants = {name: value for (name, value) in self.constants().items() if name.isupper() and isinstance(value, int)}
        return '#pragma once\n/* Generated from ae350_map.py. */\n' + ''.join(('#define AE350_%s 0x%xu\n' % item for item in sorted(constants.items()))) + ''.join(('#define AE350_VIDEO_%s 0x%xu\n' % item for item in sorted(self.VIDEO_REGS.items()))) + '#define AE350_IMAGE_ABI 0x%08xu\n' % self.image_abi()

    def ld_fragment(self):
        self.require()
        return '\n'.join(['/* Generated from ae350_map.py. */', 'MEMORY {', ' rom (rx) : ORIGIN = 0x%x, LENGTH = 0x%x' % (self.ROM_BASE, self.ROM_SIZE), ' ilm (rwx) : ORIGIN = 0x%x, LENGTH = 0x%x' % (self.ILM_BASE, self.ILM_CODE_BYTES), ' dlm (rw) : ORIGIN = 0x%x, LENGTH = 0x%x' % (self.DLM_BASE, self.DLM_DATA_BYTES), ' ddr (rwx) : ORIGIN = 0x%x, LENGTH = 0x%x' % (self.DDR_CODE, self.DDR_CODE_MAX), ' bss (rw) : ORIGIN = 0x%x, LENGTH = 0x%x' % (self.BIOS_BSS_BASE, self.BIOS_BSS_BYTES), '}', '__ilm_base = 0x%x;' % self.ILM_BASE, '__ilm_limit = 0x%x;' % (self.ILM_BASE + self.ILM_SIZE), '__dlm_base = 0x%x;' % self.DLM_BASE, '__dlm_limit = 0x%x;' % (self.DLM_BASE + self.DLM_SIZE), '__local_stack_bottom = 0x%x;' % self.ROM_STACK_BOTTOM, '__local_stack_top = 0x%x;' % self.ROM_STACK_TOP, '__payload_stack_bottom = 0x%x;' % self.PAYLOAD_STACK_BOTTOM, '__payload_stack_top = 0x%x;' % self.PAYLOAD_STACK_TOP]) + '\n'
