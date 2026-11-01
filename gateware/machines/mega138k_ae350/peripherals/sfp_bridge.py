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
