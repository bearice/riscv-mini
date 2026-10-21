"""Packed read-only CSR layouts, shared by gateware and C accessor generation.

Each tuple is (word name, ((legacy accessor name, width), ...)).
No write accessor aliases: control/command semantics stay independent.
"""
from types import SimpleNamespace
from migen import Signal, Cat
from litex.soc.interconnect.csr import CSRStatus

STATUS_LAYOUTS = {
    'board_io': [('inputs', [('buttons',4), ('switches',4), ('pressed',4), ('released',4)])],
    'mic': [('counts', [('level',16), ('captured',16)]),
            ('state', [('busy',1), ('done',1), ('activity',1), ('activity_right',1)])],
    'audio': [('state', [('level',16), ('errors',3), ('busy',1), ('amplifier',1)])],
    'rgb_lcd': [('state', [('active',1), ('busy',1)])],
}

def packed_status(module, bank):
    """Keep internal state Signals separate from their packed read-only bus view."""
    for word, fields in STATUS_LAYOUTS[bank]:
        signals = []
        for name, width in fields:
            signal = Signal(width, name=bank+'_'+name)
            setattr(module, '_'+name, SimpleNamespace(status=signal))
            signals.append(signal)
        if sum(width for _, width in fields) > 32:
            raise ValueError('Packed status exceeds CSR bus word')
        csr = CSRStatus(sum(width for _, width in fields), name=word)
        setattr(module, '_'+word, csr)
        module.comb += csr.status.eq(Cat(*signals))
