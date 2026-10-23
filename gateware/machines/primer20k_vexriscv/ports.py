"""20K logical RTL ports bound to the common physical resource model."""
from dataclasses import replace
from gateware.io import IOBindings


def bind_ports(machine):
    io = IOBindings(machine)
    d = machine.devices
    io.pad('clk27', 'clock', 'clk')
    io.pad('btn_n', 'core_reset', 'reset')
    io.resource('serial', 'debug_uart')
    io.resource('ddram', machine.memory.resource, rename={'addr': 'a'})
    for group, port in (('leds', 'board_led'), ('buttons', 'btn_n'), ('switches', 'board_switch')):
        for index, resource in enumerate(getattr(machine.gpio, group)):
            io.pad(port, resource, index=index + (group == 'buttons'))
    io.pad('rgb_led', d.ws2812, 'data')
    io.resource('sdcard', d.sd, rename={'detect': 'cd'})
    sd = io.board.resources[d.sd].signals
    io.signals('spisdcard', dict(clk=sd['clk'], mosi=sd['cmd'],
        miso=replace(sd['data'], pins=sd['data'].pins[:1]),
        cs_n=replace(sd['data'], pins=sd['data'].pins[3:])))
    io.pad('sd_detect', d.sd, 'detect')
    io.resource('spiflash', d.flash)
    io.resource('rmii', d.ethernet)
    io.resource('eth_clocks', d.ethernet_clock, rename={'clk': 'ref_clk'})
    io.resource('usb_ulpi', d.usb)
    io.resource('spi_lcd', d.spi_lcd)
    io.pad('shared_phy_reset_n', d.phy_reset, 'reset')
    io.resource('rgb_lcd', d.video, rename={color + '_lsb_first': color for color in 'rgb'})
    io.resource('audio_dac', d.audio, rename={'data': 'din'})
    for index, resource in enumerate(d.microphones):
        io.resource('microphone' if index == 0 else 'microphone_second', resource)
    return io
