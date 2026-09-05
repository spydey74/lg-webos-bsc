# AV Control — Architecture & Handover

Single source of truth for the LG TV + LG Soundbar activity‑audio system. Written
to let a fresh session pick up with minimal context. Last updated **2026‑08‑28**.

> Scope note: the Home Assistant side (helpers, scripts, automations, dashboard)
> is created/edited **via MCP**, not stored in this repo. This doc + the git
> history are the record of those shapes. The **pyscript engine** and this doc
> *are* in the repo.

---

## 1. The core model: two audio paths, one switch

Every Activity (Sofabaton X2 → HA `script.activity_<name>`) has two branches,
selected by a `choose` at the top of the script:

| Branch | When | Who controls audio |
|---|---|---|
| **Network** (`choose[0]`) | `input_boolean.av_network_mode_master` **and** `input_boolean.av_network_<activity>` both **on** | **The TV** (bscpylgtv integration), via the pyscript engine |
| **IR / legacy** (`default`) | either toggle off | **The soundbar** (`script.h7_soundbar_preset_native`) over IR/Sofabaton |

The IR branch is the original, proven body, preserved **verbatim** — the deeper
kill‑switch `input_boolean.lg_tv_use_ir` still lives inside `script.lg_tv_ready`.
Nothing in the network work removes or edits the IR path.

**Why TV‑primary in network mode:** on eARC the TV is the driver and the soundbar
is the leaf for source, the four mapping sound modes, and volume. The TV
integration is hooked into TV startup where the soundbar trails behind. So when
network mode is on, the TV owns everything it can and the soundbar just follows;
the drift‑watch cleans up the TV's own per‑input memory drift.

---

## 2. Components

### 2a. Integration — `lg_webos_bsc` (repo: private `spydey74/lg-webos-bsc`)
HACS custom integration for the webOS 26 TV (bscpylgtv + aiowebostv's canonical
manifest). Provides media_player, Game Optimizer/picture selects, picture number
sliders, audio/model sensors, buttons, raw services (`launch_app`, `set_settings`,
`set_input`, `set_sound_output`, `command`, `luna`), remote, notify, SSDP.
Full details: `webos26-fresh-key-full-perms` memory + repo README.

Key entities:
- `media_player.lg_webos_tv_oled83g67lw` — volume/mute/source; **`volume_level`
  mirrors the actual eARC volume live** (subscribed scalar, <0.2 s).
- `sensor.lg_webos_tv_oled83g67lw_audio_settings` — attr `soundOutput`
  (`external_arc` healthy), `soundMode`, etc.

Quirks: WOL does **not** work from HA here (environmental) → power stays
IR/Sofabaton. Some sound keys are write‑only (500 on read). `soundMode` **is**
writable on eARC and drives the soundbar in one shot.

**Ours to maintain:** we (Claude Code) authored this integration and `lg_soundbar_plus`
— connection / reconnect / state bugs are fixed *here*, not just worked around in the
engine (see the `we-own-lg-custom-integrations` memory + §9). **Connection hardening
(2026‑09‑04):** `coordinator.async_command` now reconnects + retries once on a
mid‑command socket drop (`ConnectionClosed`/timeout), then raises a clean
`HomeAssistantError` — webOS flaps the socket under load / on a cold‑boot handshake, and a
raw `websockets.ConnectionClosedOK` used to escape to callers (it crashed the AV engine +
reset script). The poll path already reconnects each cycle when `is_connected()` is False.

