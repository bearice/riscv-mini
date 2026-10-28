# AE350 TMDS output

`tmds.py` implements transition coding, balanced bit counts, parallel parity,
running disparity and the three-register symbol pipeline in Migen. It retains
the original SVO copyright and permissive license. There are no local Verilog
encoder or PHY sources in the build.

`phy.py` implements the physical adapter in Migen. It registers RGB and timing
signals together, releases the pixel and serializer resets, and instantiates
Gowin `OSER10` and `ELVDS_OBUF` primitives. Explicit `DFFCE` primitives preserve
the exact asynchronous assertion and four-clock release behavior; replacing
these with a domain reset synchronizer would add release cycles. No fabric
reset flops run on the fast serializer clock.

Serializer reset releases from a pixel-rate PLL output shifted by one quarter
VCO period. The three preserved lane release FFs have explicit instance names
`hdmi_lane_resetn_ff{0,1,2}_0`, bound to the machine's `hdmi_reset_sites`.
Every runtime clock configuration retains recovery/removal STA checks.
`VIDEO_ENABLE=0` holds the PHY in reset; the forwarded pixel clock remains.

Raster timing, runtime modes, RGB565/RGB888/XRGB8888 unpacking, coherent
configuration, FIFO crossing, DDR arbitration and HPD/interrupt control belong
to the AE350 machine under `gateware/machines/mega138k_ae350/`. The BIOS TTY
renderer uses RGB565 and the shared BIOS VT state machine.

`tests/mega138k_ae350/tmds_encoder_verilog_test.py` emits the production Migen
encoder and compares complete symbols and latency with an independent integer
reference. `hdmi_reset_verilog_test.py` emits the production Migen PHY, models
the IO/FF primitives, and checks asynchronous reset, phase-safe release,
stopped-clock recovery and aligned RGB/DE/HS/VS streams. These tests do not
retain a second hand-written HDL implementation of the encoder or PHY.

The Icarus PLL test snapshots the vendor model's static phase initialization
from its parameters: Icarus does not run a parameter-only `always @(*)` block.
Frequency, dynamic-divider, lock and phase propagation logic remain the vendor
model. FPGA recovery/removal validation always uses Gowin STA.
