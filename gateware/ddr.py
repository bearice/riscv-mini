"""128 MiB H5TQ1G63EFR geometry and conservative bring-up timings.

Geometry: local SK hynix datasheet pp. 4/9. Photo confirms -PBC:
DDR3-1600, normal consumption, commercial temperature grade. Retain
use 15 ns RP/RCD, 37.5 ns RAS and 50 ns FAW. RFC=160 ns is deliberately
longer than the 1 Gbit requirement. Refresh every 3.9 us covers hot refresh.
96 MHz CK uses optional DLL-off operation; verify on the actual device.
"""
from litedram.modules import DDR3Module, _TechnologyTimings, _SpeedgradeTimings


class H5TQ1G63EFR(DDR3Module):
    nbanks, nrows, ncols = 8, 8192, 1024
    technology_timings = _TechnologyTimings(
        tREFI=32e6/8192, tWTR=(4, 7.5), tCCD=(4, None),
        tRRD=(6, 10), tZQCS=(64, 80))
    speedgrade_timings = {'default': _SpeedgradeTimings(
        tRP=15, tRCD=15, tWR=15, tRFC=(None, 160),
        tFAW=(None, 50), tRAS=37.5)}
