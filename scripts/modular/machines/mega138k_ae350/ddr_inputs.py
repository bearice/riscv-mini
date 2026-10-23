"""Project DDR export fields projected from the selected hardware snapshot."""


def profile_for(machine):
    """Keep the native IPC parameters rather than invent a second timing table."""
    controller = machine.ip['ddr3']
    clock_hz = controller.parameters['Memory_Clock'] * 1000000
    resource = machine.board.resources[machine.memory.resource]
    return dict(
        name='%gMHz' % (clock_hz / 1000000),
        ip_module=controller.module,
        ip_identity={name: machine.artifacts[name]['identity'] for name in ('ddr3', 'ddr_pll')},
        window_base=machine.memory.base,
        window_bytes=machine.memory.size_bytes,
        geometry=dict(data_width=len(resource.signals['dq'].pins)),
        parameters=dict(controller.parameters),
        pll_parameters=dict(machine.ip['ddr_pll'].parameters),
        clocks=dict(ck_hz=clock_hz, sys_hz=machine.clocks.sys,
                    cpu_hz=machine.clocks.cpu, data_hz=machine.clocks.data_memory_bus),
    )
