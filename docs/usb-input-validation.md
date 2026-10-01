# USB keyboard/mouse input validation — 2026-10-01

Receiver: Logitech `046D:C52B`, three HID interfaces, kept connected throughout.
The first two physical input attempts produced no key or mouse events despite
successful enumeration, an advancing OHCI frame number and zero USB errors.
Enumeration alone did not prove interrupt transfers worked.

The live periodic schedule showed all three endpoint EDs sharing the same
head TD (`40835880`) and dummy tail TD (`40835890`). TinyUSB's OHCI
`gtd_find_free()` returned an unused descriptor without reserving it. A dummy
tail is owned by an ED before `gtd_init()`; subsequent endpoints could allocate
it again and overwrite its endpoint index and DMA buffer. The local fix marks
the descriptor used when allocating it, including dummy tails.

`test usb` now checks the actual periodic ED list for bounded, aligned DDR
addresses and exclusive head/tail TD ownership across endpoints. It failed on
the unpatched firmware (`build/usb-input-red/`, `USB periodic TD ownership FAIL`)
and passed after the fix. `usb stop/restart` and shared PHY reset also exercise
descriptor release and reallocation. Key, mouse and raw-report drop counters
are exposed and included in the test.

Physical input capture `build/usb-input-20261001-212331/result.json` completed
in 98.86 seconds on the patched firmware:

| Input | Recorded result |
| --- | --- |
| A | 9 presses, 9 releases |
| Enter | 2 presses, 2 releases |
| Left Shift | 2 presses, 2 releases; modifier mask `02` |
| Left Ctrl | 13 presses, 13 releases; modifier mask `01` |
| Mouse | 96 events; positive/negative X and Y |
| Buttons | Left/right press and release, masks `00`, `01`, `02` |
| Wheel | Positive and negative motion |
| Controller/display | 148 HID reports, zero USB errors, zero RGB LCD underflows |

The user performed the physical input sequence and confirmed completion.
LED output, hubs, low-speed devices and arbitrary non-Boot report decoding
remain untested. The final monitor build is `build/usb-input-fixed-final/`;
`firmware-verification.json` records a passing command regression including
three USB restarts, shared PHY reset and a short two-second peripheral soak.
This targeted regression supplements the earlier M10 five-minute run.

All patched applications in this session were loaded through UART into DDR.
Flash still contains the older M10 monitor; this fix is not yet persistent.
See [USB input demo](usb-input-demo.md) for the graphical example.
