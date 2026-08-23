# LG webOS (bscpylgtv) — Home Assistant custom integration

Controls an **LG webOS 26** TV as a proper Home Assistant **device**, using the
[`bscpylgtv`](https://github.com/chros73/bscpylgtv) engine with its blacklisted
pairing manifest replaced **at runtime** by aiowebostv's canonical webOS 26
manifest (from [aiowebostv PR #719](https://github.com/home-assistant-libs/aiowebostv/pull/719),
commit `2eff7398`, shipped in `aiowebostv >= 0.9.2`).

The headline capability over core `webostv` is the **LG Game Optimizer genre**
select (`setSystemSettings` surface), which core `webostv` does not expose.

> **Scope:** this is an **additive complement** to Home Assistant's core
> `webostv`, not a replacement. Phase 1 delivers a `media_player`
> (power / volume / mute / current app / source) plus a **Game Optimizer genre**
> `select`.

---

## Why this exists (the webOS 26 story, in brief)

webOS 26 (firmware ~2026‑08‑11) made two changes that broke every open‑source
LG client:

1. **Blacklisted the shared `com.lge.test` pairing manifest** — every register
   with it returns `403 blacklisted certificate`, even with a valid key, because
   the firmware re‑checks the manifest on *every* reconnect. This is why the
   stock `bscpylgtv` manifest fails to connect. **Fix:** present aiowebostv's new
   canonical manifest (no `signatures`, no `signed`, one top‑level `permissions`
   list incl. `LAUNCH` + `WRITE_SETTINGS`, `appVersion` `1.1`). This integration
   does it at runtime — no fork of `bscpylgtv`. See `patch.py`.
2. **`setSystemSettings` is not directly callable** (it 401s regardless of key).
   The working path is the `createAlert → closeAlert` **alert bridge**, which
   `bscpylgtv`'s `set_settings()` already implements. The luna call's result is
   not returned over the socket, so settings changes are confirmed **by effect,
   not response**.

`launch_app` **works** on this firmware. The confirmed‑good reference for every
call is `lg_webos_net.py` in the parent project.

---

## The decision gate (sec.3a) — RESOLVED: fresh pairing works

**Result (confirmed live on real hardware, 2026‑08‑22): a fresh pairing with the
new canonical manifest grants FULL permissions.** In the gate test on a webOS 26
TV, a brand‑new key launched an app (`launch_app` → screensaver) and drove the
Game Optimizer genre via the alert bridge (the genre visibly flipped on screen).
Only the *direct* `setSystemSettings` alias returned `401` — expected, since no
SSAP client can call it directly; the working path is the alert bridge.

**So the grandfathered key is no longer required** — just pair fresh from the
config flow (**“Pair fresh”**, the default). The paste‑an‑existing‑key path is
kept only as a convenience/fallback (e.g. reusing a key from LGTVCompanion).

You can re‑verify on your own TV at any time:

```bash
pip install --upgrade "aiowebostv>=0.9.2"
python3 tools/webos26_decision_gate_probe.py <TV-IP>
```

It pairs fresh, runs a permission battery (reads, `launch_app`, and the
`gameGenre` alert bridge), and prints a hypothesis A/B verdict. Watch the TV: the
Game Optimizer genre flips FPS → Standard if the bridge works.

---

## Installation (HACS custom repository)

1. HACS → ⋮ → **Custom repositories** → add this repo, category **Integration**.
2. Install **LG webOS (bscpylgtv)**, restart Home Assistant.
3. **Settings → Devices & services → Add integration → LG webOS (bscpylgtv)**
   — or accept the **SSDP‑discovered** TV card if it appears (it pre‑fills the
   host and dedupes by the TV's UPnP id).
4. Pick the mode per your decision‑gate result, enter the TV IP (and MAC for
   Wake‑on‑LAN power‑on), and finish.

**Power‑on:** Wake‑on‑LAN requires a MAC address and the TV's
**“Mobile TV On”** setting enabled. If HA can't broadcast onto the TV's subnet
(some container setups can't), set an explicit **WOL broadcast address**
(e.g. `192.168.1.255`) in the integration options.

---

## Entities

- **`media_player`** — power off (`power_off`), power on (Wake‑on‑LAN),
  volume / mute, current app, and source select.
- **`select` · Game Optimizer genre** — Standard, FPS, RTS, RPG, Sports via
  `set_settings("other", {"gameGenre": …})`.
- **`sensor` · Audio output** — shows the current sound output (HDMI ARC/eARC,
  Bluetooth, TV speakers, optical, …). webOS reports `external_arc` for both ARC
  and eARC; the raw value is available as the `raw_sound_output` attribute.
- **`select` · Picture mode** — `set_system_picture_mode` (disabled by default;
  some HDR/Dolby modes only apply to HDR content, so a set may fail depending on
  what's playing).
- **`button`** — Screen off / Screen on / Screensaver / Reboot / Reboot (soft).
  These are one-shot actions that do not need the input socket.
- **`number` · Picture** — Backlight / Contrast / Brightness / Color. These read
  back real values via `get_picture_settings` and write via
  `set_settings("picture", …)`. (Sharpness / OLED light are omitted — including
  them 500s the read on this firmware.)
- **`sensor` · Model** (diagnostic, disabled by default) — model name + serial
  from `get_system_info` (`get_software_info` 401s on this firmware, so version
  sensors aren't available).
- **`remote`** (disabled by default) — sends webOS key presses
  (`remote.send_command` with names like HOME/BACK/UP/DOWN/ENTER/…). Uses the
  input/pointer socket, which can 401 on this firmware — a failed key raises a
  clear error but never brings the entity or entry down. Enable it to try.
- **`notify`** — shows an on-screen toast on the TV (`bscpylgtv.send_message`).
- **`sensor` · Audio settings** — the TV's reported sound-category settings.
  State is the current sound output; **every readable key is an attribute**.
  Confirmed readable on webOS 26 (OLED G6): `soundMode`, `soundOutput`,
  `soundOutputDigital`, `avSync`, `avSyncSpdif`. Companion per-setting sensors
  exist too (disabled by default) for history/automation. The readable subset is
  discovered at runtime, so other models self-adjust.
  > Most of the ~100 sound keys **500 on read** on this firmware (`aiSound`,
  > `bluetoothMode`, `soundOptimizer`, `eArc`, `autoVolume`, …). They can often
  > still be **written** with the `lg_webos_bsc.set_settings` service
  > (`category: sound`), just not read back.

### Services

Raw passthroughs (mirror `lg_webos_net.py`'s verbs), targeted at the TV's
`media_player`:

- `lg_webos_bsc.launch_app` — launch an app by id.
- `lg_webos_bsc.set_settings` — write a `setSystemSettings` category via the
  alert bridge (e.g. `category: picture`, `settings: {pictureMode: game}`).
- `lg_webos_bsc.command` — raw SSAP request (e.g. `uri: audio/getVolume`);
  returns the response.
- `lg_webos_bsc.luna` — raw protected luna call via the alert bridge (fires on
  close; no result returned).

### Power‑on (Wake‑on‑LAN)

Power‑*off* works out of the box. Power‑*on* needs two things:

1. A **MAC address** set in the integration **options** (Settings → Devices &
   services → LG webOS → Configure). The `media_player` only advertises TURN_ON
   once a MAC is present.
2. The TV's **“Mobile TV On”** / Wake‑on‑LAN setting enabled
   (General → Devices → External Devices, or Network settings, depending on
   model). Without it the TV ignores the magic packet.

### HDMI input switching

HDMI 1–4 do **not** appear in the source list until you enable
**“Enable HDMI input switching”** in the options (off by default — see the
soundbar‑drift caution below). Once enabled, the inputs from the TV appear
alongside launchable apps.

### Power state

The TV is reported **off** in standby too (webOS “Active Standby”/“Suspend”),
not just when fully unreachable — so a shut‑down TV shows `off` even while it
stays on the network for Quick Start. “Screen Off” counts as on (audio may still
be playing over eARC).

### Confirmed vs unconfirmed (labeled as `lg_webos_net.py` labels its verbs)

| Capability | Status on this TV/firmware |
|---|---|
| Fresh‑key full permissions (sec.3a hypothesis A) | **CONFIRMED** (2026‑08‑22, fresh pair) |
| Loads in HA as a device (media_player + select + sensor) | **CONFIRMED** (clean logs) |
| `launch_app` (app / screensaver) | **CONFIRMED** (fresh key) |
| Game Optimizer genre FPS ↔ Standard (alert bridge) | **CONFIRMED** (seen on screen + in HA) |
| Volume set / mute write | **CONFIRMED** (verified in HA) |
| `power_off` | **CONFIRMED** (verified in HA) |
| Reads (current app, volume, sound output) | **CONFIRMED** |
| Power state incl. standby → off | **CONFIRMED** (fixed after live report) |
| Power‑on (Wake‑on‑LAN) | **NOT WORKING here** — environmental (HA can't broadcast to the TV subnet); official webOS method also fails, a phone WOL app works |
| HDMI input switching (`set_input`) | **CONFIRMED** (opt‑in; verified live) |
| Picture settings **read** (backlight/contrast/brightness/color) | **CONFIRMED** via `get_picture_settings` |
| Picture settings read incl. sharpness/oled_light | **NOT AVAILABLE** — 500s the whole batch |
| `get_system_info` (model/serial) | **CONFIRMED** |
| `get_software_info` (sw version) | **NOT AVAILABLE** — 401 |
| Remote key presses (`button()`) | **CONFIRMED** (HOME verified live; input socket works with the fresh key) |
| SSDP discovery | **CONFIRMED** (dedupes against the already-configured TV) |
| Push state updates (subscriptions) | **CONFIRMED** — volume/app/input push in <1 s; no connect hang |
| Notify (on-screen toast) | **LIKELY** — `send_message`, not yet verified live |
| Game genre **read‑back** | **NOT AVAILABLE** — `getSystemSettings` 500s; the select shows the last value set this session |

---

## Cautions & non‑goals

- **Additive only.** This integration does **not** touch the Sofabaton/IR
  architecture, `script.activity_*`, or `input_boolean.lg_tv_use_ir`. IR stays
  the Activity path until you choose to migrate. No existing automation/script is
  modified.
- **HDMI input switching is opt‑in and OFF by default.** A network `switchInput`
  near the audio path can trigger the soundbar eARC/DAFC EQ drift (see the
  parent project's `AV_Control_Handover.md` §8). Enable it in the integration
  **options** only if you accept that risk; it is never auto‑fired.
- **TV volume ≠ audible level.** Audio is eARC to the soundbar — expose TV volume
  but keep it out of audio‑level automation.
- **Key custody — now moot (hypothesis A held).** Because fresh pairing grants
  full permissions on this firmware, the old grandfathered‑key fragility no
  longer applies: if a key is ever lost, just pair fresh again. The integration
  still stores its key in the config entry as the single source of truth and
  never overwrites a working key on its own. (Historical note: under the *old*
  manifest only a pre‑update key had full permissions — that is no longer the
  case.)
- **No calibration / LUTs** in this pass.

---

## State updates: hybrid push + poll (sec.5)

Like Home Assistant's core `webostv`, this integration is **push-first**:

- It subscribes to a curated scalar set (`power`, `current_app`, `muted`,
  `volume`, `sound_output`) via `register_state_update_callback`, so changes made
  with the physical remote (volume, HDMI switches, power) reflect in HA within a
  fraction of a second — verified on webOS 26 (`tools/subscription_probe.py`).
- The periodic poll becomes a **reconnect heartbeat** that also refreshes the
  bits we don't subscribe (apps/inputs list, `system_info`, picture settings).
- `bscpylgtv` awaits subscription setup with **no timeout**, so a silently-dropped
  subscription could hang `connect()`. We bound it (20 s) and **automatically fall
  back to pure polling** for the session if that happens — zero regression.
- We deliberately do **not** subscribe `system_info`/`software_info` (static;
  `software_info` 401s) or `apps`/`inputs` (their subscribed shape differs).
- The canonical manifest is re-applied on **every** (re)connect; the input socket
  is lazy so it can't abort connect; a merely-off/unreachable TV is reported
  **off**, not unavailable, and detected within ~2 s.

---

## Project layout

```
custom_components/lg_webos_bsc/
  __init__.py        entry setup / unload, key persistence
  manifest.json      requirements: bscpylgtv, aiowebostv>=0.9.2, wakeonlan
  const.py           domain, defaults, genre options
  patch.py           runtime manifest replacement (sec.2)
  coordinator.py     client creation/connect/poll, in‑memory key store (sec.3/4/5)
  config_flow.py     fresh‑pair AND paste‑existing‑key paths (sec.3a)
  entity.py          shared device grouping
  media_player.py    power/volume/mute/source + raw services
  select.py          Game Optimizer genre, picture mode
  sensor.py          audio output, model
  number.py          picture: backlight/contrast/brightness/color
  button.py          screen on/off, screensaver, reboot
  remote.py          webOS key presses (input socket; may 401)
  notify.py          on-screen toast messages
  diagnostics.py     redacted config-entry + runtime dump
  services.yaml      launch_app / set_settings / command / luna
  strings.json  translations/en.json
tools/
  webos26_decision_gate_probe.py   the sec.3a test — run first
  get_picture_settings_probe.py    phase-2 read gate (picture/sw-info reads)
  subscription_probe.py            push-updates gate (do subscriptions fire?)
  sound_settings_probe.py          which sound-category keys read back
hacs.json  README.md
```

## Detecting TV-side audio overrides (companion to the soundbar integration)

When automations set a 'desired' audio state on an LG soundbar (see the companion
[spydey74/ha-lg-soundbar](https://github.com/spydey74/ha-lg-soundbar)), the TV can
later override parts of it with its own internal logic. The **Audio settings**
sensor surfaces the TV's *actual* sound-category state so an automation can detect
the drift, e.g.:

```yaml
# Alert if the TV switched its digital output away from the desired passthrough
- alias: TV overrode digital audio output
  trigger:
    - platform: state
      entity_id: sensor.lg_webos_tv_audio_settings
      attribute: soundOutputDigital
  condition:
    - "{{ state_attr('sensor.lg_webos_tv_audio_settings', 'soundOutputDigital') != 'auto' }}"
  action: ...
```

Run `tools/sound_settings_probe.py` to see exactly which keys and values your TV
reports.

## Credits & acknowledgements

This integration stands on work generously shared by others — thank you:

- **[chros73/bscpylgtv](https://github.com/chros73/bscpylgtv)** — the engine this
  integration drives (game optimizer, picture, `set_settings` alert bridge, subscriptions).
- **[home-assistant-libs/aiowebostv](https://github.com/home-assistant-libs/aiowebostv)**
  — the canonical webOS 26 pairing manifest (PR #719) we present at runtime, and
  the push/`register_state_update_callback` model this integration mirrors.
- **[Home Assistant core `webostv`](https://github.com/home-assistant/core/tree/dev/homeassistant/components/webostv)**
  — the hybrid push + reconnect-poll architecture we followed.
- **[belikh/ha_chros73_bscpylgtv](https://github.com/belikh/ha_chros73_bscpylgtv)**
  — a helpful reference for the HA wiring shape and the bscpylgtv entity surface
  (number/select/button/remote/notify/services).
- **LGTVCompanion** and **BetterDisplay** — independently found and shipped the
  unsigned-manifest fix for the webOS 26 blacklist that made any of this possible.

Any mistakes here are ours, not theirs.
