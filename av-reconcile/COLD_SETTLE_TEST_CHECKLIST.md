# Cold-settle fix — deploy & test checklist (Changes A/B/C, 2026-09-09)

Handoff: `CLAUDE_CODE_HANDOFF_nlziet_cold_settle.md`. Changes:
- **A** — signal-gated cold-start EQ write (integration exposes `audio_source`; engine waits for a real stream).
- **B** — discrete network pre-position (`turn_on` if the Zigbee plug reads standby, then select ARC).
- **C** — leave the bar on ARC at teardown (`script.power_off`, already live).

---

## 1. Deploy

- [ ] **Integration → HACS.** Update **LG Soundbar Plus** to **v0.1.10** in HACS, then restart HA (or reload the `lg_soundbar_plus` integration).
- [ ] **Pyscript (manual).** Copy `av-reconcile/pyscript/av_reconcile.py` → `<config>/pyscript/av_reconcile.py`, then run `pyscript.reload`.
- [ ] **power_off** — already updated live via MCP; nothing to deploy.

## 2. Verify the new plumbing (warm, no cold boot needed)

- [ ] `media_player.lg_soundbar` now has attributes `audio_source`, `power_status`, `debug_all_fields` (Developer Tools → States).
- [ ] With TV on + eARC active, `audio_source` shows a real value (expected `PCM` or `DOLBY AUDIO`), **not** `NO SIGNAL`.
- [ ] A **warm** activity switch to a non-AI-Sound source (e.g. NLZiet) still settles fast — no added delay. Log line: `av_reconcile[...]: soundbar sound_mode=... confirmed (signal-gated)`.
- [ ] `pyscript.av_tv_reconcile` logs show `preposition -> TV soundOutput=external_arc` at the start of a switch (Change B).

## 3. Confirm the audio field, then let me strip debug

- [ ] Do **one cold boot** into NLZiet (or any non-AI-Sound source).
- [ ] Tell me it's done — I'll read `media_player.lg_soundbar` → `debug_all_fields` **live** and confirm the exact incoming-stream key (expected `s_audio_source`) + its `NO SIGNAL`/`PCM`/`DOLBY` values.
- [ ] If the real key name differs, I remap the gate (currently keyed on `audio_source`, fed by `find_any('s_audio_source')`).
- [ ] Once confirmed, I ship **v0.1.11** removing `debug_all_fields`.

## 4. Regression matrix (temescal logger + HA traces; segment vs `activity_*`/`power_off`)

- [ ] **Warm-cold** (bar last on ARC, short standby): `sound_mode` ends **Standard**, fast. *(This is the bug the fix targets — pre-fix this landed on AI Sound Pro.)*
- [ ] **Deep-cold, last on ARC** (Change C in effect): fast eARC route, correct sound_mode.
- [ ] **Deep-cold, forced Wi-Fi** (repro the poisoned case): Change B routes it — no internal-speaker fallback, no ~45 s ceiling wait. Watch for `preposition -> soundbar source ARC (was Wi-Fi)`.
- [ ] **Bluetooth headphones on**: soundbar path still skipped entirely (no pre-position, no eARC force). Log: `BT headphones active -> ... soundbar left alone`.

## 5. Watch for (log lines / notifications)

- [ ] `audio still NO SIGNAL after wait -- writing eq ... anyway` → stream never arrived in the window; raise `input_number.av_audio_signal_wait_seconds` (default 20 s) if it recurs.
- [ ] `audio_source attribute not exposed ... proceeding on timer` → integration not deployed/loaded (should NOT appear after step 1).
- [ ] `AV: cold-boot soundbar set failed` persistent notification → h7 recovery failed; raise the cold-boot ceiling slider.
- [ ] Any `H7 Soundbar Preset (native) -- ...mismatch` notifications during the runs.

## 6. Open questions to resolve during testing

- [ ] **O1** — does discrete `turn_on` wake the H7 from *deep* standby? (Owner note 2026-09-09: the control socket seems generally open, so it likely does. Confirm on the deep-cold-forced-Wi-Fi run: does the bar power up from `preposition -> soundbar turn_on` before the reachability wait?)
- [ ] **O4** — does volume need drift coverage too? (Stuck in 2/3 pre-fix runs; confirm it still sticks post-fix.)

## Rollback
- Integration: HACS → downgrade to v0.1.9.
- Pyscript: restore the previous `av_reconcile.py` and `pyscript.reload`.
- `power_off`: remove the prepended "Leave soundbar on ARC" step (first sequence item).
