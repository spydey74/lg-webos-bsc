# Decision record — stay on bscpylgtv (pure-poll) vs migrate to aiowebostv (Path C)

**Date:** 2026-09-06
**Decision:** **STAY** on the current config (`lg_webos_bsc` on bscpylgtv, push disabled / pure-poll, 0.1.7). Treat Path C as a **triggered future move**, not a now move.
**Status of the everyday path:** working again after the 0.1.7 fix; first clean cold boot validated 2026-09-06 18:45 (~8 s, 0 corrections). Watching for consistency, esp. an overnight/long-off cold start.

---

## 1. Background

Over 2026-09-04..06 the TV integration wedged **six times**, each a different socket teardown/reconnect hang, all inside bscpylgtv's **push subscription machinery**. We stopped patching individual modes and **disabled push** (`_push_mode = False`, 0.1.7) — pure polling removes the layer where every hang lived. TV state in HA now refreshes on the ~5 s poll instead of <1 s push. "Path C" = rebuild the integration's connection/state layer on **aiowebostv** (the official HA-libs client that HA core `webostv` uses), keeping any bscpylgtv-only features as scripts.

## 2. The pivotal finding (VERIFIED 2026-09-06)

The engine's per-activity writes split into two classes:

| Operation (per activity switch unless noted) | SSAP mechanism | aiowebostv on webOS 26 |
|---|---|---|
| `launch_app` / `select_source` | app/input API | ✅ works |
| volume / mute set | audio API | ✅ works |
| `set_sound_output` (force eARC) | `audio/changeSoundOutput` | ✅ works (`change_sound_output`) |
| power off | standard | ✅ works |
| **assert `soundMode`** (drives soundbar EQ on eARC) | **`setSystemSettings` (sound)** | ❌ **no path** |
| Game Optimizer `gameGenre` (rare) | `setSystemSettings` (other) | ❌ no path |
| picture settings write | `setSystemSettings` (picture) | ❌ no path |

