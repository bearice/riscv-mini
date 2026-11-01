"""Bridge between 32-bit LiteEth EthernetDMA (50 MHz sys_clk)
and 64-bit Gowin 10G Ethernet MAC (156.25 MHz xge_clk).

Includes:
- Asynchronous FIFOs with Gray code CDC
- Width conversion: 32-bit <-> 64-bit with byte enable handling
- Store-and-forward TX buffer to prevent 10 Gbps line-rate underflow
"""
from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.fifo import AsyncFIFO
from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from liteeth.common import eth_phy_description


class SFP10GDMABridge(LiteXModule):
    def __init__(self, depth=512):
        # ---------------------------------------------------------------------
        # DMA Interface (50 MHz sys domain)
        # ---------------------------------------------------------------------
        self.dma_sink = stream.Endpoint(eth_phy_description(32))   # TX from DMA
        self.dma_source = stream.Endpoint(eth_phy_description(32)) # RX to DMA

        # ---------------------------------------------------------------------
        # 10G MAC Interface (156.25 MHz eth_sfp_tx / eth_sfp_rx domains)
        # ---------------------------------------------------------------------
        # TX into 10G MAC (domain: eth_sfp_tx)
        self.tx_mac_valid = Signal()
        self.tx_mac_data = Signal(64)
        self.tx_mac_byte = Signal(8)
        self.tx_mac_last = Signal()
        self.tx_mac_error = Signal()
        self.tx_mac_ready = Signal()

        # RX from 10G MAC (domain: eth_sfp_rx)
        self.rx_mac_valid = Signal()
        self.rx_mac_data = Signal(64)
        self.rx_mac_byte = Signal(8)
        self.rx_mac_last = Signal()
        self.rx_mac_error = Signal()

        # =====================================================================
        # RX PATH: 10G MAC (156.25 MHz) -> RX Async FIFO -> DMA (50 MHz sys)
        # =====================================================================
        # Format in FIFO: {rx_error(1), rx_last(1), rx_byte(8), rx_data(64)} = 74 bits
        rx_fifo = ClockDomainsRenamer({"write": "eth_sfp_rx", "read": "sys"})(
            AsyncFIFO(width=74, depth=depth)
        )
        self.submodules.rx_fifo = rx_fifo

        # Push into RX FIFO on 156.25 MHz MAC clock
        self.comb += [
            rx_fifo.din.eq(Cat(self.rx_mac_data, self.rx_mac_byte, self.rx_mac_last, self.rx_mac_error)),
            rx_fifo.we.eq(self.rx_mac_valid & rx_fifo.writable),
        ]

        # Read and unpack 64-bit -> two 32-bit words in 50 MHz sys domain
        rx_data_64 = rx_fifo.dout[:64]
        rx_byte_8  = rx_fifo.dout[64:72]
        rx_last_1  = rx_fifo.dout[72]
        rx_error_1 = rx_fifo.dout[73]

        RX_S_W0 = 0
        RX_S_W1 = 1
        rx_state = Signal(reset=RX_S_W0)

        # In S_W0: emit lower 32-bit word
        # In S_W1: emit upper 32-bit word
        rx_w0_is_last = Signal()
        self.comb += [
            rx_w0_is_last.eq(rx_last_1 & (rx_byte_8[4:8] == 0)),
            # FIFO pop is combinatorial with DMA handshake
            rx_fifo.re.eq(
                self.dma_source.ready & rx_fifo.readable & (
                    (rx_state == RX_S_W1) |
                    ((rx_state == RX_S_W0) & rx_w0_is_last)
                )
            ),
            If(rx_state == RX_S_W0,
                self.dma_source.valid.eq(rx_fifo.readable),
                self.dma_source.data.eq(rx_data_64[:32]),
                self.dma_source.be.eq(rx_byte_8[:4]),
                self.dma_source.last.eq(rx_w0_is_last),
                self.dma_source.error.eq(rx_error_1 & rx_w0_is_last),
            ).Else(
                self.dma_source.valid.eq(rx_fifo.readable),
                self.dma_source.data.eq(rx_data_64[32:64]),
                self.dma_source.be.eq(rx_byte_8[4:8]),
                self.dma_source.last.eq(rx_last_1),
                self.dma_source.error.eq(rx_error_1),
            )
        ]

        self.sync += [
            Case(rx_state, {
                RX_S_W0: [
                    If(rx_fifo.readable & self.dma_source.ready,
                        If(~rx_w0_is_last,
                            rx_state.eq(RX_S_W1),
                        )
                    )
                ],
                RX_S_W1: [
                    If(self.dma_source.ready,
                        rx_state.eq(RX_S_W0),
                    )
                ]
            })
        ]

        # =====================================================================
        # TX PATH: DMA (50 MHz sys) -> Pack 32b->64b -> TX Async FIFO -> 10G MAC (156.25 MHz)
        # =====================================================================
        # Format in FIFO: {tx_error(1), tx_last(1), tx_byte(8), tx_data(64)} = 74 bits
        tx_fifo = ClockDomainsRenamer({"write": "sys", "read": "eth_sfp_tx"})(
            AsyncFIFO(width=74, depth=depth)
        )
        self.submodules.tx_fifo = tx_fifo

        tx_buf_data = Signal(32)
        tx_buf_be = Signal(4)
        tx_buf_err = Signal()

        TX_P_W0 = 0
        TX_P_W1 = 1
        tx_pack_state = Signal(reset=TX_P_W0)

        tx_fifo_din_data = Signal(64)
        tx_fifo_din_byte = Signal(8)
        tx_fifo_din_last = Signal()
        tx_fifo_din_err  = Signal()
        tx_fifo_we_sys   = Signal()

        self.comb += [
            tx_fifo.din.eq(Cat(tx_fifo_din_data, tx_fifo_din_byte, tx_fifo_din_last, tx_fifo_din_err)),
            tx_fifo.we.eq(tx_fifo_we_sys),
        ]

        # Count completed frames committed into TX FIFO in sys domain
        tx_frames_in_cnt = Signal(8)

        self.sync += [
            tx_fifo_we_sys.eq(0),
            If(self.dma_sink.valid & self.dma_sink.ready,
                Case(tx_pack_state, {
                    TX_P_W0: [
                        If(self.dma_sink.last,
                            # Single 32-bit word frame (or odd trailing word)
                            tx_fifo_din_data.eq(Cat(self.dma_sink.data, Constant(0, 32))),
                            tx_fifo_din_byte.eq(Cat(self.dma_sink.be, Constant(0, 4))),
                            tx_fifo_din_last.eq(1),
                            tx_fifo_din_err.eq(self.dma_sink.error),
                            tx_fifo_we_sys.eq(1),
                            tx_frames_in_cnt.eq(tx_frames_in_cnt + 1),
                            tx_pack_state.eq(TX_P_W0),
                        ).Else(
                            tx_buf_data.eq(self.dma_sink.data),
                            tx_buf_be.eq(self.dma_sink.be),
                            tx_buf_err.eq(self.dma_sink.error),
                            tx_pack_state.eq(TX_P_W1),
                        )
                    ],
                    TX_P_W1: [
                        tx_fifo_din_data.eq(Cat(tx_buf_data, self.dma_sink.data)),
                        tx_fifo_din_byte.eq(Cat(tx_buf_be, self.dma_sink.be)),
                        tx_fifo_din_last.eq(self.dma_sink.last),
                        tx_fifo_din_err.eq(tx_buf_err | self.dma_sink.error),
                        tx_fifo_we_sys.eq(1),
                        If(self.dma_sink.last,
                            tx_frames_in_cnt.eq(tx_frames_in_cnt + 1),
                        ),
                        tx_pack_state.eq(TX_P_W0),
                    ]
                })
            )
        ]
        self.comb += self.dma_sink.ready.eq(tx_fifo.writable)

        # Synchronize committed frame count to eth_sfp_tx domain via Gray counter
        tx_frames_in_gray = Signal(8)
        self.comb += tx_frames_in_gray.eq(tx_frames_in_cnt ^ (tx_frames_in_cnt >> 1))

        tx_frames_in_sync_gray = Signal(8)
        self.specials += MultiReg(tx_frames_in_gray, tx_frames_in_sync_gray, "eth_sfp_tx")

        # Convert Gray back to binary in eth_sfp_tx domain
        tx_frames_in_bin = Signal(8)
        bin_bits = []
        for i in range(8):
            bit = tx_frames_in_sync_gray[7]
            for j in range(6, i - 1, -1):
                bit = bit ^ tx_frames_in_sync_gray[j]
            bin_bits.append(bit)
        self.comb += tx_frames_in_bin.eq(Cat(*bin_bits))

        # Draining FSM in eth_sfp_tx domain (156.25 MHz)
        tx_frames_out_cnt = Signal(8)
        has_frame_ready = Signal()
        self.comb += has_frame_ready.eq(tx_frames_in_bin != tx_frames_out_cnt)

        TX_DRAIN_IDLE = 0
        TX_DRAIN_SEND = 1
        tx_drain_state = Signal(reset=TX_DRAIN_IDLE)

        self.comb += [
            self.tx_mac_valid.eq(tx_fifo.readable & (tx_drain_state == TX_DRAIN_SEND)),
            self.tx_mac_data.eq(tx_fifo.dout[:64]),
            self.tx_mac_byte.eq(tx_fifo.dout[64:72]),
            self.tx_mac_last.eq(tx_fifo.dout[72]),
            self.tx_mac_error.eq(tx_fifo.dout[73]),
            tx_fifo.re.eq(self.tx_mac_ready & tx_fifo.readable & (tx_drain_state == TX_DRAIN_SEND)),
        ]

        self.sync.eth_sfp_tx += [
            Case(tx_drain_state, {
                TX_DRAIN_IDLE: [
                    If(has_frame_ready & tx_fifo.readable,
                        tx_drain_state.eq(TX_DRAIN_SEND),
                    )
                ],
                TX_DRAIN_SEND: [
                    If(self.tx_mac_ready & tx_fifo.readable,
                        If(tx_fifo.dout[72], # last beat of frame
                            tx_frames_out_cnt.eq(tx_frames_out_cnt + 1),
                            tx_drain_state.eq(TX_DRAIN_IDLE),
                        )
                    )
                ]
            })
        ]


