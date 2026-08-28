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

### 2b. Engine — `pyscript.av_tv_reconcile(activity)`  (`pyscript/av_reconcile.py`)
The **sole network‑mode audio controller**. Deploy: copy to `/config/pyscript/`
+ `pyscript.reload`. Flow per call:
1. Switch TV source (`launch_app` HDMI pseudo‑apps / `select_source` app titles — see `PROFILES`).
2. **Headphones guard** (`binary_sensor.tv_bluetooth_headphones` on): set TV volume to `input_number.av_bluetooth_headphone_volume`, leave soundbar alone, stop.
3. Read desired eq (`input_select.av_sound_mode_<activity>`) + upmix (`input_boolean.av_ai_upmix_<activity>`).
4. **Stamp** the drift‑watch desired state (calls `script.av_stamp_desired_audio`).
5. **Sound mode:** TV `set_settings(sound,{soundMode})` for the 4 mapping eqs; soundbar `select_sound_mode` for Clear Voice (no TV equivalent).
6. **AI upmix:** soundbar `switch.living_room_lg_soundbar_ai_upmix` (skipped when eq = ai_sound). Waits for soundbar reachability first; each soundbar write is guarded so a not‑ready bar can't abort the run.
7. **Volume:** `media_player.volume_set` on the **TV** — applied **once**, the first time `external_arc` is confirmed in the settle loop (so a cold‑boot eARC handshake can't re‑apply the TV's remembered volume over ours), then **never re‑asserted** (user‑adjustable).
8. Hold `soundOutput = external_arc` through the settle window (`input_number.av_settle_window_seconds`, debounce `WRONG_CONFIRM_POLLS=2`); notify‑only if it never settles.

Does **not** do power. Does **not** call h7.

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

### 2e. Soundbar preset — `script.h7_soundbar_preset_native`  (**IR‑mode primary only**)
The proven write→verify→retry→IR‑fallback soundbar primitive. Fields
source/eq/upmix/volume/tv_was_cold. Still called by the **default/IR** branch of
every activity. **2026‑08‑28:** volume was removed from its verify/retry +
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

---

## 3. Per‑activity helpers (drive both branches) & dashboard

| Helper (suffix = activity) | Purpose |
|---|---|
| `input_number.av_volume_<a>` | start volume (0–100) |
| `input_select.av_sound_mode_<a>` | `standard`/`bass`/`custom`/`ai_sound`/`clear_voice_base`/`clear_voice_high` |
| `input_boolean.av_ai_upmix_<a>` | AI upmix on/off |
| `input_boolean.av_network_<a>` | per‑activity network allowlist |

Globals: `av_network_mode_master`, `av_settle_window_seconds` (12),
`av_bluetooth_headphone_volume` (50), `av_drift_watch_window` (45),
`binary_sensor.tv_bluetooth_headphones`, `input_number.h7_soundbar_ready_ceiling`
(soundbar reachability wait, reused by the engine). All `av_*` helpers carry the
**`av_network`** label. **Dashboard:** `/av-network` (global controls + live TV
audio status + per‑activity cards + Batocera spatial‑audio card).

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
- **Open after the 2026‑08‑28 refactor:** AI‑upmix reliability on a fully cold
  boot (soundbar readiness), and cold‑boot volume holding after the eARC
  handshake. NLZiet+Batocera validated live; the other 8 rolled out but not yet
  individually cold‑tested.
- Once cold boots show no residual drift, turn `av_drift_watch_window` down.

---

## 8. Validation status (2026‑08‑28)
Network‑mode TV‑primary refactor: engine deployed & reloaded (service confirmed);
**NLZiet + Batocera validated live** (source/mode/upmix/volume correct, manual
volume sticks); other 8 activities rolled out via structure‑targeted transform,
shield spot‑checked (VRROOM+engine+Ugreen preserved, soundbar step removed, IR
branch intact) — pending individual live tests.
