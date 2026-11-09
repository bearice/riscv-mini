"""Install local native drivers into the pinned Linux source (WSL)."""
from pathlib import Path
import re
import sys

port = Path(__file__).resolve().parent
sys.path.insert(0, str(port.parent))
import vendor_install

drivers = [
    ('irqchip', 'riscv_mini_irq', 'MINI_IRQ', 'RISCV && OF'),
    ('mmc/host', 'riscv_mini_mmc', 'MMC_RISCV_MINI', 'MMC && RISCV && OF'),
    ('net/ethernet', 'riscv_mini_net', 'NET_RISCV_MINI', 'NETDEVICES && RISCV && OF'),
    ('misc', 'riscv_mini_board', 'BOARD_RISCV_MINI', 'GPIOLIB && INPUT && LEDS_CLASS && RISCV && OF'),
    ('mtd/maps', 'riscv_mini_flash', 'MTD_RISCV_MINI', 'MTD && RISCV && OF'),
    ('spi', 'riscv_mini_spi', 'SPI_RISCV_MINI', 'SPI && RISCV && OF'),
    ('usb/host', 'riscv_mini_usb', 'USB_RISCV_MINI', 'USB && RISCV && OF'),
    ('../sound/drivers', 'riscv_mini_audio', 'SND_RISCV_MINI', 'SND && RISCV && OF'),
]
# Machine-specific upstream edits share this entry with the common overlays:
# one small patch per purpose, applied in file-name order.
MACHINES = {'primer20k_vexriscv': dict(patches=None),
            'mega138k_ae350': dict(patches=port / 'ae350/patches')}


def inputs(machine):
    return [*sorted(port.glob('*.c')), *sorted(port.glob('*.h')), Path(__file__).resolve(),
            *vendor_install.patches(MACHINES[machine]['patches'])]


