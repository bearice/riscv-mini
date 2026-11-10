"""Unit tests for Mega138K AE350 256-color palette (INDEX8) and scanout wrap-back."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from migen import Memory, Module, Signal, run_simulation
from litex.soc.interconnect import stream
from gateware.machines.mega138k_ae350.ddr3_ahb import DDR3UI
from gateware.machines.mega138k_ae350.video_memory import VideoFrameReader
from gateware.machines.mega138k_ae350.peripherals.video import VideoPaletteLookup
from gateware.machines.mega138k_ae350 import address_map as amap


class VideoPaletteLookupTest(unittest.TestCase):
    def test_palette_lookup_and_bank_switch(self):
        # Preload palette RAM: bank 0 = index * 0x11, bank 1 = (255 - index) * 0x11
        init_data = [(i * 0x11) & 0xffff for i in range(256)] + \
                    [((255 - i) * 0x11) & 0xffff for i in range(256)]
        palette = Memory(16, 512, init=init_data, name='test_palette')
        palette_read = palette.get_port(clock_domain='sys')
        m = Module()
        m.specials += palette, palette_read
        lookup = VideoPaletteLookup(palette_read)
        m.submodules.lookup = lookup

        seen = []

        def producer():
            yield lookup.indexed.eq(1)
            yield lookup.palette_bank.eq(0)

            # Bank 0: 10, 20, 30, 40
            for idx in [10, 20, 30, 40]:
                yield lookup.sink.valid.eq(1)
                yield lookup.sink.data.eq(idx)
                yield lookup.sink.index.eq(idx)
                yield lookup.sink.epoch.eq(1)
                yield
                while not (yield lookup.sink.ready):
                    yield
            yield lookup.sink.valid.eq(0)

            # Wait for consumer to finish bank 0
            while len(seen) < 4:
                yield

            # Bank 1: 10, 20
            yield lookup.palette_bank.eq(1)
            for idx in [10, 20]:
                yield lookup.sink.valid.eq(1)
                yield lookup.sink.data.eq(idx)
                yield lookup.sink.index.eq(idx)
                yield lookup.sink.epoch.eq(2)
                yield
                while not (yield lookup.sink.ready):
                    yield
            yield lookup.sink.valid.eq(0)

            while len(seen) < 6:
                yield

            # Bypass mode (indexed=0)
            yield lookup.indexed.eq(0)
            yield lookup.sink.valid.eq(1)
            yield lookup.sink.data.eq(0xdeadbeef)
            yield lookup.sink.index.eq(999)
            yield lookup.sink.epoch.eq(3)
            yield
            while not (yield lookup.source.valid):
                yield
            self.assertEqual((yield lookup.source.valid), 1)
            self.assertEqual((yield lookup.source.data), 0xdeadbeef)

        def consumer():
            yield lookup.source.ready.eq(1)
            while len(seen) < 6:
                yield
                if (yield lookup.source.valid):
                    bank = 0 if len(seen) < 4 else 1
                    seen.append((bank, (yield lookup.source.index), (yield lookup.source.data) & 0xffff))

        run_simulation(m, [producer(), consumer()])
        # Check bank 0 lookups
        self.assertEqual(seen[0], (0, 10, 10 * 0x11))
        self.assertEqual(seen[1], (0, 20, 20 * 0x11))
        self.assertEqual(seen[2], (0, 30, 30 * 0x11))
        self.assertEqual(seen[3], (0, 40, 40 * 0x11))
        # Check bank 1 lookups
        self.assertEqual(seen[4], (1, 10, (255 - 10) * 0x11))
        self.assertEqual(seen[5], (1, 20, (255 - 20) * 0x11))

    def test_backpressure_retains_color(self):
        palette = Memory(16, 512, init=[0x1234] * 512, name='test_palette_bp')
        palette_read = palette.get_port(clock_domain='sys')
        m = Module()
        m.specials += palette, palette_read
        lookup = VideoPaletteLookup(palette_read)
        m.submodules.lookup = lookup

        def stimulus():
            yield lookup.indexed.eq(1)
            yield lookup.palette_bank.eq(0)
            yield lookup.source.ready.eq(0)  # Downstream stalled

            # Send 1 pixel
            yield lookup.sink.valid.eq(1)
            yield lookup.sink.data.eq(0)
            yield lookup.sink.index.eq(42)
            yield lookup.sink.epoch.eq(1)
            yield
            yield lookup.sink.valid.eq(0)
            yield

            # Output should now be valid, holding color 0x1234 even with source.ready=0
            self.assertEqual((yield lookup.source.valid), 1)
            self.assertEqual((yield lookup.source.data) & 0xffff, 0x1234)
            self.assertEqual((yield lookup.source.index), 42)

            # Hold for 5 cycles, color must not be lost
            for _ in range(5):
                yield
                self.assertEqual((yield lookup.source.valid), 1)
                self.assertEqual((yield lookup.source.data) & 0xffff, 0x1234)

            # Now release stall
            yield lookup.source.ready.eq(1)
            yield
            # Consumed
            yield lookup.source.ready.eq(0)
            yield
            self.assertEqual((yield lookup.source.valid), 0)

        run_simulation(m, stimulus())


class VideoFrameReaderWrapTest(unittest.TestCase):
    def test_wrap_back_row_progression(self):
        ui = DDR3UI()
        # 32 bytes per block -> width=32 means 2 blocks per row (stride=64, row_bytes=64)
        # height=4 rows total
        reader = VideoFrameReader(ui, width=32, height=4, min_base=0x100000,
                                  ram_bytes=1 << 24, runtime=False)
        m = Module()
        m.submodules.reader = reader

        # Ring buffer spans 3 rows: 0x100000 to 0x1000c0 (3 * 64 bytes)
        # Scanout starts at row 2 (0x100080) for 4 rows:
        # Row 0: 0x100080, 0x1000a0
        # Row 1 (wraps to start): 0x100000, 0x100020
        # Row 2: 0x100040, 0x100060
        # Row 3: 0x100080, 0x1000a0
        commands = []

        def stimulus():
            yield reader.request.valid.eq(1)
            yield reader.request.base.eq(0x100080)
            yield reader.request.stride.eq(64)
            yield reader.request.epoch.eq(1)
            yield reader.request.run.eq(1)
            yield reader.request.wrap_start.eq(0x100000)
            yield reader.request.wrap_end.eq(0x1000c0)
            yield reader.source.ready.eq(1)
            yield ui.cmd_ready.eq(1)

            yield
            while not (yield reader.request.ready):
                yield
            yield reader.request.valid.eq(0)

            # Run reader through 4 rows * 2 blocks = 8 blocks
            blocks_read = 0
            for _ in range(200):
                if (yield ui.cmd_en) and (yield ui.cmd_ready):
                    addr_words = (yield ui.addr)
                    byte_addr = addr_words << 2
                    commands.append(byte_addr)
                    # Complete read next cycle
                    yield
                    yield ui.rd_data_valid.eq(1)
                    yield ui.rd_data.eq(0xbeef)
                    yield
                    yield ui.rd_data_valid.eq(0)
                    blocks_read += 1
                    if blocks_read == 8:
                        break
                yield

        run_simulation(m, stimulus())
        expected = [
            0x100080, 0x1000a0,  # Row 0
            0x100000, 0x100020,  # Row 1 (wrapped!)
            0x100040, 0x100060,  # Row 2
            0x100080, 0x1000a0,  # Row 3
        ]
        self.assertEqual(commands, expected)

    def test_invalid_wrap_triggers_address_error(self):
        ui = DDR3UI()
        reader = VideoFrameReader(ui, width=32, height=2, min_base=0x100000,
                                  ram_bytes=1 << 24, runtime=False)
        m = Module()
        m.submodules.reader = reader

        def stimulus():
            # Test 1: base < wrap_start
            yield reader.request.valid.eq(1)
            yield reader.request.base.eq(0x100000)
            yield reader.request.stride.eq(64)
            yield reader.request.epoch.eq(1)
            yield reader.request.run.eq(1)
            yield reader.request.wrap_start.eq(0x100080)
            yield reader.request.wrap_end.eq(0x100100)
            yield
            while not (yield reader.request.ready):
                yield
            yield reader.request.valid.eq(0)

            for _ in range(10):
                yield
            self.assertEqual((yield reader.address_error), 1)

        run_simulation(m, stimulus())

    def test_row_crossing_wrap_boundary_rejected(self):
        ui = DDR3UI()
        # width=32 -> 2 blocks per row = 64 row_bytes
        reader = VideoFrameReader(ui, width=32, height=2, min_base=0x100000,
                                  ram_bytes=1 << 24, runtime=False)
        m = Module()
        m.submodules.reader = reader

        def stimulus():
            # Test: base=0x1000a0 with [0x100000, 0x1000c0).
            # base < wrap_end (0x1000a0 < 0x1000c0), but row_end = 0x1000e0 > wrap_end!
            # Must be rejected with address_error.
            yield reader.request.valid.eq(1)
            yield reader.request.base.eq(0x1000a0)
            yield reader.request.stride.eq(64)
            yield reader.request.epoch.eq(1)
            yield reader.request.run.eq(1)
            yield reader.request.wrap_start.eq(0x100000)
            yield reader.request.wrap_end.eq(0x1000c0)
            yield
            while not (yield reader.request.ready):
                yield
            yield reader.request.valid.eq(0)

            for _ in range(10):
                yield
            self.assertEqual((yield reader.address_error), 1)

        run_simulation(m, stimulus())

    def test_wrap_span_from_latched_descriptor(self):
        ui = DDR3UI()
        reader = VideoFrameReader(ui, width=32, height=4, min_base=0x100000,
                                  ram_bytes=1 << 24, runtime=False)
        m = Module()
        m.submodules.reader = reader
        commands = []

        def stimulus():
            # Accept first descriptor: [0x100000, 0x1000c0), base=0x100080
            yield reader.request.valid.eq(1)
            yield reader.request.base.eq(0x100080)
            yield reader.request.stride.eq(64)
            yield reader.request.epoch.eq(1)
            yield reader.request.run.eq(1)
            yield reader.request.wrap_start.eq(0x100000)
            yield reader.request.wrap_end.eq(0x1000c0)
            yield reader.source.ready.eq(1)
            yield ui.cmd_ready.eq(1)

            yield
            while not (yield reader.request.ready):
                yield
            # Immediately change request endpoint to simulate a next/bogus descriptor
            # during EXTENT/BOUND cycles.
            yield reader.request.valid.eq(0)
            yield reader.request.wrap_start.eq(0x200000)
            yield reader.request.wrap_end.eq(0x300000)

            blocks_read = 0
            for _ in range(200):
                if (yield ui.cmd_en) and (yield ui.cmd_ready):
                    addr_words = (yield ui.addr)
                    byte_addr = addr_words << 2
                    commands.append(byte_addr)
                    yield
                    yield ui.rd_data_valid.eq(1)
                    yield ui.rd_data.eq(0xbeef)
                    yield
                    yield ui.rd_data_valid.eq(0)
                    blocks_read += 1
                    if blocks_read == 8:
                        break
                yield

        run_simulation(m, stimulus())
        # The reader must have calculated wrap_span from the accepted descriptor (192 bytes),
        # not the changed request.wrap_end / wrap_start!
        expected = [
            0x100080, 0x1000a0,  # Row 0
            0x100000, 0x100020,  # Row 1 (wrapped by 192 bytes back to 0x100000)
            0x100040, 0x100060,  # Row 2
            0x100080, 0x1000a0,  # Row 3
        ]
        self.assertEqual(commands, expected)


class PaletteMemoryTest(unittest.TestCase):
    def test_palette_ram_write_and_readback(self):
        palette = Memory(16, 512, init=[0]*512, name='video_palette')
        palette_write = palette.get_port(write_capable=True, has_re=True, clock_domain='sys')
        palette_read = palette.get_port(clock_domain='sys')
        m = Module()
        m.specials += palette, palette_write, palette_read

        def stimulus():
            # Write 0x5678 to address 10
            yield palette_write.adr.eq(10)
            yield palette_write.dat_w.eq(0x5678)
            yield palette_write.we.eq(1)
            yield palette_write.re.eq(0)
            yield
            yield palette_write.we.eq(0)
            yield palette_write.re.eq(1)
            yield
            yield
            self.assertEqual((yield palette_write.dat_r), 0x5678)

            # Read from read port (which is in video_pix domain on hardware)
            yield palette_read.adr.eq(10)
            yield
            yield
            self.assertEqual((yield palette_read.dat_r), 0x5678)

        run_simulation(m, stimulus())


if __name__ == '__main__':
    unittest.main()