**Verified fact 1 — aiowebostv has NO alert bridge and NO `setSystemSettings` method.**
Source: `home-assistant-libs/aiowebostv/aiowebostv/webos_client.py` (master, fetched 2026-09-06). It exposes generic `request()` / `command()` (ssap:// only), `subscribe()`, `register_state_update_callback`, and standard writes (`set_volume`, `set_mute`, `change_sound_output`, `launch_app`, `set_input`, `power_off`, `button`). There is **no `createAlert → closeAlert` alert bridge** and no `set_system_settings`.

**Verified fact 2 — HA core issue #180025 is still OPEN, no fix / no linked PR (fetched 2026-09-06).**
It concerns the **direct** `setSystemSettings` write regressing on **older** webOS (LG C3 / CX) after the aiowebostv 0.9.2 bump. On **webOS 26** (our OLED83G67LW), our own hardware testing already established direct `setSystemSettings` **401s regardless of permissions** — the alert bridge is the only working path. **So #180025's eventual fix would restore older-firmware direct writes; it does NOT unblock webOS-26 writes.** The `#180025` cloud watcher remains useful as a general aiowebostv-write-health signal, but it is **not** a gate that makes Path C viable for this TV.

**Consequence:** on webOS 26, `soundMode` (frequent) + `gameGenre` + picture writes fundamentally require the alert bridge, which **only bscpylgtv implements**. aiowebostv cannot do them today, and no upstream fix on the horizon changes that. So **Path C does not let us retire bscpylgtv** — it only lets aiowebostv take over the connection/state/standard-control layer, while bscpylgtv (or a ported alert bridge) must stay for `setSystemSettings` writes.

## 3. Dimension-by-dimension comparison

| Dimension | **Stay** (bscpylgtv, pure-poll 0.1.7) | **Path C** (aiowebostv + alert-bridge writes still on bscpylgtv) |
|---|---|---|
| Connection reliability | proven working now; hardened by us the hard way | aiowebostv reconnect is battle-tested across all HA core installs — likely better long-term |
| Real-time updates | ~5 s poll | **<1 s push restored** — the main thing regained |
| State reads (vol/app/input/power/soundOutput) | ✅ polled | ✅ pushed + polled |
| Media control (source/vol/output/power) | ✅ | ✅ (standard SSAP — safe) |
| `soundMode` writes (engine core, frequent) | ✅ alert bridge, works today | ❌ **stays on bscpylgtv** (aiowebostv can't; #180025 irrelevant for webOS 26) |
| Game Optimizer / picture writes (rare) | ✅ | bscpylgtv one-shot scripts |
| Library maintenance | bscpylgtv = niche (single maintainer) | aiowebostv = official HA-libs, actively maintained |
| Connection model | 1 client | aiowebostv persistent **+ bscpylgtv** for setSystemSettings (one-shot connect→write→disconnect, or a 2nd persistent socket) |
| webOS-26 quirks already solved | done (500s on sound reads, 401 sw-info, alert bridge, manifest) | must re-derive/re-test in the new integration |
| Effort to reach | zero (done) | **significant rewrite + re-test + re-pair risk**, right after we finally stabilized |
| Entity IDs / engine wiring | unchanged | changes if adopting HA-core `webostv` (engine must be re-pointed) |
| Exposure to #180025 | none (alert bridge sidesteps it) | none for webOS 26 either (we'd keep the alert bridge on bscpylgtv regardless) |

## 4. Net delta — what C buys vs costs

**C gains:**
1. Reliable **<1 s state updates** back (the only thing pure-poll gave up).
2. A **better-maintained** connection foundation, aligned with HA core.
3. Escape from bscpylgtv's fragile teardown/reconnect **for the connection/state layer**.

**C costs / risks:**
1. **Does not retire bscpylgtv** — the frequent `soundMode` write still needs the alert bridge, which aiowebostv lacks. You end up running **two clients** (or invoking bscpylgtv one-shots on every activity switch).
2. **Full rewrite with real regression risk**, immediately after a week of instability we just escaped.
3. **Re-deriving the webOS-26 workarounds** already paid for in bscpylgtv.
4. **Re-pairing / entity re-pointing** risk to the engine.

## 5. The three real ways to handle the alert-bridge gap under C

- **C-a — Hybrid, bscpylgtv for writes.** aiowebostv persistent (connection/state/standard control); bscpylgtv one-shot (connect→alert-bridge write→disconnect) for `soundMode` every activity + `gameGenre`/picture rarely. Works, but a bscpylgtv connect on every activity switch is exactly the flaky path we're trying to leave, and two clients may hit the TV's connection limit (we saw `1008 policy violation Try Again Later` during boot).
- **C-b — Port the alert bridge into aiowebostv** (upstream PR or a thin fork). Cleanest end state — aiowebostv does everything, bscpylgtv retired. Real work + upstream review latency; a fork reintroduces a maintenance burden.
- **C-c — Redesign the engine to not use TV `soundMode`.** Drive the soundbar EQ **only** on the soundbar directly (`media_player.select_sound_mode` on `media_player.lg_soundbar`, already a supported fallback path). Then aiowebostv covers all TV needs and only the rare Game Optimizer needs a bscpylgtv one-shot. Cost: lose the "TV soundMode drives the soundbar in one shot + fixes the TV's per-input eq memory" behavior; the soundbar-direct path must become primary and be proven as reliable as it is as today's fallback.

## 6. Recommendation

**Stay.** It works, and the only cost is ~5 s of UI/automation latency you don't feel in normal use. A rewrite now trades a known-good state for migration risk to buy back sub-second updates you may not need — and it wouldn't even retire bscpylgtv.

Revisit Path C only when a **specific trigger** fires:
- **Trigger 1 — pure-poll proves unreliable again** over the next batch of cold boots (esp. overnight). Then C's reliability case becomes real and worth the risk.
- **Trigger 2 — sub-second updates genuinely start to matter** (e.g. a new automation or UX need that 5 s lag breaks). Then weigh C-b or C-c.

If a trigger fires, prefer **C-c** (drop TV `soundMode`, aiowebostv for everything, bscpylgtv one-shot for the rare Game Optimizer) as the cleanest single-client-ish end state — **provided** the soundbar-direct EQ path proves reliable as the primary. Fall back to **C-b** (port the alert bridge) if TV-driven soundMode must stay.

## 7. Open items to confirm before starting any C variant

- Re-confirm aiowebostv exposes a `soundOutput` state (our engine reads `sensor..._audio_settings.soundOutput`) and the app/input/volume surface the engine needs, with matching values on webOS 26.
- Decide entity-ID strategy: rebuild-on-aiowebostv-keeping-our-IDs (C1) vs adopt HA-core `webostv` and re-point the engine (C2).
- If C-c: validate the soundbar-direct EQ path (select_sound_mode) as reliably as today's TV-soundMode path across all activities, cold and warm.
- Re-verify facts 1 & 2 at that time (aiowebostv moves; #180025 may change) — but note fact 2 is largely moot for webOS 26 regardless.