### 2b. Engine — `pyscript.av_tv_reconcile(activity)`  (`pyscript/av_reconcile.py`)
The **sole network‑mode audio controller**. Deploy: copy to `/config/pyscript/`
+ `pyscript.reload`. Flow per call:
1. Switch TV source (`launch_app` HDMI pseudo‑apps / `select_source` app titles — see `PROFILES`). **Retried** (`SOURCE_SWITCH_RETRIES` 4 × `SOURCE_SWITCH_RETRY_DELAY` 2 s) and **guarded**: on a cold boot the webOS websocket can still be (re)connecting when `ensure_tv_on` returns, so the call can raise `ConnectionClosedOK` — retry through the reconnect, and never let a source‑switch failure abort the audio reconcile. (All TV‑websocket calls below — soundMode, set_sound_output, volume — are likewise guarded.)
2. **Headphones guard** (`binary_sensor.tv_bluetooth_headphones` on): set TV volume to `input_number.av_bluetooth_headphone_volume`, leave soundbar alone, stop.
3. Read desired eq (`input_select.av_sound_mode_<activity>`) + upmix (`input_boolean.av_ai_upmix_<activity>`).
4. **Stamp** the drift‑watch desired state (calls `script.av_stamp_desired_audio`).
4b. **PATH SELECT (`_wait_soundbar_reachable` → robust vs TV‑primary).** Wait for the
   soundbar to be reachable (`on` + reports a `source`) — warm switch clears on the first
   (cached) poll; cold boot **forces a fresh read (`_refresh_soundbar` → `homeassistant.update_entity`)
   every `SOUNDBAR_REFRESH_POLL` (2 s)** until it reports in, bounded by
   `input_number.av_cold_boot_soundbar_ceiling_seconds` (default 45 s, dashboard‑tunable).
   The forced read helps ONLY since `lg_soundbar_plus` 0.1.9: the real cold‑boot detection
   lag was **the soundbar client's reconnect backoff** (was capped at 30 s), not the 30 s
   `scan_interval` as first assumed. When the bar is off the backoff climbs to its cap, so the
   client sat in a `sleep` and wouldn't reconnect until it expired — and `update_entity` did
   **nothing**, because it calls `request_all()` on a dead socket (which just fails); only the
   sleeping background thread reconnects. 0.1.9 caps the backoff at 5 s and makes a forced
   refresh `poke()` an immediate reconnect, so `update_entity` finally drives detection. With
   that, the bar is seen within ~a connect RTT of powering on, and when it came up clean on ARC, take
   the fast TV‑primary path with **no h7 fallback**. Then decide from the soundbar's **own `source`** (NOT the
   TV `soundOutput` sensor, which goes stale across a cold boot — that staleness is what
   defeated the earlier eARC gate). **If the soundbar isn't on `ARC`** (it woke on Bluetooth
   in its stale mode, or never came up) → **`needs_robust`: hand the initial set to
   `_call_h7`** — `script.h7_soundbar_preset_native(source=arc, eq, upmix, volume, tv_was_cold=True)`,
   which forces the input to ARC and sets everything with network verify→retry→**IR fallback**
   + a cold settle recheck (IR works whenever the bar has power, unlike the TV/eARC channel).
   h7 failure → `av_cold_boot_<activity>` notification. **If it's already on `ARC`** → the
   engine's own TV‑primary writes (steps 5–6) own it.
5. **Sound mode:** TV `set_settings(sound,{soundMode})` for the 4 mapping eqs; soundbar `select_sound_mode` for Clear Voice (no TV equivalent). Runs on **both** paths — on the robust path it reinforces (durably, TV‑root) the eq h7 just set.
6. **AI upmix + Clear Voice — TV‑primary path only** (`if not needs_robust`; on the robust path h7 already set eq+upmix on the soundbar with its own verify/retry/IR). Upmix via soundbar `switch.living_room_lg_soundbar_ai_upmix` (skipped when eq = ai_sound). **AI Sound Pro takes the switch entity `unavailable`, not just locked**, so on a switch *away* from ai_sound `_set_upmix` first **waits for the switch to come back available** (`input_number.av_upmix_unlock_timeout_seconds`, default 8 s, dashboard‑tunable), then **writes → verifies → retries** (`UPMIX_VERIFY_RETRIES` 3); notify‑only if it never un‑locks.
7. **Volume:** `media_player.volume_set` on the **TV** — applied **once**, the first time `external_arc` is confirmed in the settle loop, then **never re‑asserted** (user‑adjustable). On the robust path h7 already set the soundbar volume; this applies the eARC‑authoritative TV volume to the same target, so they converge.
8. Hold `soundOutput = external_arc` through the settle window (`input_number.av_settle_window_seconds`, debounce `WRONG_CONFIRM_POLLS=2`); notify‑only if it never settles.

Does **not** do power. **Calls h7 only on the cold/bad‑state (`needs_robust`) path** — the IR
branch remains h7's primary caller.

