"""The capture contract must reject no signal, channel swaps and flipped images."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from mini_ops.mega138k_ae350.ae350_video_suite import inspect_frame, ConsolePattern, timestamp_stats


def card(gain=1, offset=0):
    frame = bytearray([24] * (320 * 240 * 3))
    # Rectangle extents and channel order follow the test-card image geometry.
    for x0, x1, palettes in ((48, 144, ((63, 0, 0), (0, 63, 0), (0, 0, 63))),
                              (176, 272, ((0, 63, 63), (63, 0, 63), (63, 63, 0)))):
        for (y0, y1), rgb in zip(((32, 80), (96, 144), (160, 208)), palettes):
            pixel = bytes(max(0, min(255, round(value * gain + offset))) for value in rgb)
            for y in range(y0, y1):
                frame[(y * 320 + x0) * 3:(y * 320 + x1) * 3] = pixel * (x1 - x0)
    return bytes(frame)


class CaptureContractTest(unittest.TestCase):
    def test_correct_card_with_capture_gain(self):
        for gain, offset in ((0.5, 0), (1, 8), (2.5, -8)):
            with self.subTest(gain=gain, offset=offset):
                self.assertEqual(inspect_frame(card(gain, offset))[1], [])

    def test_black_and_gray_no_signal_are_rejected(self):
        for value in (0, 15, 16, 24, 128, 255):
            self.assertEqual(len(inspect_frame(bytes([value]) * (320 * 240 * 3))[1]), 6)

    def test_red_blue_swap_is_rejected(self):
        raw = bytearray(card())
        raw[0::3], raw[2::3] = raw[2::3], raw[0::3]
        self.assertTrue(inspect_frame(raw)[1])

    def test_vertical_flip_is_rejected(self):
        raw = card()
        flipped = b''.join(raw[y * 960:(y + 1) * 960] for y in reversed(range(240)))
        self.assertTrue(inspect_frame(flipped)[1])

    def test_horizontal_flip_is_rejected(self):
        raw = card()
        flipped = b''.join(raw[(y * 320 + x) * 3:(y * 320 + x + 1) * 3]
                           for y in range(240) for x in reversed(range(320)))
        self.assertTrue(inspect_frame(flipped)[1])

    def test_truncation_is_rejected(self):
        with self.assertRaises(ValueError):
            inspect_frame(card()[:-1])


class TimestampTest(unittest.TestCase):
    def test_regular_cadence(self):
        log='\n'.join('[Parsed_showinfo_0] n: %d pts: %d pts_time:%.8f duration:1' % (i,i,i/60) for i in range(720))
        result=timestamp_stats(log,60)
        self.assertEqual(result['estimated_missing_frames'],0)
        self.assertEqual(result['nonmonotonic'],0)

    def test_missing_frame(self):
        log='\n'.join('[Parsed_showinfo_0] n: %d pts: %d pts_time:%.8f duration:1' % (i,i,i/60) for i in range(720) if i!=400)
        self.assertEqual(timestamp_stats(log,60)['estimated_missing_frames'],1)

    def test_absent_timestamps_are_not_zero_drops(self):
        self.assertEqual(timestamp_stats('no timestamp evidence',60),dict(available=False))

    def test_integer_pts_avoid_long_run_print_rounding(self):
        log='[Parsed_showinfo_0] config in time_base: 1/60000, frame_rate:60/1\n'
        log+='\n'.join('[Parsed_showinfo_0] n: %d pts: %d pts_time:%.6g duration:1000' %
                       (i,i*1000,i/60) for i in range(107900,108100))
        result=timestamp_stats(log,60,warmup=0)
        self.assertEqual(result['timestamp_basis'],'integer PTS/time_base')
        self.assertEqual(result['estimated_missing_frames'],0)
        self.assertEqual(result['max_gap_ms'],16.667)


class ConsoleComparisonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rgb=bytearray(320*240*3)
        raw=bytearray(640*480*2)
        for y0 in (12,30,56,104,184):
            for x0 in range(10,146,7):
                for y in range(y0,y0+6):
                    for x in range(x0,x0+3):
                        if x==x0 or y in (y0,y0+5):
                            cls.rgb[(y*320+x)*3:(y*320+x)*3+3]=b'\xff'*3
                            for yy in (y*2,y*2+1):
                                for xx in (x*2,x*2+1):
                                    raw[(yy*640+xx)*2:(yy*640+xx)*2+2]=b'\xff\xff'
        cls.pattern=ConsolePattern(raw)

    def test_expected_console(self):
        self.assertEqual(self.pattern.inspect(self.rgb,full=True)[1],[])
        self.assertEqual(self.pattern.inspect(self.rgb)[1],[])

    def test_one_pixel_codec_offset(self):
        shifted=bytearray(len(self.rgb))
        for y in range(240):
            shifted[(y*320+1)*3:(y+1)*320*3]=self.rgb[y*320*3:((y+1)*320-1)*3]
        self.assertEqual(self.pattern.inspect(shifted,full=True)[1],[])
        self.assertEqual(self.pattern.inspect(shifted)[1],[])

    def test_no_signal_and_wrong_layout(self):
        for value in (0,128,255):
            self.assertTrue(self.pattern.inspect(bytes([value])*(320*240*3),full=True)[1])
        flipped=b''.join(self.rgb[(y*320+x)*3:(y*320+x+1)*3]
                         for y in range(240) for x in reversed(range(320)))
        self.assertTrue(self.pattern.inspect(flipped,full=True)[1])


if __name__ == '__main__':
    unittest.main()
