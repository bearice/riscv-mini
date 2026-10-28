"""AE350 runtime video modes. IDs and byte layouts are part of the video ABI.

RGB888 is packed B,G,R; XRGB8888 is B,G,R,X in increasing byte addresses.
The wire always carries eight bits per component (DVI compatible TMDS).
"""
from dataclasses import dataclass
from fractions import Fraction

RGB565, RGB888, XRGB8888 = range(3)
BYTES_PER_PIXEL = (2, 3, 4)


@dataclass(frozen=True)
class VideoMode:
    name: str
    width: int
    height: int
    h_front: int
    h_sync: int
    h_back: int
    v_front: int
    v_sync: int
    v_back: int
    pixel_hz: int
    h_positive: bool = False
    v_positive: bool = False

    @property
    def h_total(self):
        return self.width + self.h_front + self.h_sync + self.h_back

    @property
    def v_total(self):
        return self.height + self.v_front + self.v_sync + self.v_back

    @property
    def refresh_hz(self):
        return Fraction(self.pixel_hz, self.h_total * self.v_total)


# Progressive timings only. 1080p60 requires a separate high-speed PHY
# qualification; it must not be advertised by this 74.25MHz scanout profile.
MODES = (
    VideoMode('640x480@60', 640,480,16,96,48,10,2,33,25175000),
    VideoMode('800x600@60', 800,600,40,128,88,1,4,23,40000000,True,True),
    VideoMode('1024x768@60',1024,768,24,136,160,3,6,29,65000000),
    VideoMode('1280x720@50',1280,720,440,40,220,5,5,20,74250000,True,True),
    VideoMode('1280x720@60',1280,720,110,40,220,5,5,20,74250000,True,True),
    VideoMode('1920x1080@24',1920,1080,638,44,148,4,5,36,74250000,True,True),
    VideoMode('1920x1080@25',1920,1080,528,44,148,4,5,36,74250000,True,True),
    VideoMode('1920x1080@30',1920,1080,88,44,148,4,5,36,74250000,True,True),
)


def pll_config(pixel_hz, input_hz=50000000):
    """Integer dividers preserve the exact serializer 5:1 relationship.

    MDIV has eighth-step fractional control on the GW5A PLL. The actual
    clock is exported rather than presenting the nominal rate as measured.
    """
    candidates = []
    for idiv in (1, 2):
        for serial_div in range(2, 26):
            pix_div = serial_div * 5
            m8 = round(Fraction(pixel_hz * pix_div * idiv * 8, input_hz))
            vco = Fraction(input_hz * m8, idiv * 8)
            if 16 <= m8 <= 1023 and 650000000 <= vco <= 1300000000:
                actual = vco / pix_div
                candidates.append((abs(actual-pixel_hz), -vco, idiv, m8, pix_div, serial_div, actual))
    _, _, idiv, m8, pix_div, serial_div, actual = min(candidates)
    if abs(actual-pixel_hz) / pixel_hz > Fraction(1, 200):
        raise ValueError('video PLL error exceeds 0.5%')
    return dict(idiv=idiv, mdiv=m8//8, fraction=m8%8,
                pixel_div=pix_div, serial_div=serial_div, actual_hz=round(actual))