### 2c. Stamp script — `script.av_stamp_desired_audio` (mode parallel)
Fields `sound_mode_label`, `upmix`. Writes `input_text.av_desired_sound_mode_label`,
`input_boolean.av_desired_upmix_state`, `input_datetime.av_audio_preset_at` = `now()`.
Moved out of h7 so the drift‑watch works in network mode. (IR mode's h7 still
writes the same stamp inline.)

### 2d. Drift‑watch — `automation.av_soundbar_drift_watch`
Triggers on `media_player.lg_soundbar` `sound_mode` change. Within
`input_number.av_drift_watch_window` s (default 45, max 60) of the stamp, if the
soundbar drifted off the desired mode, re‑asserts: **TV `soundMode`** for
Standard/AI Sound Pro/Bass/Custom (durable, TV‑root), **soundbar
`select_sound_mode`** for Clear Voice. Re‑asserts AI upmix (unless AI Sound Pro).
**Leaves volume alone.** Works in both modes (keyed off the stamp timestamp).

### 2e. Soundbar preset — `script.h7_soundbar_preset_native`  (**IR‑mode primary + network cold‑boot recovery**)
The proven write→verify→retry→IR‑fallback soundbar primitive. Fields
source/eq/upmix/volume/tv_was_cold. Called by the **default/IR** branch of every
activity, **and (2026‑08‑29) by the network engine on its `needs_robust` path**
(cold/bad‑state boot where the soundbar isn't on ARC) — see §2b step 4b. Its
`source=arc` forcing + IR fallback are exactly what the pure TV/eARC engine path
lacked: on a cold boot the soundbar can wake on Bluetooth in its stale mode, and IR
drives it regardless of the eARC handshake. Bails up front if `media_player.lg_soundbar`
is `unavailable` (integration down); `tv_was_cold=True` enables its settle‑recheck. **2026‑08‑28:** volume was removed from its verify/retry +
settle‑recheck (it sets volume once, never re‑asserts) so manual volume changes
after a switch stick; source/eq/upmix verify + IR fallback (Soundsuite device 16)
unchanged.

### 2f. Activity scripts — `script.activity_<name>` (×10)
`choose`: network branch = `ensure_tv_on` → power‑fail guard → `parallel`
[`nest_display_source`, **device routing** (VRROOM select / Ugreen IR / Oppo
power / Kodi wake, per device), `pyscript.av_tv_reconcile`]. Default branch =
original IR body incl. h7 + `script.lg_tv_ready`, verbatim.

Activities & routing: nlziet (app NLZIET), youtube (app YouTube), kodi (HDMI4 via
VRROOM port 0 + Kodi wake), batocera (HDMI3 direct + WOL switch.batocera), ps5
(HDMI1 direct), blu_ray (HDMI4 via VRROOM port 3 + Oppo remote), xbox (HDMI4 via
VRROOM port 1), shield (HDMI4 via VRROOM port 2 + Ugreen IR dev13 cmd5), switch
(VRROOM 2 + Ugreen dev13 cmd4), ugoos (VRROOM 2 + Ugreen dev13 cmd1).

### 2g. Manual reset — `script.av_reset_audio` (button on `/av-network`)
Re‑asserts the **current activity's** source‑specific audio standards after the user
has fiddled with volume / modes / Bluetooth — **audio only, no source/app switch.**
It is a **thin wrapper**: resolve activity (optional `activity` field, else
`input_text.av_last_activity`; notify + stop if neither) → call
**`pyscript.av_tv_reconcile(activity, reset=True)`** — the engine's reset mode (§2b):
skips the source switch, forces `soundOutput = external_arc` up front (undoes Bluetooth /
TV‑speaker, wakes an auto‑off soundbar), **always takes the soundbar‑direct h7 path**
(eq/upmix/volume with network verify→retry→IR fallback, TV‑websocket‑independent), then
TV‑root soundMode + eARC volume as guarded best‑effort. `mode: restart`; **silent on
success** — notification only on no‑activity. **Why delegate to the engine:** a plain HA
script's `continue_on_error` does **not** catch the raw `websockets…ConnectionClosedOK`
the flaky TV socket throws (observed 2026‑09‑04 — it aborted the script on the TV‑root
step), whereas the engine's Python `try/except` guards do. **Depends on `av_last_activity`**
for the button path (engine‑populated) and on the engine carrying the `reset` param
(redeploy required).

---

## 3. Per‑activity helpers (drive both branches) & dashboard

| Helper (suffix = activity) | Purpose |
|---|---|
| `input_number.av_volume_<a>` | start volume (0–100) |
| `input_select.av_sound_mode_<a>` | `standard`/`bass`/`custom`/`ai_sound`/`clear_voice_base`/`clear_voice_high` |
| `input_boolean.av_ai_upmix_<a>` | AI upmix on/off |
| `input_boolean.av_network_<a>` | per‑activity network allowlist |

Globals: `av_network_mode_master`, `av_settle_window_seconds` (12),
`av_upmix_unlock_timeout_seconds` (8), `av_cold_boot_soundbar_ceiling_seconds` (45),
`av_bluetooth_headphone_volume` (50), `av_drift_watch_window` (45),
`binary_sensor.tv_bluetooth_headphones`, `input_number.h7_soundbar_ready_ceiling`
(soundbar reachability wait, reused by the engine). **Dashboard:** `/av-network`
(global controls + live TV audio status + per‑activity cards + Batocera
spatial‑audio card).

**Helper taxonomy (labels + helpers‑scope categories):** every helper carries a
`av_network` and/or `ir_network` **label** and exactly one **category**, split by
which audio path reads it:
- **Network‑only** — label `av_network`, category *AV Network* (15): the master +
  per‑activity `av_network_<a>` toggles, `av_settle_window_seconds`,
  `av_upmix_unlock_timeout_seconds`, `av_cold_boot_soundbar_ceiling_seconds`,
  `av_bluetooth_headphone_volume`. (engine‑only)
- **Both paths** — labels `av_network`+`ir_network`, category *AV Shared* (35): the
  per‑activity `av_volume`/`av_sound_mode`/`av_ai_upmix` trios, `av_drift_watch_window`,
  `h7_soundbar_ready_ceiling`, and the drift stamp trio (`av_desired_sound_mode_label`,
  `av_desired_upmix_state`, `av_audio_preset_at`).
- **IR‑only** — label `ir_network`, category *IR Network* (4): `h7_switch_delay_cold_boot`,
  `h7_switch_delay_warm_switch`, `h7_delay_audio_after_source_switch`, `lg_tv_use_ir`.

Labels overlap (shared helpers carry both) so filtering `label:av_network` = network+shared
and `label:ir_network` = IR+shared; the three categories are exclusive buckets in the
Helpers UI.

Seeded volumes: NLZiet 15, YouTube 15, Kodi 20, Batocera 10, PS5 15, Blu‑Ray 20,
Xbox 15, Shield 15, Switch 10, Ugoos 15. Sound modes: Batocera `ai_sound`, rest
`standard`. Upmix on: NLZiet, YouTube.

---

## 4. Sound‑mode (eq) ↔ TV soundMode map

| Soundbar eq | label | TV `soundMode` | corrected via |
|---|---|---|---|
| `standard` | Standard | `standard` | TV root ✓ live |
| `ai_sound` | AI Sound Pro | `aiSoundPlus` | TV root ✓ live |
| `bass` | Bass | `bassBoost` | TV root (1:1) |
| `custom` | Custom | `customEq` | TV root (1:1) |
| `clear_voice_base` | Clear Voice (Base) | — | soundbar‑side |
| `clear_voice_high` | Clear Voice (High) | — | soundbar‑side |

TV's single `voiceEnhance` can't distinguish the two Clear Voice modes → kept
soundbar‑side. TV `personalized` has no soundbar equivalent. AI upmix is
soundbar‑only (no TV control).

---

## 5. Bluetooth headphones
TV reports them as `soundOutput=bt_soundbar` (same as a BT‑connected soundbar);
`binary_sensor.tv_bluetooth_headphones` disambiguates by also requiring the
soundbar's source ≠ Bluetooth. When active: audio is fully TV‑managed — soundbar
untouched (auto‑powers off with no eARC input), no `external_arc` force (would
kick the headset off), TV volume set to the BT helper. TV blocks sound‑mode
control on Bluetooth, so no mode handling there.

---

## 6. Deploy / extend
- **Engine change:** edit `pyscript/av_reconcile.py` → copy to `/config/pyscript/`
  → `pyscript.reload` → confirm `pyscript.av_tv_reconcile` service exists.
- **New activity:** add a `PROFILES` entry; create `av_network_<a>` + the 3
  per‑input helpers; graft the router (`choose` network branch = parallel[nest,
  routing, engine]; default = the IR body); add a dashboard card. Don't touch the
  IR path.
- **MCP write tools** need a rotating hourly `BestPracticeKey` from
  `ha_get_skill_guide(home-assistant-best-practices, references/automation-patterns.md)`;
  pass `MandatoryBPS=false`. `ha_config_set_script` `python_transform` requires a
  `config_hash` from `ha_config_get_script`. pyscript is real Python (def/while ok);
  the **python_transform sandbox** forbids `def`/`while`/`type()`/`isinstance` —
  use for‑loops + `.get()` + `.pop/.append/.insert` + slicing.

---

## 7. Constraints & watch‑points
- WOL from HA doesn't reach the TV here → power = IR/Sofabaton
  (`script.ensure_tv_on` = IR primary + official webostv network turn‑on fallback).
- **Cold‑boot soundbar readiness — v2 fix deployed 2026‑08‑29, awaiting a real cold
  test.** Two cold boots failed: (1) power‑off→NLZiet, soundbar came up 32 s late in
  AI Sound Pro; (2) power‑off→Kodi, soundbar came up on **Bluetooth** and the TV
  `soundOutput` sensor was **stale `external_arc`**, which defeated the first (eARC‑gate)
  fix — it cleared instantly on the stale value. Deeper cause: the pure TV/eARC engine
  path (a) never forced the soundbar onto **ARC** (the old h7 did, via `source=arc`) and
  (b) dropped the **IR fallback**, so it can't drive a soundbar that woke on Bluetooth.
  **v2 (§2b step 4b):** the engine now keys off the soundbar's **own `source`** and, when
  it isn't on ARC, hands the initial set to `h7_soundbar_preset_native` (forces ARC +
  IR fallback + cold settle recheck). **Cold boots can't be forced on demand** (HA power
  sensors read "offline" long before the bar is truly cold — only hours off reproduces
  it), so validate after each real failure: check logs for `robust h7 path` vs
  `TV‑primary path`, h7's own mismatch notifications, and `av_cold_boot_<activity>`; raise
  `av_cold_boot_soundbar_ceiling_seconds` if the soundbar needs longer to report in.
