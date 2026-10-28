"""AE350 logical RTL ports bound to the same resource model used by 20K."""
from gateware.io import IOBindings


def bind_ports(machine):
    io = IOBindings(machine)
    d = machine.devices
    io.pad('clk50', 'clock', 'clk')
    io.pad('reset_key', 'core_reset', 'reset')
    io.resource('serial', 'debug_uart')
    io.resource('cpu_jtag', d.cpu_jtag)
    io.pad('fan_pwm', d.fan, 'pwm')
    io.pad('fan_tach', d.fan, 'tach')
    io.resource('user_flash', d.flash)
    io.resource('boot_flash', d.boot_flash)
    io.resource('system_i2c', d.i2c)
    io.pad('twi_mux_sel', d.twi_mux, 'sel')
    io.resource('sdcard', d.sd, rename={'detect':'cd'})
    io.resource('audio', d.audio, rename={'data': 'din'})
    io.resource('microphone', d.microphones[0])
    for group, port in (('leds', 'user_led'), ('buttons', 'user_button')):
        for index, resource in enumerate(getattr(machine.gpio, group)):
            io.pad(port, resource, index=index)
    io.pad('ws2812', d.ws2812, 'data')
    memory = machine.memory.resource
    signals = io.board.resources[memory].signals
    for name in ('addr', 'ba', 'dm', 'dq'):
        for index in range(len(signals[name].pins)):
            io.pad('ddr3_' + name, memory, name, index, bit=index)
    for name in ('cs_n', 'ras_n', 'cas_n', 'we_n', 'cke', 'odt', 'reset_n'):
        io.pad('ddr3_' + name, memory, name)
    io.differential_port('ddr3_ck', 'ddr3_ck_n', (signals['ck'],))
    for index in range(4):
        io.differential_port('ddr3_dqs%d' % index, 'ddr3_dqs%d_n' % index,
                             (signals['dqs_' + str(index)],))
    video = io.board.resources[d.video].signals
    if 'hpd' in video:
        io.pad('hdmi_hpd', d.video, 'hpd')
    io.differential_port('hdmi_clk_p', 'hdmi_clk_n', (video['clk'],))
    io.differential_port('hdmi_data_p', 'hdmi_data_n', tuple(video['data_' + str(i)] for i in range(3)))
    return io
