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
3. **Settings → Devices & services → Add integration → LG webOS (bscpylgtv)**.
4. Pick the mode per your decision‑gate result, enter the TV IP (and MAC for
   Wake‑on‑LAN power‑on), and finish.

**Power‑on:** Wake‑on‑LAN requires a MAC address and the TV's
**“Mobile TV On”** setting enabled.

---

## Entities (phase 1)

- **`media_player`** — power off (`power_off`), power on (Wake‑on‑LAN),
  volume / mute, current app, and source select.
- **`select` · Game Optimizer genre** — Standard, FPS, RTS, RPG, Sports via
  `set_settings("other", {"gameGenre": …})`.

### Confirmed vs unconfirmed (labeled as `lg_webos_net.py` labels its verbs)

| Capability | Status on this TV/firmware |
|---|---|
| Fresh‑key full permissions (sec.3a hypothesis A) | **CONFIRMED** (2026‑08‑22, fresh pair) |
| `launch_app` (app / screensaver) | **CONFIRMED** (fresh key) |
| Game Optimizer genre FPS ↔ Standard (alert bridge) | **CONFIRMED** (seen on screen, fresh key) |
| Reads (current app, volume) | **CONFIRMED** |
| `power_off` | **LIKELY** — standard SSAP, untested here |
| Volume set / mute write | **LIKELY** — audio scope present, write untested |
| HDMI input switching (`set_input`) | **UNCONFIRMED** — off by default (see below) |
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

## How the connection is made robust (sec.5)

- The client is created with **`states=[]`** and the coordinator **polls**
  (`get_current_app`, `get_volume`, `get_muted`, `get_power_state`). This avoids
  the subscription/static‑state cascade — `bscpylgtv`'s `connect_handler` fetches
  `software_info` unguarded, which can fail on this firmware and abort setup.
- The canonical manifest is re‑applied on **every** (re)connect.
- Input‑socket use is lazy in `bscpylgtv` (only on button/cursor), so it cannot
  abort connect; a merely‑off/unreachable TV is reported as **off**, not
  unavailable.
- Cold‑boot connects use a bounded retry loop (~90 s), mirroring `lg_webos_net.py`.

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
  media_player.py    power/volume/mute/source
  select.py          Game Optimizer genre
  strings.json  translations/en.json
tools/
  webos26_decision_gate_probe.py   the sec.3a test — run this first
hacs.json  README.md
```
