# fix(doom): keep the debug console answering while the game clock is held

生成：2026-10-12T08:14:57+00:00；模式：historical commit documentation。

## 改动

- Debug console starvation fix: DG_SleepMs now services the debug channel through dbg_service_held() while the clock is held. A pause taken inside a real-time wait never reaches another frame boundary - the screen wipe in d_main.c spins in I_Sleep() on I_GetTime(), and the pause freezes exactly that clock - so a frame dump queued for the next boundary starved forever. On hardware --command frame returned nothing at all, not even DBG done, while unqueued reports still answered and the game ran on at 3.59 fps.
- dbg_service_held() declares the in-progress frame final before running the boundary service: the engine cannot finish it because the clock it waits on is frozen, and queued f/F/g still drain inside one service call, so they keep describing the same buffer. Marking the frame ready in the pause handler instead was tried and rejected - f then ran mid-frame while g ran at a later point, and DebugChannelTest failed with 168/200 rows (59.74%).
- Timing report window: g_window_start was initialised to 0 while dbg_now_ms() returns board uptime, so the first t report divided the payload frames by hours of power-on time and printed fps=0.01 for a game at 3.6; the window now opens when DG_Init first reports the panel geometry.
- docs: --compare reads whatever dumps are currently in --out-dir and every capture overwrites them, so it must follow frame+source with no capture in between; a stale pairing reported a bogus 81.65% diff.
- docs: payload-porting pitfall 16 - the board falls back to the Flash XIP BIOS after any reset, so a payload can meet a BIOS without file services and report a misleading IWAD not found. Unknown a6 returns -1 exactly like a missing file, and bios_info.features has no FILESYSTEM bit; pin the state with board run --image before testing.

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