- **Cold‑boot source switch crash — fixed 2026‑09‑04.** A cold boot → Batocera did nothing
  (no source, no volume, no mode): the webOS websocket was still (re)connecting when the engine
  fired `launch_app`, which raised `websockets…ConnectionClosedOK`. `_switch_source` was
  unguarded, so it aborted the whole run at step 1. Now source switch retries (4 × 2 s) through
  the reconnect and every TV‑websocket call is guarded — a transient drop is logged and skipped,
  never fatal (§2b step 1). The audio reconcile (soundbar‑direct via h7) doesn't depend on the
  TV websocket anyway, so it now proceeds even if the TV side is briefly flaky.
- **The "30 s cold‑boot delay" is a reporting artifact, not the device — but the FIRST
  root cause was wrong (corrected 2026‑09‑05).** The bar is physically up in ~2‑5 s while HA
  reported it operative ~30 s late, confirmed by the user ("soundbar starts instantly with the
  TV, shown in HA much later"). Originally blamed on `scan_interval` = 30 s + fixed by forcing
  `update_entity` every 2 s — but that forced read never actually worked: it calls the
  soundbar client's `request_all()` on a **dead socket**, which just fails. The true cause was
  `lg_soundbar_plus`'s **reconnect backoff, capped at 30 s** (`protocol.py`): with the bar off
  the backoff reaches its cap, so the background thread sits in `sleep(30)` and won't attempt a
  reconnect until it expires — nothing HA does from the coordinator side can shortcut it. This
  is what made a 2026‑09‑05 Batocera cold boot wait its full 45 s soundbar ceiling. **Fix
  (`lg_soundbar_plus` 0.1.9):** backoff cap 30 s → 5 s, an interruptible‑sleep `poke()`, and
  the coordinator pokes a reconnect on a disconnected forced refresh — so `update_entity`
  finally drives detection and a returning bar is seen in ~a connect RTT. This is the exact
  same class of bug as the TV integration's, in our other integration. The TV side polls every
  5 s (`lg_webos_bsc poll_interval=5`); the soundbar stays push‑driven once connected.
- **Watch:** the TV `…audio_settings.soundOutput` sensor was observed **stale** across
  the 21:30 cold boot (held `external_arc` while the TV was off). The engine no longer
  trusts it for path selection, but the settle loop (§2b step 8) still keys off it — if
  staleness recurs, that hold/volume‑gate logic may need the same soundbar‑source basis.
- **Resolved 2026‑08‑29:** switching *from* ai_sound (AI Sound Pro) to an upmix
  source left upmix stuck at its pre‑ai_sound value — AI Sound Pro takes the
  upmix switch entity `unavailable`, and the engine wrote it before the soundbar
  finished leaving that eq, so the write was dropped. Engine now waits for the
  switch to come back available, then writes+verifies+retries (§2b step 6).
  Validated live: Batocera(AI Sound Pro) → NLZiet, upmix went unavailable → on.
- Once cold boots show no residual drift, turn `av_drift_watch_window` down.

---

## 8. Validation status (2026‑09‑04)
Network‑mode TV‑primary refactor live; NLZiet + Batocera validated live (source/mode/
upmix/volume correct, manual volume sticks); other 8 rolled out, shield spot‑checked.
Fixes since, newest first:
- **2026‑09‑04 (late) — SOURCE‑LEVEL fix: restored bscpylgtv keepalive** (`lg_webos_bsc`
  **0.1.5**, `patch.py`): the root cause of the whole 0.5.4 hang saga was that bscpylgtv
  opens both its sockets with `ping_interval=None` (keepalive OFF) and has no recv timeout,
  so a half‑open socket wedges the recv loop while `is_connected()` still reports True.
  0.5.2 self‑healed this *by accident* (a teardown crash completed the task); 0.5.4 removed
  that crash. We now re‑enable websockets keepalive via a surgical proxy over
  `bscpylgtv.webos_client.websockets` (scoped to bscpylgtv only; ping every 30 s, 20 s PONG
  grace) — a dead socket now raises `ConnectionClosed` in the recv loop → task completes →
  `is_connected()` False → the coordinator reconnects. This *prevents* the wedge at the
  source rather than reacting to it, restoring (deterministically) the pre‑0.5.4 auto‑recovery.
  The 0.1.4 ceilings below remain as a backstop. **Validated live (2026‑09‑05, 00:39–00:43):**
  with the shim active, ~4 min of debug polling (every ~5 s, ~8 keepalive cycles) ran 100 %
  `success: True` with **zero** reconnects/downgrades/timeouts — webOS 26 answers the protocol
  PINGs cleanly, no spurious drops. Still to see: a real cold boot (the original failure trigger).
- **2026‑09‑04 (late) — coordinator‑loop wedge (the 0.1.3 fix was incomplete)**: after 0.1.3,
  a warm‑idle TV coordinator still wedged — TV entities froze at 20:51 and stayed frozen,
  and the next two Activity runs (NLZiet 21:33, Batocera 22:03) parked `running`. Root cause:
  0.1.2/0.1.3 bounded each *command* and each *poll getter*, but **the poll cycle as a whole,
  `connect()`, and `_safe_disconnect()` were still unbounded** — and `async_command` ends with
  `await self.async_refresh()`. So once a poll wedged in the connect/refresh/lock path (not in
  a getter), the DataUpdateCoordinator loop stopped *and* every subsequent blocking TV command
  hung on the dead refresh → the engine's `blocking=True` calls parked the scripts. Fix
  (`lg_webos_bsc` **0.1.4**), structural rather than another point‑timeout: a hard ceiling on
  the **whole** poll cycle (`_POLL_CYCLE_TIMEOUT` 30 s → drop client + report offline), both
  `connect()` paths bounded (`_CONNECT_TIMEOUT` 25 s), `_safe_disconnect` bounded, the trailing
  `async_refresh` made non‑fatal, and the three direct‑client methods (`button`/`send_message`/
  `set_game_genre`) + the sound‑settings reads bounded. Every network‑facing client await in the
  coordinator is now capped, so the loop can never wedge regardless of what bscpylgtv does.
  Recovered the live wedge with an HA restart. Awaiting a real cold boot to confirm.
- **2026‑09‑04 — command hang (bscpylgtv 0.5.4 regression)**: a cold‑boot Batocera run
  parked the Activity script `running` forever — the engine's TV command awaited a reply
  on a half‑open socket that never came (bscpylgtv has `ping_interval=None` + no recv
  timeout; v0.5.4's PR #8 removed the accidental teardown‑crash that used to self‑heal it —
  root cause in §9). Integration hardened (`lg_webos_bsc` 0.1.2/0.1.3): commands bounded by
  `wait_for` (hang → reconnect+retry), poll getters bounded, and a timed‑out power read
  drops the wedged socket so the poll rebuilds. Cleared the stuck run via `pyscript.reload`
  (a wedged script needs an HA restart to fully clear).
- **2026‑09‑04 — manual Reset button** (`script.av_reset_audio`, §2g): delegates to the
  engine's `reset` mode (Python‑guarded, always soundbar‑direct h7). **Validated live:**
  ran `execution=finished`, held the Batocera standard (AI Sound Pro / vol 10 / ARC / eARC),
  no error even while the TV websocket was flapping.
- **2026‑09‑04 — cold‑boot source‑switch crash** (`ConnectionClosedOK` aborting the run):
  source switch retries 4×2 s + all TV‑websocket calls guarded (§2b step 1). Deployed;
  reset‑path exercised live, cold‑boot path awaiting a real cold boot.
- **2026‑08‑30 — cold‑boot detection speed** (v2.1): `_wait_soundbar_reachable` forces a
  fresh read every 2 s past the 30 s `scan_interval`. Deployed; awaiting a real cold boot.
- **2026‑08‑29 — cold‑boot soundbar readiness** (v2): source‑based path select + h7 recovery
  (forces ARC + IR). Deployed; awaiting a real cold boot to confirm.
- **2026‑08‑29 — upmix unlock** (AI Sound Pro → upmix source): validated live.
- **2026‑08‑29 — helper taxonomy** (av_network/ir_network labels + AV Network/Shared/IR
  categories across all 53 helpers): applied.

Open: (a) individual cold‑boot confirmations for the remaining activities — can only be
checked after each real failure (cold boots aren't forceable, §7); (b) **TV webOS websocket
flapping** — an issue in *our own* `lg_webos_bsc` integration (§2a/§9), currently only
worked around in the engine; the integration itself is being hardened (§9).

---

## 9. `lg_webos_bsc` vs the official `webostv` (connection handling)
We own `lg_webos_bsc` (§2a), so the websocket flapping is ours to fix at the source, not
just work around. Compared against HA core's `webostv`
(github.com/home-assistant/core → `homeassistant/components/webostv`) + the `aiowebostv`
library:

| Aspect | Official `webostv` | `lg_webos_bsc` |
|---|---|---|
| State | push callback **+** 10 s poll safety net | push subset **+** 5 s poll (auto‑downgrades to pure poll if a subscription hangs connect) |
| Reconnect | poll cycle: `if is_connected(): return` else `connect()` | same (`async_ensure_connected` each poll) |
| Command guard | `@cmd` decorator catches `WEBOSTV_EXCEPTIONS` → clean `HomeAssistantError`; refuses if TV off | **adopted 2026‑09‑04:** `async_command` reconnect + retry‑once on `ConnectionClosed`/timeout → clean `HomeAssistantError` |
| Availability | `async_set_updated_data(None)` only after a successful connect | off/unreachable → `power=off`, entities stay available |

**Adopted:** the command‑level connection guard — the missing piece that let a raw
`ConnectionClosedOK` crash callers (our retry‑once is actually a bit stronger than the
official decorator, which only surfaces the error). **Deliberately different:** we poll a
minimal set and cache the slow bits, because this webOS 26 firmware silently drops some
subscriptions and 500s/401s some getters — the official model assumes a better‑behaved
stack. **Could still adopt if flapping persists:** route the few direct‑client methods
(`async_remote_button`, `async_send_message`, `async_set_game_genre`) through the same
guard, and consider `aiowebostv`'s newer client if bscpylgtv's reconnect proves weaker.

### 9a. The bscpylgtv 0.5.4 hang (root cause + our layered mitigation)
**Where it comes from.** bscpylgtv opens the control socket with
`websockets.connect(..., ping_interval=None, ...)` — **keepalive disabled** — and its
receive loop (`async for raw_msg in ws:`) has **no read timeout**. So a *half‑open* TCP
socket (silently dead, no close frame — common right after a cold‑boot handshake) wedges
that loop: `connect_task` never finishes, `is_connected()` (= "task not done") keeps
reporting **True**, and command response futures in `self.futures` (resolved only by the
recv loop, cancelled only in its `finally`) **never resolve → the command hangs forever.**
On **0.5.2** this self‑healed by accident — the teardown hit an `asyncio.wait()` `TypeError`
on Python 3.11+, which *completed* the task → `is_connected()` False → our poll reconnected.
**v0.5.4 PR #8** ("Fix teardown closeout crash on Python 3.11+") wrapped the closeout
callbacks in `ensure_future` to remove that crash — correct in itself, but it removed the
accidental self‑heal, so on 2026.09 (Python 3.14) the wedge now sits indefinitely. Hence
"the 0.5.4 hang fix made hangs worse."

**Why not pin to 0.5.2:** its teardown `TypeError` fires on every disconnect on Python 3.14
(worse), and it predates 0.5.3's webOS 26 manifest support. Stay on 0.5.4+.

**Our mitigation (all in `coordinator.py`, no bscpylgtv patch):**
- **Command timeout** (0.1.2) — `async_command` bounds each attempt with `wait_for`
  (`_COMMAND_TIMEOUT` 10 s); a hang → `TimeoutError` → reconnect + retry‑once → clean error.
- **Poll getter timeout** (0.1.3) — `_safe_call` bounded by `_POLL_CALL_TIMEOUT` (5 s) so a
  wedged socket can't hang the poll.
- **Liveness canary** (0.1.3) — a *timed‑out* `get_power_state` in the poll drops the client
  (`async_shutdown_client`), so the next 5 s poll rebuilds a fresh socket. Proactive heal.
- **Whole‑cycle + connect + disconnect ceilings** (0.1.4) — the decisive one. 0.1.2/0.1.3
  bounded the *pieces* but not the *composition*: `connect()` (under `_connect_lock`), the
  full poll sequence, `_safe_disconnect()`, and the `async_refresh()` chained onto every
  command were all still unbounded, so a hang in any of them froze the coordinator loop and,
  through the trailing refresh, hung every blocking command into it (observed 20:51 freeze +
  two wedged scripts, same day). 0.1.4 wraps the entire poll in `_POLL_CYCLE_TIMEOUT` (30 s),
  both `connect()` paths + `_safe_disconnect` in explicit `wait_for`, makes the trailing
  refresh non‑fatal, and routes the last direct‑client methods through `wait_for`. Now **every**
  network‑facing client await is capped — the class of bug (an unbounded bscpylgtv await
  anywhere wedging the loop), not just the instances found so far, is closed.

- **Source‑level keepalive** (0.1.5) — **the actual root fix.** `patch.py`'s
  `patch_bscpylgtv_keepalive()` proxies `bscpylgtv.webos_client.websockets` and re‑enables
  the keepalive bscpylgtv disabled (`ping_interval=None` → 30 s ping / 20 s PONG grace),
  scoped to bscpylgtv only. A dead socket now fails a PING → `ConnectionClosed` in the recv
  loop → `connect_task` completes → `is_connected()` False → reconnect. Prevents the wedge
  instead of reacting to it; installed once in `async_setup_entry` before any connect.

**On the old "webOS may not PONG" worry:** the concern that bscpylgtv disabled
`ping_interval` deliberately turned out not to be load‑bearing — the official HA `webostv` +
`aiowebostv` run keepalive on webOS TVs fine, and our 20 s PONG grace only fails a genuinely
dead socket. If a future firmware ever ignored protocol PINGs, the shim would drop healthy
sockets; the coordinator ceilings above are the backstop, and we'd raise the grace or revert
the shim. Worth filing upstream on `chros73/bscpylgtv` (a recv read‑timeout or re‑enabled
keepalive) so the fork isn't needed, but the local patch fully resolves it for us now.
