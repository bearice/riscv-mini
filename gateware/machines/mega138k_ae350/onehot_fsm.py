"""Migen FSM whose state register GowinSynthesis implements one-hot."""
from migen import FSM, Signal


class OneHotFSM(FSM):
    """SUG550 §5.7: binary state needs a two-level decode before every
    ``ongoing``/``NextValue`` enable; one-hot reduces each to a single FF.

    GowinSynthesis extracts the FSM, and so honours ``syn_encoding``, only
    when the state register is declared before its next value. LiteX sorts
    declarations by name, hence ``state_next`` instead of ``next_state``.
    """
    def do_finalize(self):
        nstates = len(self.actions)
        self.encoding = dict((s, n) for n, s in enumerate(self.actions.keys()))
        self.decoding = {n: s for s, n in self.encoding.items()}

        self.state = Signal(max=nstates, reset=self.encoding[self.reset_state])
        self.state.attr.add(("syn_encoding", "onehot"))
        self.state._enumeration = self.decoding
        self.next_state = Signal(max=nstates, name="state_next")
        self.next_state._enumeration = {n: "{}:{}".format(n, s) for n, s in self.decoding.items()}

        for state, signal in self.before_leaving_signals.items():
            encoded = self.encoding[state]
            self.comb += signal.eq((self.state == encoded) & ~(self.next_state == encoded))
        if self.reset_state in self.after_entering_signals:
            self.after_entering_signals[self.reset_state].reset = 1
        for state, signal in self.before_entering_signals.items():
            encoded = self.encoding[state]
            self.comb += signal.eq(~(self.state == encoded) & (self.next_state == encoded))

        self._finalize_sync(self._lower_controls())
