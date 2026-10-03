# RGB LCD keyboard/mouse demo

Source: [`firmware/examples/usb_input_demo.c`](../firmware/examples/usb_input_demo.c).
This standalone DDR application uses the existing RGB LCD scanner and USB HAL.
It uses the selected USB HAL backend; the default is the lightweight PIO host.
No gateware or bootloader change is needed; the application ABI must match the
FPGA build currently running on the board (ABI is generated per build; current full is `552653d0`).

The 480×272 RGB565 screen contains a 36-column, eight-row text area, a mouse
arrow and three lines of input/device status. Typing wraps at the right edge;
reaching the last row scrolls the text. The underline marks the insertion cell.

| Action | Effect |
| --- | --- |
| Mouse movement | Relative motion moves the cursor; the tip is clamped to screen bounds |
| Left / right / middle held | Cursor fill becomes red / green / blue; released fill is black |
| Wheel | Accumulated signed wheel motion appears in the status bar |
| Keyboard | US ASCII letters, digits and punctuation; Shift and Caps Lock affect case |
| Enter / Backspace | New line / erase preceding cell |
| Escape | Clear text and return the insertion cell to the top left |
| Ctrl / Alt / GUI combinations | Show usage/modifiers without inserting text |

This is a small input/display demo, without auto-repeat, an IME, arbitrary HID
report decoding or a window system. Keyboard LED state is not changed. Caps Lock
is maintained locally and shown on screen. The latest key usage/press state,
modifier bits, press/release counts, mouse position/buttons/wheel, USB errors
and drops, and LCD underflows are visible.

Each framebuffer retains its own cursor underlay and last-painted text/status
cells. Rendering restores the old cursor on the inactive buffer, repaints changed
cells, saves/draws the new cursor, then calls `hal_video_present()` to wait for
the frame boundary before reusing the other buffer. The cursor is clipped at
the right and bottom edges. There is no full-frame copy on mouse movement.
USB queues are drained in the main loop; event bursts are combined for display.
Rendering has a 16 ms minimum gap after a completed presentation. Panel scan
remains approximately 59.94 Hz at 9 MHz regardless of input/render rate.

Build and load using PowerShell from `riscv-mini`:

```powershell
& ./.venv/Scripts/python.exe scripts/build.py `
    --app firmware/examples/usb_input_demo.c --output-dir build/usb-input-demo

# Select an existing successfully routed FPGA build with the matching ABI.
# The board must already be running that configuration.
& ./.venv/Scripts/python.exe scripts/boot_upload.py --reset --mode uart `
    --output-dir <that-fpga-build-dir> --image build/usb-input-demo/firmware/app.img
```

The boot client checks the existing FPGA build's PnR evidence, bitstream hash
and application ABI, then transfers this application to DDR. It does not write
Flash. UART commands at 115200 8N1:

```text
status
test demo
reboot
```

`status` prints input counters, cursor position, text rows and scanner status.
`test demo` checks USB connection/HID presence, zero errors/drops, cursor bounds,
frame-swap failures, LCD scan progress/underflows, UART/IRQ errors and US-layout
case/modifier mappings. Human input and screen appearance still need observation.
Serial `!` or `reboot` returns to the loader; without menu input, the previous

软件复位后恢复 Flash 中的基础 monitor；demo 为独立 DDR 应用，运行时需要不断轮询 USB 并提交帧。
