# feat(doom): UART debug channel, deterministic zone, 8x faster blit

生成：2026-10-11T14:48:35+00:00；模式：staged review。

## 改动

- UART debug channel (dg_debug.c/h, scripts/doom_debug.py): pause/step, dumps of the drawn 320x200 area, of both 480x272 slots, of the 8-bit index buffer and colors[], plus per-segment timings, a file-IO report with a running read CRC, game state and test patterns. Commands are served from the shim poll hook so they answer during the minutes-long startup; frame dumps are queued to the next frame boundary so a dump never races the draw.
- Determinism fix: I_ZoneBase() zeroes the zone. Before this, -O0 and -O2 rendered 45726/64000 bytes (71.45%) differently at a bit-identical camera state (gametic 172, leveltime 1, E1M5 demo), i.e. the renderer read memory it never wrote. With the zone zeroed every compared leveltime is bit-identical. The board places the zone in BSS which the BIOS already zeroes, so this changes QEMU and any future non-zeroed allocator; cost is one 16 MiB memset, about 1.8 s of a 140 s startup.
- Blit: cmap_to_fb uses a precomputed rgb565_palette and packs two pixels per 32-bit word when fb_scaling is 1 and the target is 16bpp. 233 ms/frame -> 28 ms on hardware (69% -> 8% of a 288 ms frame). DebugChannelTest re-runs the blit on the host from the dumped index buffer and the dumped colors[] and requires 0/200 rows differ.
- Vertical centring: I_FinishUpdate computed y_offset but never applied it, so the picture sat against the top with a 72-line black band below while left and right were centred; it now pads 36 rows above.
- Per-frame blank of I_VideoBuffer, black by default and c cycles black / 0xff evidence / off. Measured unwritten pixels: 100% at level tic 0, 523 px (1.0%) at tic 1, zero from tic 34. At 35 fps one tic of stale pixels is invisible; at this port 3.4 fps it stays on the panel for a third of a second.
- exit() returns to the BIOS console: shim_setjmp is an asm entry that captures the callers frame instead of a C wrapper that only tail-called at -Os; at -O2 the wrapper kept its own frame and longjmp returned into a stack the game had reused. BIOS_POLL is throttled to 20 ms (2904 polls in 41 s at 0.68 ms each).
- Build plumbing: bios_payload.py, doom_image.py and doom_sim.py gained --optimize (payload default -O2); doom_sim.py gained --define, used by SIM_NOKEYS to watch the attract loop without injected keys.
- docs: the WAD must be 1.9. The 1.8 shareware WAD attract demos desync and draw full-height flat bands that look exactly like a renderer bug; both hashes and the Debian non-free recipe are recorded, and the read-CRC differential (board == QEMU at six checkpoints) is documented as the way to exonerate the SD block cache and file layer.
- Measured on hardware: 3.37 fps (tick 288 ms, blit 28, draw 9, present_fails=0). The reported flicker is LCD DMA underruns (0 idle, 137 over 9751 panel frames during a run); the fix is gateware DDR arbitration or FIFO depth, not this payload.

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
