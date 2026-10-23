# AE350 TMDS output

`ae350_hdmi_phy.v` is the local physical adapter: pixel reset, phase-shifted
serializer reset release, three OSER10s and differential output buffers.
`svo_tmds.v` retains the permissively licensed SVO TMDS encoder and its original
copyright/license. No sibling example project is opened by the build.

Raster timing, built-in panels, tagged RGB565 pixels, coherent configuration,
FIFO crossing and DDR arbitration are owned by the AE350 machine under
`gateware/machines/mega138k_ae350/`. The BIOS TTY renderer uses the AE350 video
BSP and the shared BIOS VT state machine.

Serializer reset asserts asynchronously and releases from a 25MHz PLL output
shifted by 8ns. Recovery/removal to both PCLK and FCLK remain in STA. Each lane has a preserved release flop near its serializer. Ordinary
fabric reset flops do not run on the fast serializer clock.