class SFP1GDMABridge(LiteXModule):
    """Bridge between 32-bit LiteEth DMA (50 MHz sys) and 8-bit Gowin 1G MAC (125 MHz)."""
    def __init__(self, depth=512):
        # DMA Interface (50 MHz sys domain)
        self.dma_sink = stream.Endpoint(eth_phy_description(32))   # TX from DMA
        self.dma_source = stream.Endpoint(eth_phy_description(32)) # RX to DMA

        # 1G MAC Interface (125 MHz eth_sfp1_tx / eth_sfp1_rx domains)
        self.tx_mac_valid = Signal()
        self.tx_mac_data = Signal(8)
        self.tx_mac_last = Signal()
        self.tx_mac_error = Signal()
        self.tx_mac_ready = Signal()

        self.rx_mac_valid = Signal()
        self.rx_mac_data = Signal(8)
        self.rx_mac_last = Signal()
        self.rx_mac_error = Signal()

        # =====================================================================
        # TX PATH: DMA (50 MHz sys) -> Async FIFO -> 1G MAC (125 MHz)
        # =====================================================================
        # Format in FIFO: {tx_error(1), tx_last(1), tx_be(4), tx_data(32)} = 38 bits
        tx_fifo = ClockDomainsRenamer({"write": "sys", "read": "eth_sfp1_tx"})(
            AsyncFIFO(width=38, depth=depth)
        )
        self.submodules.tx_fifo = tx_fifo

        self.comb += [
            tx_fifo.din.eq(Cat(self.dma_sink.data, self.dma_sink.be, self.dma_sink.last, self.dma_sink.error)),
            tx_fifo.we.eq(self.dma_sink.valid & tx_fifo.writable),
            self.dma_sink.ready.eq(tx_fifo.writable),
        ]

        # Store-and-Forward TX Commit Tracking
        tx_frames_in_sys = Signal(8)
        self.sync += [
            If(self.dma_sink.valid & self.dma_sink.ready & self.dma_sink.last,
                tx_frames_in_sys.eq(tx_frames_in_sys + 1)
            )
        ]

        tx_frames_gray_sys = Signal(8)
        self.comb += tx_frames_gray_sys.eq(tx_frames_in_sys ^ (tx_frames_in_sys >> 1))

        tx_frames_gray_tx = Signal(8)
        self.specials += MultiReg(tx_frames_gray_sys, tx_frames_gray_tx, "eth_sfp1_tx")

        bin_bits = []
        for i in range(8):
            bit_val = 0
            for j in range(i, 8):
                bit_val = bit_val ^ tx_frames_gray_tx[j]
            bin_bits.append(bit_val)
        tx_frames_in_bin = Signal(8)
        self.comb += tx_frames_in_bin.eq(Cat(*bin_bits))

        tx_frames_drained = Signal(8)
        has_frame = Signal()
        self.comb += has_frame.eq(tx_frames_in_bin != tx_frames_drained)

        # 32-bit to 8-bit stream unpacking in eth_sfp1_tx domain
        w_data = Signal(32)
        w_last_byte = Signal(2)
        w_last = Signal()
        w_error = Signal()
        byte_idx = Signal(2)

        TX_S_IDLE = 0
        TX_S_SEND = 1
        tx_state = Signal(reset=TX_S_IDLE)

        tx_mac_last_reg = Signal()
        tx_mac_error_reg = Signal()

        next_last_byte = Signal(2)
        self.comb += Case(tx_fifo.dout[32:36], {
            0b0001: next_last_byte.eq(0),
            0b0011: next_last_byte.eq(1),
            0b0111: next_last_byte.eq(2),
            "default": next_last_byte.eq(3),
        })

        curr_byte = Signal(8)
        self.comb += Case(byte_idx, {
            0: curr_byte.eq(w_data[0:8]),
            1: curr_byte.eq(w_data[8:16]),
            2: curr_byte.eq(w_data[16:24]),
            3: curr_byte.eq(w_data[24:32]),
        })

        is_word_end = Signal()
        self.comb += is_word_end.eq(byte_idx == w_last_byte)

        self.comb += [
            If(tx_state == TX_S_SEND,
                self.tx_mac_valid.eq(1),
                self.tx_mac_data.eq(curr_byte),
                self.tx_mac_last.eq(tx_mac_last_reg),
                self.tx_mac_error.eq(tx_mac_error_reg),
            ).Else(
                self.tx_mac_valid.eq(0),
                self.tx_mac_data.eq(0),
                self.tx_mac_last.eq(0),
                self.tx_mac_error.eq(0),
            )
        ]

        self.sync.eth_sfp1_tx += [
            If(tx_state == TX_S_IDLE,
                If(has_frame & tx_fifo.readable,
                    tx_fifo.re.eq(1),
                    w_data.eq(tx_fifo.dout[0:32]),
                    w_last_byte.eq(next_last_byte),
                    w_last.eq(tx_fifo.dout[36]),
                    w_error.eq(tx_fifo.dout[37]),
                    tx_mac_last_reg.eq(tx_fifo.dout[36] & (next_last_byte == 0)),
                    tx_mac_error_reg.eq(tx_fifo.dout[37] & (tx_fifo.dout[36] & (next_last_byte == 0))),
                    byte_idx.eq(0),
                    tx_state.eq(TX_S_SEND),
                ).Else(
                    tx_fifo.re.eq(0),
                    tx_mac_last_reg.eq(0),
                    tx_mac_error_reg.eq(0),
                )
            ).Elif(tx_state == TX_S_SEND,
                tx_fifo.re.eq(0),
                If(self.tx_mac_ready,
                    If(is_word_end,
                        If(w_last,
                            tx_frames_drained.eq(tx_frames_drained + 1),
                            tx_mac_last_reg.eq(0),
                            tx_mac_error_reg.eq(0),
                            tx_state.eq(TX_S_IDLE),
                        ).Else(
                            If(tx_fifo.readable,
                                tx_fifo.re.eq(1),
                                w_data.eq(tx_fifo.dout[0:32]),
                                w_last_byte.eq(next_last_byte),
                                w_last.eq(tx_fifo.dout[36]),
                                w_error.eq(tx_fifo.dout[37]),
                                tx_mac_last_reg.eq(tx_fifo.dout[36] & (next_last_byte == 0)),
                                tx_mac_error_reg.eq(tx_fifo.dout[37] & (tx_fifo.dout[36] & (next_last_byte == 0))),
                                byte_idx.eq(0),
                            )
                        )
                    ).Else(
                        byte_idx.eq(byte_idx + 1),
                        tx_mac_last_reg.eq(w_last & ((byte_idx + 1) == w_last_byte)),
                        tx_mac_error_reg.eq(w_error & (w_last & ((byte_idx + 1) == w_last_byte))),
                    )
                )
            )
        ]

        # =====================================================================
        # RX PATH: 1G MAC (125 MHz) -> RX Async FIFO -> DMA (50 MHz sys)
        # =====================================================================
        rx_fifo = ClockDomainsRenamer({"write": "eth_sfp1_rx", "read": "sys"})(
            AsyncFIFO(width=38, depth=depth)
        )
        self.submodules.rx_fifo = rx_fifo

        rx_byte_cnt = Signal(2)
        rx_word_data = Signal(32)

        assembled_word = Signal(32)
        self.comb += [
            assembled_word.eq(rx_word_data),
            Case(rx_byte_cnt, {
                0: assembled_word[0:8].eq(self.rx_mac_data),
                1: assembled_word[8:16].eq(self.rx_mac_data),
                2: assembled_word[16:24].eq(self.rx_mac_data),
                3: assembled_word[24:32].eq(self.rx_mac_data),
            })
        ]

        rx_word_be = Signal(4)
        self.comb += Case(rx_byte_cnt, {
            0: rx_word_be.eq(0b0001),
            1: rx_word_be.eq(0b0011),
            2: rx_word_be.eq(0b0111),
            3: rx_word_be.eq(0b1111),
        })

        rx_push = Signal()
        self.comb += rx_push.eq(self.rx_mac_valid & ((rx_byte_cnt == 3) | self.rx_mac_last))

        self.comb += [
            rx_fifo.din.eq(Cat(assembled_word, rx_word_be, self.rx_mac_last, self.rx_mac_error)),
            rx_fifo.we.eq(rx_push & rx_fifo.writable),
        ]

        self.sync.eth_sfp1_rx += [
            If(self.rx_mac_valid,
                If(self.rx_mac_last | (rx_byte_cnt == 3),
                    rx_byte_cnt.eq(0),
                    rx_word_data.eq(0),
                ).Else(
                    rx_byte_cnt.eq(rx_byte_cnt + 1),
                    rx_word_data.eq(assembled_word),
                )
            )
        ]

        # Drain RX FIFO into DMA in sys domain (50 MHz)
        self.comb += [
            self.dma_source.valid.eq(rx_fifo.readable),
            self.dma_source.data.eq(rx_fifo.dout[0:32]),
            self.dma_source.be.eq(rx_fifo.dout[32:36]),
            self.dma_source.last.eq(rx_fifo.dout[36]),
            self.dma_source.error.eq(rx_fifo.dout[37]),
            rx_fifo.re.eq(self.dma_source.valid & self.dma_source.ready),
        ]
