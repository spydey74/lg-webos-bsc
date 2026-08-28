# AV reconcile — network Activity path (TV audio override correction)

> **See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the canonical, up‑to‑date system
> overview** (network‑vs‑IR model, entity tables, deploy/extend, gotchas). This
> README is the lighter intro.

A **complement** to the existing IR/Sofabaton Activity architecture
(`AV_Control_Handover.md`), not a replacement. For each Activity it adds a network
path where, as long as the TV integration works, **the TV is the sole audio
controller** and the soundbar just follows over eARC:

1. switches the TV source over the network (bscpylgtv),
2. sets the audio from the per‑input helpers — **TV `soundMode`** for the four
   mapping eqs (Clear Voice + AI upmix stay soundbar‑side), and **TV volume**
   (applied once, then user‑adjustable),
3. **holds the TV's sound output at `external_arc`** for a short *settle window*,
   correcting the TV when it overrides the audio path (the §8 drift).

**As of 2026‑08‑28** the network branch no longer calls
`h7_soundbar_preset_native` — the pyscript engine owns network‑mode audio. h7
remains the primary **only for the IR branch (master off)**. The per‑input helpers
still drive the soundbar preset in the IR branch.

Power stays **IR/Sofabaton** (`script.ensure_tv_on`; WOL from our integration
doesn't work here). Master‑off = today's behaviour, unchanged. Rolled out to **all
ten Activities** (NLZiet, YouTube, Kodi, Batocera, PS5, Blu‑Ray, Xbox, Shield,
Switch, Ugoos); NLZiet/Kodi/Batocera validated cold+warm, the rest live‑tested.

## Design decisions (locked)

| # | Decision |
|---|---|
| Engine | **pyscript** (`pyscript.av_tv_reconcile`) — real code, in‑repo |
| TV control | **bscpylgtv** integration is the single TV interface |
| Desired audio | TV sound output held at **`external_arc`** on entry |
| Correction debounce | only correct after `soundOutput` is wrong for 2 polls (ignores a cold‑boot transient) |
| Persistent override | **notify only** (`av_audio_override_<activity>`) |
| Bluetooth headphones | audio is TV‑managed: **no soundbar, no output force**; set TV volume to the BT helper; only the video source switches |
| Per‑input params | volume / sound mode / AI upmix helpers drive the audio in both branches: **network** via the TV integration (engine); **IR** via the soundbar preset |
| TV‑primary (network) | 2026‑08‑28: in network mode the TV is the sole audio controller (source + soundMode + volume + drift); h7 is IR‑branch‑only |

## Components

- `pyscript/av_reconcile.py` — the engine (`PROFILES` for all 10 activities).
  Copy to `<config>/pyscript/` and `pyscript.reload`.
- HA‑side helpers, script routers, and the dashboard live in Home Assistant
  (created via MCP), not in this repo. `packages/av_reconcile.yaml` is a starter
  reference for the core helpers only.

## Helpers (HA entity IDs)

Global:

| Helper | Purpose |
|---|---|
| `input_boolean.av_network_mode_master` | master on/off for the whole network path |
| `input_boolean.av_network_<activity>` (×10) | per‑Activity allowlist |
| `input_number.av_settle_window_seconds` | settle‑window length (default 12 s) |
| `input_number.av_bluetooth_headphone_volume` | TV volume used while BT headphones are active (default 50 — BT's own scale) |
| `binary_sensor.tv_bluetooth_headphones` | headphone detection (see below) |

Per‑activity soundbar preset (×10 each, seeded to the previous hard‑coded values):

| Helper | Purpose |
|---|---|
| `input_number.av_volume_<activity>` | start volume |
| `input_select.av_sound_mode_<activity>` | eq: `standard`/`bass`/`custom`/`ai_sound`/`clear_voice_base`/`clear_voice_high` |
| `input_boolean.av_ai_upmix_<activity>` | AI upmix on/off |

All `av_*` helpers carry the **`av_network`** HA label, so the network set is easy
to split from the legacy IR set. `input_boolean.lg_tv_use_ir` remains the deeper
kill‑switch. **Dashboard:** `/av-network` (sidebar) — global controls + live TV
audio status + a per‑activity card each (network toggle, volume, sound mode,
upmix).

## How a network Activity runs

```
tv_was_cold?  ──▶ script.ensure_tv_on  (IR/Sofabaton power — unchanged)
                    │  (abort+notify if TV never reports on)
                    ▼
   parallel ┌─ nest_display_source
            ├─ per-activity routing: VRROOM select / Ugreen / Oppo / WOL / Kodi wake
            └─ pyscript.av_tv_reconcile(activity)  ← owns ALL network-mode audio:
                  switch source; then
                  • BT headphones → set TV volume to the BT helper, stop; or
                  • otherwise → stamp desired; assert TV soundMode (mapping eqs)
                    or soundbar Clear Voice; AI upmix (soundbar); TV volume once
                    on external_arc; hold external_arc through the settle window
```

(The soundbar preset `h7_soundbar_preset_native` is **not** in the network branch
any more — it runs only in the IR/default branch.)

## Bluetooth headphones

The TV reports connected BT headphones as **`soundOutput = bt_soundbar`** — the
*same* value it uses for a Bluetooth‑connected soundbar. So detection can't key on
`soundOutput` alone; `binary_sensor.tv_bluetooth_headphones` disambiguates by also
requiring the **soundbar not be the Bluetooth sink**
(`media_player.lg_soundbar` source ≠ `Bluetooth`).

When headphones are active the engine leaves the soundbar completely alone (it
auto‑powers‑off with no eARC input), does **not** force `external_arc` (that would
kick the headset off), and sets the **TV volume** to
`input_number.av_bluetooth_headphone_volume` (default 50). The TV **blocks sound‑
mode control** while on Bluetooth, so no mode handling is needed there. Headphones
are only ever selected mid‑Activity, never at startup.

## Soundbar drift‑watch

The soundbar preset's own 6 s settle‑recheck can miss a **later** drift: on a cold
NLZiet boot (2026‑08‑27 21:34) the preset applied Standard cleanly and the 6 s
recheck passed, but at **+23 s** the TV's own `soundMode` flipped `standard →
aiSound_soundbar` and dragged the soundbar to **AI Sound Pro**, staying wrong for
~3.5 min. The drift is **TV‑driven** (TV `soundMode` and soundbar `sound_mode`
move in lockstep), triggered by the TV's per‑input audio memory / eARC
renegotiation on cold start.

`automation.av_soundbar_drift_watch` handles residual drift, event‑driven:

- `script.h7_soundbar_preset_native` stamps the desired soundbar mode label
  (`input_text.av_desired_sound_mode_label`), upmix (`input_boolean.av_desired_upmix_state`)
  and a timestamp (`input_datetime.av_audio_preset_at`) whenever it runs.
- The automation triggers on `media_player.lg_soundbar` `sound_mode` changing;
  while within `input_number.av_drift_watch_window` seconds (default 45, max 60,
  on the dashboard) of that stamp, if the mode drifted away from the desired it
  re‑asserts the mode via `media_player.select_sound_mode` (+ AI upmix unless the
  mode is AI Sound Pro). **Volume is left alone** (user‑adjustable). Re‑asserting
  to the desired makes the trigger self‑terminate (no loop); a
  `av_soundbar_drift_corrected` notification records each correction.

Works in both IR and network mode (keyed off the preset timestamp, not the master
toggle).

**Root‑level correction (2026‑08‑27).** The TV `soundMode` is writable on eARC
(`lg_webos_bsc.set_settings(sound, {soundMode: standard|aiSoundPlus})`) and setting
it drives the soundbar in one shot — the TV is the driver, the soundbar the leaf.
So:
Official soundbar‑eq → TV `soundMode` mappings (bscpylgtv G6 `supportSoundMode`):

| Soundbar eq | TV `soundMode` | Corrected via |
|---|---|---|
| `standard` | `standard` | TV root (confirmed live) |
| `ai_sound` (AI Sound Pro) | `aiSoundPlus` | TV root (confirmed live) |
| `bass` (Bass) | `bassBoost` | TV root (1:1) |
| `custom` (Custom) | `customEq` | TV root (1:1) |
| `clear_voice_base/high` | `voiceEnhance` | **soundbar‑side** (TV has one `voiceEnhance` for two soundbar modes, so it can't target the exact one) |

(TV `personalized` has no soundbar equivalent.)

- The **engine** asserts the TV `soundMode` right after the input switch for the
  four mapping modes above (from each activity's `input_select.av_sound_mode_<n>`),
  fixing the TV's per‑input memory so it's less likely to drift. Clear Voice is
  skipped (would override the precise soundbar setting).
- The **drift‑watch** corrects those four at the root via the TV `soundMode`
  (durable — a soundbar‑only correction can be re‑driven by the TV); Clear Voice
  corrects soundbar‑side via `media_player.select_sound_mode`.

The drift‑watch remains as a backstop; once real cold boots show no drift, turn
`av_drift_watch_window` down (or to 0) from the dashboard.

## Deploy / extend

1. HACS → **pyscript**; copy `pyscript/av_reconcile.py` → `<config>/pyscript/`,
   `pyscript.reload`, verify `pyscript.av_tv_reconcile` exists.
2. Helpers, the per‑script `choose`/`default` routers, and the dashboard are all
   created via MCP (see the git history / this doc for the shapes).
3. To extend to a new Activity: add a `PROFILES` entry, an `av_network_<n>` toggle
   + the three per‑input helpers, graft the router (`choose` network branch,
   legacy body preserved as `default`), and point its `h7_soundbar_preset_native`
   at the helpers. Nothing here removes or edits the IR path.

## Validation notes

- Healthy value is `soundOutput = external_arc`. Every clean switch logs
  `settled on external_arc after 0 correction(s)`; a genuine TV override logs
  `soundOutput=tv_speaker for 2 polls, correcting…` then `settled … after 1` —
  confirmed live.
- Readable audio keys on this firmware: `soundOutput`, `soundOutputDigital`,
  `soundMode`, `digitalAudioPriority`, `eArcSupport`, `avSync`, `avSyncSpdif`.
  Only `soundOutput` is *reconciled*; the rest are observable and could be added
  to the loop if the TV is seen to override them.
