# AV reconcile — network Activity path (TV audio override correction)

A **complement** to the existing IR/Sofabaton Activity architecture
(`AV_Control_Handover.md`), not a replacement. It adds a network path for
selected Activities that:

1. switches the TV source over the network (bscpylgtv), and
2. **holds the TV's sound output at `external_arc`** for a short *settle window*,
   correcting the TV when it overrides the audio path (the §8 drift — now
   observable via the bscpylgtv audio sensor).

Everything else stays exactly as today: **power is IR/Sofabaton** (WOL doesn't
work here), and the **soundbar stays on the proven `h7_soundbar_preset_native`
path**. Master-off = today's behaviour, unchanged.

## Design decisions (locked)

| # | Decision |
|---|---|
| Engine | **pyscript** (`pyscript.av_tv_reconcile`) — real code, in-repo |
| TV control | **bscpylgtv** integration is the single TV interface |
| Desired audio | TV sound output held at **`external_arc`** on entry |
| Persistent override | **notify only** (`av_audio_override_<activity>`) |
| Bluetooth headphones | when active: **no soundbar, no output force** — audio left entirely to the TV; only the video source switches |
| Scope | **NLZiet, Kodi, Batocera** first |

Headphones are never selected at startup — only mid-Activity — so the engine
always *targets* `external_arc` on entry and the guard only trips if you've moved
to BT headphones and then change Activity.

## Components

- `pyscript/av_reconcile.py` — the engine. Copy to `<config>/pyscript/`.
- `packages/av_reconcile.yaml` — helpers + `binary_sensor.tv_bt_headphones` +
  the router pattern to graft onto each Activity script.

## Helpers

| Helper | Purpose |
|---|---|
| `input_boolean.av_network_mode` | master on/off for the whole network path |
| `input_boolean.av_net_nlziet` / `_kodi` / `_batocera` | per-Activity allowlist |
| `input_number.av_settle_seconds` | settle-window length (default 12 s) |
| `binary_sensor.tv_bt_headphones` | derived from the TV's `soundOutput` |

`input_boolean.lg_tv_use_ir` remains the deeper kill-switch: if the TV is ever
blocked again upstream, master-off (or the legacy path) restores IR instantly.

## How a network Activity runs

```
tv_was_cold?  ──▶ script.ensure_tv_on  (IR/Sofabaton power — unchanged)
                    │  (abort+notify if TV never reports on)
                    ▼
   parallel ┌─ nest_display_source
            ├─ soundbar: h7_soundbar_preset_native   [SKIPPED if BT headphones]
            ├─ pyscript.av_tv_reconcile(activity)     ← switch source + hold external_arc
            └─ per-activity: VRROOM select / WOL / Kodi wake
```

The engine loop: every second within the window, read the TV's `soundOutput`;
if it isn't `external_arc`, call `lg_webos_bsc.set_sound_output`; exit early once
it's held for `STABLE_HOLD_SECONDS`; notify if the window expires still wrong.

## Deploy

1. HACS → install **pyscript** if not present.
2. Copy `pyscript/av_reconcile.py` → `<config>/pyscript/av_reconcile.py`; reload
   pyscript (or restart). Verify the `pyscript.av_tv_reconcile` service exists.
3. Create the helpers + headphone `binary_sensor` (package file or UI/MCP).
4. Graft the router onto `script.activity_nlziet/kodi/batocera` (legacy body kept
   as the `default:` branch). **Review the diff before applying.**
5. Enable `av_network_mode` + the per-Activity toggle for **one** Activity and
   test — a warm switch first, then a cold start — watching
   `sensor.lg_webos_tv_oled83g67lw_audio_settings` and the pyscript logs.

## Validation notes

- The confirmed healthy value is `soundOutput = external_arc` (live 2026-08-24).
- Readable audio keys on this firmware: `soundOutput`, `soundOutputDigital`,
  `soundMode`, `digitalAudioPriority`, `eArcSupport`, `avSync`, `avSyncSpdif`.
  Only `soundOutput` is *reconciled*; the rest are observable for context and
  could be added to the loop later if the TV is seen to override them too.
- Extend to more Activities by adding a `PROFILES` entry + an `av_net_<n>` toggle
  + the router branch. Nothing here removes or edits the IR path.
