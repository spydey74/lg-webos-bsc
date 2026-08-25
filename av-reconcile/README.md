# AV reconcile — network Activity path (TV audio override correction)

A **complement** to the existing IR/Sofabaton Activity architecture
(`AV_Control_Handover.md`), not a replacement. For each Activity it adds a network
path that:

1. switches the TV source over the network (bscpylgtv),
2. **holds the TV's sound output at `external_arc`** for a short *settle window*,
   correcting the TV when it overrides the audio path (the §8 drift — now
   observable via the bscpylgtv audio sensor), and
3. drives the **soundbar preset from per‑input helpers** (volume / sound mode /
   AI upmix) in **both** the network and legacy IR branches.

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
| Per‑input params | volume / sound mode / AI upmix helpers drive the soundbar preset in **both** branches |

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
            ├─ soundbar: h7_soundbar_preset_native (eq/upmix/volume from helpers)
            │            [SKIPPED entirely if BT headphones active]
            ├─ pyscript.av_tv_reconcile(activity)   ← switch source; then either
            │     • BT headphones → set TV volume to the BT helper, stop; or
            │     • otherwise     → hold external_arc through the settle window
            └─ per-activity: VRROOM select / Ugreen / Oppo / WOL / Kodi wake
```

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