def install(src, machine):
    for directory, name, config, depends in drivers:
        folder = src / 'drivers' / directory
        source = port / (name + '.c')
        if not source.exists():
            continue
        (folder / source.name).write_bytes(source.read_bytes().replace(b'\r\n', b'\n'))
        kconfig = folder / 'Kconfig'
        extra = {'SND_RISCV_MINI': '\tselect SND_PCM\n',
                 'MINI_IRQ': '\tselect LITEX\n'}.get(config, '')
        config_text = kconfig.read_text()
        if f'config {config}\n' not in config_text:
            with kconfig.open('a') as f:
                f.write(f'\nconfig {config}\n\tbool "riscv-mini native {name}"\n\tdepends on {depends}\n' + extra)
            with (folder / 'Makefile').open('a') as f:
                f.write(f'obj-$(CONFIG_{config}) += {name}.o\n')
        elif extra:
            stanza = f'config {config}\n\tbool "riscv-mini native {name}"\n\tdepends on {depends}\n'
            if stanza + extra not in config_text:
                kconfig.write_text(config_text.replace(stanza, stanza + extra))

    usb_make = src / 'drivers/usb/Makefile'
    (src / 'drivers/usb/host/riscv_mini_usb_port.h').write_bytes(
        (port / 'riscv_mini_usb_port.h').read_bytes().replace(b'\r\n', b'\n'))
    usb_entry = 'obj-$(CONFIG_USB_RISCV_MINI) += host/\n'
    if usb_entry not in usb_make.read_text():
        with usb_make.open('a') as f: f.write(usb_entry)

    # Extend upstream LiteUART without changing its existing 8-bit CSR binding.
    uart = src / 'drivers/tty/serial/liteuart.c'
    text = uart.read_text()
    if 'mini_uart_read' not in text:
        marker = '/* events */'
        helpers = '''/* Native single-word CSRs on the riscv-mini variant. */
static bool mini_uart_native(struct uart_port *port)
{
    return port->dev && of_device_is_compatible(port->dev->of_node,
                                               "riscv-mini,liteuart32");
}
static u8 mini_uart_read(struct uart_port *port, unsigned int offset)
{
    return mini_uart_native(port) ? readl(port->membase + offset) :
                                   litex_read8(port->membase + offset);
}
static void mini_uart_write(struct uart_port *port, unsigned int offset, u8 value)
{
    if (mini_uart_native(port)) writel(value, port->membase + offset);
    else litex_write8(port->membase + offset, value);
}
'''
        text = re.sub(r'litex_read8\((?:port->membase|membase) \+ (OFF_\w+)\)',
                      r'mini_uart_read(port, \1)', text)
        text = re.sub(r'litex_write8\((?:port->membase|membase) \+ (OFF_\w+), ([^;)]+)\)',
                      r'mini_uart_write(port, \1, \2)', text)
        text = text.replace(marker, helpers + '\n' + marker)
        text = text.replace('unsigned char __iomem *membase = port->membase;\n', '')
        text = text.replace('{ .compatible = "litex,liteuart" },',
                            '{ .compatible = "litex,liteuart" },\n\t{ .compatible = "riscv-mini,liteuart32" },')
        # With IRQs, stop_rx must mask the RX source rather than touching an
        # uninitialized polling timer. Bound RX work so noisy inputs cannot starve.
        text = text.replace('/* just delete timer */\n\tdel_timer(&uart->timer);',
                            'if (port->irq) liteuart_update_irq_reg(port, false, EV_RX);\n\telse del_timer(&uart->timer);')
        text = text.replace('u8 ch;\n\n\twhile (!mini_uart_read(port, OFF_RXEMPTY))',
                            'u8 ch;\n\tunsigned int budget = 256;\n\n\twhile (budget-- && !mini_uart_read(port, OFF_RXEMPTY))')
        text = text.replace('/* not really tx empty, just checking if tx is not full */',
                            'if (mini_uart_native(port))\n\t\treturn readl(port->membase + 0x18) ? TIOCSER_TEMT : 0;')
        uart.write_text(text)

    # This board's LiteUART baud rate is fixed by gateware, not a divisor CSR.
    text = uart.read_text()
    baud_line = '\tbaud = uart_get_baud_rate(port, new, old, 0, 460800);'
    fixed_baud = '''
    if (mini_uart_native(port)) {
        baud = 115200;
        new->c_cflag = (new->c_cflag & ~(CSIZE | PARENB | CSTOPB)) | CS8;
        tty_termios_encode_baud_rate(new, baud, baud);
    }'''
    if 'tty_termios_encode_baud_rate(new, baud, baud)' not in text:
        text = text.replace(baud_line, baud_line + fixed_baud)
    if 'port->mapbase = platform_get_resource' not in text:
        text = text.replace('port->dev = &pdev->dev;',
            'port->dev = &pdev->dev;\n\tport->mapbase = platform_get_resource(pdev, IORESOURCE_MEM, 0)->start;')
    uart.write_text(text)

    # The native CSR bus is in-order and uncached. Keep IRQ-enable publication
    # ordered, but avoid flushing private caches for each character FIFO access.
    text = uart.read_text()
    text = text.replace('return port->dev && of_device_is_compatible(port->dev->of_node,\n                                               "riscv-mini,liteuart32");',
                        'return port->private_data != NULL;')
    if 'port->private_data =' not in text:
        text = text.replace('port->dev = &pdev->dev;',
            'port->dev = &pdev->dev;\n\tport->private_data = (void *)(unsigned long)of_device_is_compatible(pdev->dev.of_node, "riscv-mini,liteuart32");')
    text = text.replace('mini_uart_native(port) ? readl(port->membase + offset)',
                                   'mini_uart_native(port) ? readl_relaxed(port->membase + offset)')
    text = text.replace('if (mini_uart_native(port)) writel(value, port->membase + offset);',
        '''if (mini_uart_native(port)) {
        if (offset == 0x14) writel(value, port->membase + offset);
        else writel_relaxed(value, port->membase + offset);
    }''')
    uart.write_text(text)

    # The same framebuffer overlay is installed for both machines; enabling it is
    # a configuration/device-tree decision, not a separate build workflow.
    fb = src / 'drivers/video/fbdev'
    (fb / 'riscv_mini_fb.c').write_bytes((port / 'riscv_mini_fb.c').read_bytes())
    if 'config FB_RISCV_MINI\n' not in (fb / 'Kconfig').read_text():
        with (fb / 'Kconfig').open('a') as stream:
            stream.write('\nconfig FB_RISCV_MINI\n    bool "riscv-mini coherent RGB LCD framebuffer"\n    depends on FB && OF && RISCV\n    select FB_DMAMEM_HELPERS\n')
        with (fb / 'Makefile').open('a') as stream:
            stream.write('obj-$(CONFIG_FB_RISCV_MINI) += riscv_mini_fb.o\n')

    # AE350 video drivers (fbdev and DRM/KMS share a register header).
    (fb / 'riscv_mini_ae350_regs.h').write_bytes((port / 'riscv_mini_ae350_regs.h').read_bytes())
    (fb / 'riscv_mini_ae350_fb.c').write_bytes((port / 'riscv_mini_ae350_fb.c').read_bytes())
    if 'config FB_AE350_VIDEO\n' not in (fb / 'Kconfig').read_text():
        with (fb / 'Kconfig').open('a') as stream:
            stream.write('\nconfig FB_AE350_VIDEO\n    bool "ae350 video framebuffer with GFX2D accel (fbdev)"\n    depends on FB && OF && RISCV\n    select FB_DMAMEM_HELPERS\n')
        with (fb / 'Makefile').open('a') as stream:
            stream.write('obj-$(CONFIG_FB_AE350_VIDEO) += riscv_mini_ae350_fb.o\n')

    drm = src / 'drivers/gpu/drm/tiny'
    (drm / 'riscv_mini_ae350_regs.h').write_bytes((port / 'riscv_mini_ae350_regs.h').read_bytes())
    (drm / 'riscv_mini_ae350_drm.c').write_bytes((port / 'riscv_mini_ae350_drm.c').read_bytes())
    if 'config DRM_AE350_VIDEO\n' not in (drm / 'Kconfig').read_text():
        with (drm / 'Kconfig').open('a') as stream:
            stream.write('\nconfig DRM_AE350_VIDEO\n    tristate "ae350 KMS/DRM video driver"\n    depends on DRM && OF && RISCV && MMU\n    select DRM_KMS_HELPER\n    select DRM_GEM_DMA_HELPER\n    select DRM_GEM_FRAMEBUFFER_HELPER\n    select DRM_SIMPLE_KMS_HELPER\n    select DRM_CLIENT_SELECTION\n    help\n      KMS/DRM driver for the AE350 video engine (fixed and custom modes).\n')
        with (drm / 'Makefile').open('a') as stream:
            stream.write('obj-$(CONFIG_DRM_AE350_VIDEO) += riscv_mini_ae350_drm.o\n')

    vendor_install.apply_patches(src, MACHINES[machine]['patches'])


if __name__ == '__main__':
    vendor_install.main(__doc__, MACHINES, install, inputs, default='primer20k_vexriscv')
