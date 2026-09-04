"""
AV reconcile engine (pyscript) -- LG webOS TV audio controller (network mode).

In NETWORK mode (input_boolean.av_network_mode_master + av_network_<activity> on)
this engine is the SOLE audio controller for an Activity. On eARC the TV is the
driver and the soundbar is the leaf, and the TV integration (bscpylgtv) is keyed
into TV startup where the soundbar trails -- so the TV owns everything it can:

  * source        -> TV (launch_app / select_source)
  * sound mode    -> TV soundMode for the four mapping eqs (standard/ai_sound/
                     bass/custom); Clear Voice has no TV equivalent so it is set
                     on the soundbar directly
  * volume        -> TV volume_set (on eARC this drives the soundbar); set ONCE,
                     then it is the user's -- nothing re-asserts it
  * AI upmix      -> soundbar switch (no TV equivalent; skipped for AI Sound Pro)
  * sound output  -> held at external_arc through a short settle window
  * drift stamp   -> records the desired soundbar mode/upmix/timestamp so
                     automation.av_soundbar_drift_watch can re-assert TV-driven
                     drift afterwards (this stamp used to live in
                     h7_soundbar_preset_native, which network mode no longer calls)

It does NOT do power (that stays IR/Sofabaton -- WOL doesn't work here). The
soundbar preset script.h7_soundbar_preset_native is NOT called in network mode;
it remains the primary audio path only for IR mode (master off).

Bluetooth-headphones guard: headphones are only ever selected mid-Activity. If
they are active the audio is left entirely to the TV -- no soundbar touch, no
external_arc force (which would kick the headset off) -- and the TV volume is set
to the Bluetooth-headphone level.

Exposed service: pyscript.av_tv_reconcile(activity=<name>)

Deploy: copy this file to <config>/pyscript/av_reconcile.py (HACS 'pyscript'
integration installed) and run pyscript.reload. Data-only until an Activity
script's network branch calls it.
"""

# --- entities (confirmed live) -----------------------------------------------
TV = "media_player.lg_webos_tv_oled83g67lw"
AUDIO = "sensor.lg_webos_tv_oled83g67lw_audio_settings"  # has soundOutput attr
SOUNDBAR = "media_player.lg_soundbar"
UPMIX_SWITCH = "switch.living_room_lg_soundbar_ai_upmix"

DESIRED_SOUND_OUTPUT = "external_arc"
# Headphone detection is centralised in this template binary_sensor (it also
# disambiguates the ambiguous 'bt_soundbar' output from the real soundbar by
# checking the soundbar isn't the Bluetooth sink).
HEADPHONE_SENSOR = "binary_sensor.tv_bluetooth_headphones"
# Bluetooth headphones want their own volume scale (default ~50, vs soundbar 10-20).
BT_VOLUME_HELPER = "input_number.av_bluetooth_headphone_volume"
BT_VOLUME_DEFAULT = 50.0

# --- per-activity audio helpers (suffix = activity name) ---------------------
VOLUME_HELPER = "input_number.av_volume_"
SOUND_MODE_HELPER = "input_select.av_sound_mode_"
UPMIX_HELPER = "input_boolean.av_ai_upmix_"

# Drift-watch stamp (moved out of h7 for network mode). Small HA script that
# records input_text.av_desired_sound_mode_label / input_boolean.av_desired_upmix_state
# / input_datetime.av_audio_preset_at (now()) -- kept in HA so the timestamp is a
# clean {{ now() }} rather than a pyscript datetime import.
STAMP_SCRIPT = "av_stamp_desired_audio"

# Records the activity of the current network-mode switch so the central
# notifiers that have no activity context (drift-watch, h7) can name it. Written
# at the top of every av_tv_reconcile run; read by automation.av_soundbar_drift_watch.
LAST_ACTIVITY_HELPER = "input_text.av_last_activity"

# Robust soundbar-direct primitive shared with the IR path: forces the soundbar
# input to ARC and sets eq/upmix/volume with network write->verify->retry->IR
# fallback (Sofabaton dev 16) + a cold settle recheck. The engine hands the initial
# set to this on a cold/bad-state boot, where the TV/eARC channel it normally drives
# is unreliable but IR works whenever the bar has power. See ARCHITECTURE §2b/§2e.
H7_SCRIPT = "h7_soundbar_preset_native"
# The soundbar's ARC input label (media_player.lg_soundbar 'source' attribute). If
# the bar isn't on this, the TV/eARC path can't drive it (it woke on Bluetooth etc.).
SOUNDBAR_ARC_SOURCE = "ARC"

# Soundbar reachability wait before any soundbar-side write (upmix / Clear Voice).
SOUNDBAR_READY_HELPER = "input_number.h7_soundbar_ready_ceiling"
SOUNDBAR_READY_DEFAULT = 8.0

# Cold-boot gate. On a full power-off start the soundbar can take ~30 s+ to power
# on and negotiate eARC -- much longer than the TV. If the engine does its
# soundbar-directed writes (sound mode, AI upmix, volume) before then, they land
# on nothing and the soundbar later boots into its stale mode (observed
# 2026-08-29: power-off -> NLZiet, soundbar came up 32 s late in AI Sound Pro,
# upmix/mode/volume all wrong). So we wait for the soundbar to be powered AND on
# external_arc BEFORE any write. Generous, dashboard-tunable ceiling; a warm
# switch (soundbar already up) clears it on the first poll.
COLD_BOOT_CEILING_HELPER = "input_number.av_cold_boot_soundbar_ceiling_seconds"
COLD_BOOT_CEILING_DEFAULT = 45.0

# AI Sound Pro locks the AI-upmix control: while the soundbar is in that eq the
# upmix switch entity reports 'unavailable'. Switching *away* from AI Sound Pro
# (e.g. Batocera -> an upmix source) leaves a window where the TV soundMode has
# been asserted but the soundbar hasn't finished leaving AI Sound Pro yet, so the
# switch is still unavailable and a naive turn_on/off is silently dropped -- upmix
# hangs at its pre-AI-Sound value. So: wait for the switch to come back before
# writing it, then verify + retry.
UNAVAILABLE_STATES = ("unavailable", "unknown", "none", None)
# How long to wait for the switch to un-lock. Tunable from the AV Network
# dashboard (Control tile); the constant is the fallback if the helper is
# missing/blank.
UPMIX_AVAILABLE_HELPER = "input_number.av_upmix_unlock_timeout_seconds"
UPMIX_AVAILABLE_TIMEOUT = 8.0     # fallback default
UPMIX_VERIFY_RETRIES = 3          # write -> verify -> retry attempts
UPMIX_VERIFY_POLL = 0.6           # seconds between write and state re-read

SETTLE_HELPER = "input_number.av_settle_window_seconds"
DEFAULT_SETTLE_SECONDS = 12.0
POLL_SECONDS = 1.0
# The lg_soundbar_plus integration polls only every scan_interval (30 s), so on a
# cold boot HA can be blind to the physically-ready soundbar (up on ARC in ~2-5 s)
# for up to 30 s. During the reachability wait we force a fresh read this often
# instead of waiting for that poll -- the same homeassistant.update_entity trick h7
# uses. Only runs while the bar is not yet reachable (i.e. a cold boot).
SOUNDBAR_REFRESH_POLL = 2.0

# Source switch resilience. On a cold boot the webOS websocket can still be
# (re)connecting when ensure_tv_on returns, so launch_app/select_source can raise
# ConnectionClosedOK. Retry through the reconnect instead of aborting the run.
SOURCE_SWITCH_RETRIES = 4
SOURCE_SWITCH_RETRY_DELAY = 2.0
STABLE_HOLD_SECONDS = 3.0  # consider it settled after this long unchanged at desired
# Ignore a transient wrong value during cold-boot eARC negotiation: only correct
# after soundOutput has been wrong for this many consecutive polls.
WRONG_CONFIRM_POLLS = 2

# The TV's soundMode drives the soundbar's eq (confirmed live). These four map 1:1.
EQ_TO_TV_SOUNDMODE = {
    "standard": "standard",
    "ai_sound": "aiSoundPlus",
    "bass": "bassBoost",
    "custom": "customEq",
}
# Soundbar sound_mode labels (media_player.select_sound_mode + drift stamp).
EQ_TO_SOUNDBAR_LABEL = {
    "standard": "Standard",
    "bass": "Bass",
    "custom": "Custom",
    "ai_sound": "AI Sound Pro",
    "clear_voice_base": "Clear Voice (Base)",
    "clear_voice_high": "Clear Voice (High)",
}
# Clear Voice has no unambiguous TV soundMode (TV's single 'voiceEnhance' covers
# both), so it is set on the soundbar directly instead of via the TV.
EQ_SOUNDBAR_ONLY = ("clear_voice_base", "clear_voice_high")

# activity -> how to switch the TV source.
#   app_id    -> lg_webos_bsc.launch_app  (HDMI pseudo-apps + native apps by id)
#   app_title -> media_player.select_source (launch an app by its list title)
PROFILES = {
    "nlziet": {"app_title": "NLZIET"},
    "youtube": {"app_title": "YouTube"},
    "kodi": {"app_id": "com.webos.app.hdmi4"},      # VRROOM output -> TV HDMI4
    "batocera": {"app_id": "com.webos.app.hdmi3"},
    "ps5": {"app_id": "com.webos.app.hdmi1"},       # direct HDMI1
    "blu_ray": {"app_id": "com.webos.app.hdmi4"},   # VRROOM port 3 -> TV HDMI4
    "xbox": {"app_id": "com.webos.app.hdmi4"},      # VRROOM port 1 -> TV HDMI4
    "shield": {"app_id": "com.webos.app.hdmi4"},    # VRROOM port 2 -> TV HDMI4
    "switch": {"app_id": "com.webos.app.hdmi4"},    # VRROOM port 2 -> TV HDMI4
    "ugoos": {"app_id": "com.webos.app.hdmi4"},     # VRROOM port 2 -> TV HDMI4
}


def _num(entity, default):
    """Read a numeric helper, falling back to default on unavailable/blank."""
    try:
        return float(state.get(entity))
    except (ValueError, TypeError):
        return default


def _sound_output():
    attrs = state.getattr(AUDIO) or {}
    return attrs.get("soundOutput")


def _headphones_active():
    return state.get(HEADPHONE_SENSOR) == "on"


def _set_tv_volume(vol_0_100):
    level = max(0.0, min(1.0, vol_0_100 / 100.0))
    service.call("media_player", "volume_set", entity_id=TV,
                 volume_level=level, blocking=True)
    return level


def _switch_source(activity, profile):
    """Switch the TV source, retrying through the cold-boot window. On a cold boot the
    webOS websocket can still be (re)connecting when ensure_tv_on returns, so the first
    launch_app/select_source can raise ConnectionClosedOK as the socket closes. Retry a
    few times (the integration reconnects within a couple of seconds); never let it
    abort the run -- a failed source switch must not take the audio reconcile down with
    it. Returns True on success."""
    for attempt in range(1, SOURCE_SWITCH_RETRIES + 1):
        try:
            if profile.get("app_id"):
                log.info("av_reconcile[%s]: launch_app %s (attempt %d)",
                         activity, profile["app_id"], attempt)
                service.call("lg_webos_bsc", "launch_app", entity_id=TV,
                             app_id=profile["app_id"], blocking=True)
            elif profile.get("app_title"):
                log.info("av_reconcile[%s]: select_source %s (attempt %d)",
                         activity, profile["app_title"], attempt)
                service.call("media_player", "select_source", entity_id=TV,
                             source=profile["app_title"], blocking=True)
            return True
        except Exception as err:
            log.warning("av_reconcile[%s]: source switch attempt %d failed: %s "
                        "(TV websocket not ready?)", activity, attempt, err)
            task.sleep(SOURCE_SWITCH_RETRY_DELAY)
    log.warning("av_reconcile[%s]: source switch FAILED after %d attempts -- continuing "
                "to audio anyway", activity, SOURCE_SWITCH_RETRIES)
    return False


def _wait_soundbar_ready(timeout):
    """Poll until the soundbar reports source+sound_mode (reachable), or timeout."""
    waited = 0.0
    while waited < timeout:
        attrs = state.getattr(SOUNDBAR) or {}
        if attrs.get("source") is not None and attrs.get("sound_mode") is not None:
            return True
        task.sleep(0.5)
        waited += 0.5
    return False


def _soundbar_powered():
    return state.get(SOUNDBAR) == "on"


def _soundbar_source():
    return (state.getattr(SOUNDBAR) or {}).get("source")


def _refresh_soundbar():
    """Force an immediate fresh read of the soundbar (the coordinator's request_all)
    instead of waiting up to its 30 s scan_interval for the next poll -- the trick h7
    uses, and why h7 sees the bar when HA's cached state is stale. Fire-and-forget:
    the refreshed state lands within the coordinator's ~1.5 s grace."""
    try:
        service.call("homeassistant", "update_entity", entity_id=SOUNDBAR, blocking=False)
    except Exception as err:
        log.warning("av_reconcile: soundbar refresh failed: %s", err)


def _wait_soundbar_reachable(timeout):
    """Poll until the soundbar is powered AND reporting an input source (the
    integration is live and can be driven), or timeout. Keys off the soundbar's own
    state, not the TV soundOutput sensor (which can go stale across a cold boot).

    A warm switch returns immediately on the already-fresh cached state. Only when
    the bar isn't reachable (a cold boot) do we force a fresh read every
    SOUNDBAR_REFRESH_POLL s -- so we see the physically-ready bar in a few seconds
    rather than waiting out the integration's 30 s scan_interval."""
    if _soundbar_powered() and _soundbar_source() is not None:
        return True
    waited = 0.0
    while waited < timeout:
        _refresh_soundbar()
        task.sleep(SOUNDBAR_REFRESH_POLL)
        waited += SOUNDBAR_REFRESH_POLL
        if _soundbar_powered() and _soundbar_source() is not None:
            return True
    return _soundbar_powered() and _soundbar_source() is not None


def _call_h7(activity, eq, upmix_on, vol):
    """Hand the initial soundbar set to the proven h7 primitive: forces the input to
    ARC and sets eq/upmix/volume with network verify->retry->IR fallback + a cold
    settle recheck. Used when the soundbar isn't already on ARC (classically a cold
    boot where it wakes on Bluetooth in its stale mode), because the TV/eARC channel
    the engine normally drives is unreliable then and IR works whenever the bar has
    power. h7 also writes the drift stamp. Returns True on success."""
    data = {"source": "arc", "upmix": upmix_on, "tv_was_cold": True}
    if eq is not None:
        data["eq"] = eq
    if vol is not None:
        data["volume"] = vol
    try:
        log.info("av_reconcile[%s]: robust h7 set -> source=arc eq=%s upmix=%s vol=%s",
                 activity, eq, upmix_on, vol)
        service.call("script", H7_SCRIPT, blocking=True, **data)
        return True
    except Exception as err:
        log.warning("av_reconcile[%s]: h7 robust set failed: %s", activity, err)
        return False


def _upmix_state():
    """Current upmix switch state ('on'/'off'), or an unavailable marker."""
    try:
        return state.get(UPMIX_SWITCH)
    except (NameError, TypeError, ValueError):
        return None


def _wait_upmix_available(timeout):
    """Poll until the upmix switch leaves 'unavailable' (soundbar out of AI Sound
    Pro), or timeout. Returns True if it came back available."""
    waited = 0.0
    while waited < timeout:
        if _upmix_state() not in UNAVAILABLE_STATES:
            return True
        task.sleep(0.5)
        waited += 0.5
    return _upmix_state() not in UNAVAILABLE_STATES


def _set_upmix(activity, upmix_on):
    """Write the upmix switch and verify it stuck, retrying through the brief
    AI-Sound-Pro unlock window. No-op (with a notify) if the switch never
    becomes available."""
    want = "on" if upmix_on else "off"
    timeout = _num(UPMIX_AVAILABLE_HELPER, UPMIX_AVAILABLE_TIMEOUT)
    # Wait out the AI-Sound-Pro lock: the switch is 'unavailable' until the
    # soundbar actually leaves that eq. Writing before then is silently dropped.
    if not _wait_upmix_available(timeout):
        log.warning("av_reconcile[%s]: upmix switch still unavailable after %.0fs "
                    "(soundbar stuck in AI Sound Pro?) -- upmix not set",
                    activity, timeout)
        return
    for attempt in range(1, UPMIX_VERIFY_RETRIES + 1):
        try:
            service.call("switch", "turn_" + want, entity_id=UPMIX_SWITCH, blocking=True)
        except Exception as err:
            log.warning("av_reconcile[%s]: upmix turn_%s failed (attempt %d): %s",
                        activity, want, attempt, err)
        task.sleep(UPMIX_VERIFY_POLL)
        cur = _upmix_state()
        if cur == want:
            log.info("av_reconcile[%s]: AI upmix -> %s (verified, attempt %d)",
                     activity, want, attempt)
            return
        # If it flipped back to unavailable, wait for it to settle before retrying.
        if cur in UNAVAILABLE_STATES:
            _wait_upmix_available(timeout)
    log.warning("av_reconcile[%s]: AI upmix did not reach %s after %d attempts "
                "(last=%s)", activity, want, UPMIX_VERIFY_RETRIES, _upmix_state())


@service
def av_tv_reconcile(activity=None, reset=False):
    """Network-mode audio controller: switch source, set TV-driven audio, hold external_arc.

    reset=True is the manual 'reset audio to source standards' mode (script.av_reset_audio):
    skip the source switch, force the TV output back to eARC up front (wakes an auto-off
    soundbar / undoes Bluetooth/TV-speaker), and ALWAYS take the soundbar-direct h7 path
    (which doesn't depend on the flaky TV websocket) rather than the TV-primary path. All
    TV-websocket calls are already try/except-guarded, so a ConnectionClosedOK is logged
    and skipped instead of aborting -- unlike a plain HA script's continue_on_error."""
    reset = str(reset).lower() in ("true", "1", "yes", "on")
    profile = PROFILES.get(activity)
    if not profile:
        log.warning("av_tv_reconcile: unknown activity %r", activity)
        return

    # Record which activity this switch is for, so the central notifiers without
    # activity context (drift-watch, h7 mismatch alerts) can name it.
    try:
        service.call("input_text", "set_value", entity_id=LAST_ACTIVITY_HELPER,
                     value=activity, blocking=True)
    except Exception as err:
        log.warning("av_reconcile[%s]: could not record last activity: %s", activity, err)

    # 1) Switch the TV source over the network (skipped in reset mode -- reset only
    #    re-asserts audio for the source that's already selected).
    if not reset:
        _switch_source(activity, profile)

    # 2) Headphones: audio is managed by the TV (no soundbar, no output force).
    #    Set the TV volume to the Bluetooth-headphone level and stop.
    if _headphones_active():
        _set_tv_volume(_num(BT_VOLUME_HELPER, BT_VOLUME_DEFAULT))
        log.info("av_reconcile[%s]: BT headphones active -> TV volume set, "
                 "soundbar left alone", activity)
        return

    # 2b) Reset mode: force the TV output back to eARC up front -- undoes a Bluetooth /
    #     TV-speaker output and sends audio to eARC, which wakes an auto-powered-off
    #     soundbar so the reachability wait below can see it. Guarded (TV websocket may
    #     be flaky); the settle loop keeps holding external_arc afterwards.
    if reset:
        try:
            service.call("lg_webos_bsc", "set_sound_output", entity_id=TV,
                         output=DESIRED_SOUND_OUTPUT, blocking=True)
            log.info("av_reconcile[%s]: reset -> forced soundOutput=%s", activity,
                     DESIRED_SOUND_OUTPUT)
        except Exception as err:
            log.warning("av_reconcile[%s]: reset eARC force failed: %s (TV websocket "
                        "not ready?)", activity, err)

    # 3) Resolve the desired soundbar eq + upmix + volume from the per-activity helpers.
    eq = state.get(SOUND_MODE_HELPER + activity)
    eq_label = EQ_TO_SOUNDBAR_LABEL.get(eq)
    upmix_on = state.get(UPMIX_HELPER + activity) == "on"
    vol = _num(VOLUME_HELPER + activity, None)

    # 4) Stamp the desired soundbar state for the drift-watch (was in h7).
    if eq_label:
        try:
            service.call("script", STAMP_SCRIPT, blocking=True,
                         sound_mode_label=eq_label, upmix=upmix_on)
        except Exception as err:
            log.warning("av_reconcile[%s]: drift stamp failed: %s", activity, err)

    # 4b) PATH SELECT: choose the audio path from the soundbar's ACTUAL input source.
    #     Wait for the soundbar to be reachable (cold boot -> ~30 s power-up, warm ->
    #     instant). The TV soundOutput sensor can go stale across a cold boot, so we
    #     key off the soundbar's own 'source': if it isn't on ARC -- classically it
    #     woke on Bluetooth in its stale mode, or hasn't come up at all -- the TV/eARC
    #     channel the engine drives is unreliable, so hand the initial set to the
    #     robust h7 primitive (forces ARC + eq/upmix/volume, network verify->retry->IR
    #     fallback + cold settle recheck). If it's already on ARC, the engine's
    #     TV-primary writes below own it.
    ceiling = _num(COLD_BOOT_CEILING_HELPER, COLD_BOOT_CEILING_DEFAULT)
    reachable = _wait_soundbar_reachable(ceiling)
    src = _soundbar_source()
    # Reset mode always takes the soundbar-direct h7 path: it sets the eq/upmix/volume
    # straight on the soundbar (network + IR), which works even when the TV/eARC channel
    # is flaky -- the whole point of a manual reset.
    needs_robust = reset or (not reachable) or (src != SOUNDBAR_ARC_SOURCE)
    if needs_robust:
        if reachable:
            log.info("av_reconcile[%s]: soundbar up on source=%s (not ARC) -> robust h7 path",
                     activity, src)
        else:
            log.warning("av_reconcile[%s]: soundbar not reachable after %.0fs -> trying h7 anyway",
                        activity, ceiling)
        if not _call_h7(activity, eq, upmix_on, vol):
            service.call(
                "persistent_notification", "create",
                title="AV: cold-boot soundbar set failed",
                message=(f"{activity}: soundbar wasn't on ARC (reachable={reachable}, "
                         f"source={src}) and the h7 recovery call failed -- audio may be "
                         "wrong. Raise the 'Cold-boot soundbar ceiling' slider if this recurs."),
                notification_id=f"av_cold_boot_{activity}",
            )
    else:
        log.info("av_reconcile[%s]: soundbar already on ARC -> TV-primary path", activity)

    # 5) Sound mode: TV root for the four mapping eqs (drives the soundbar in one
    #    shot + fixes the TV's per-input memory); soundbar-side for Clear Voice. Run
    #    on both paths -- on the robust path it reinforces (durably, at the TV root)
    #    the eq h7 just set on the soundbar.
    tv_mode = EQ_TO_TV_SOUNDMODE.get(eq)
    if tv_mode:
        try:
            service.call("lg_webos_bsc", "set_settings", entity_id=TV,
                         category="sound", settings={"soundMode": tv_mode}, blocking=True)
            log.info("av_reconcile[%s]: asserted TV soundMode=%s", activity, tv_mode)
        except Exception as err:
            log.warning("av_reconcile[%s]: TV soundMode set failed: %s (TV websocket not "
                        "ready?) -- drift-watch/h7 backstop the mode", activity, err)

    # 6) Engine soundbar-direct writes (Clear Voice eq + AI upmix) -- TV-primary path
    #    ONLY. On the robust path h7 already set eq + upmix directly on the soundbar
    #    (with its own verify/retry/IR), so re-doing them here would just race it.
    if not needs_robust:
        needs_soundbar = (eq in EQ_SOUNDBAR_ONLY) or (eq != "ai_sound")
        if needs_soundbar:
            _wait_soundbar_ready(_num(SOUNDBAR_READY_HELPER, SOUNDBAR_READY_DEFAULT))

        if eq in EQ_SOUNDBAR_ONLY and eq_label:
            try:
                service.call("media_player", "select_sound_mode", entity_id=SOUNDBAR,
                             sound_mode=eq_label, blocking=True)
                log.info("av_reconcile[%s]: set soundbar sound_mode=%s (no TV equiv)",
                         activity, eq_label)
            except Exception as err:
                log.warning("av_reconcile[%s]: soundbar sound_mode set failed: %s", activity, err)

        # AI upmix is unavailable while eq is AI Sound Pro (the mode disables it and
        # the switch entity reports 'unavailable'). When switching AWAY from AI Sound
        # Pro the switch stays unavailable until the soundbar finishes leaving that eq,
        # so _set_upmix waits for it to come back, then writes + verifies + retries.
        if eq != "ai_sound":
            _set_upmix(activity, upmix_on)

    # 7) Volume: read the target now, but apply it inside the settle loop below,
    #    the first time external_arc is confirmed up. On a warm switch that's the
    #    very first poll (instant); on a cold boot it's after the eARC handshake,
    #    so the TV can't re-apply its remembered eARC volume over ours. Set ONCE
    #    on the TV (which drives the soundbar), then it's the user's -- nothing
    #    re-asserts it (the TV integration still reports it live). On the robust path
    #    h7 already set the soundbar volume; this still applies the eARC-authoritative
    #    TV volume once external_arc is confirmed (same target, so they converge).
    vol_applied = False

    # 8) Assert + hold the desired sound output for the settle window.
    try:
        settle = float(state.get(SETTLE_HELPER))
    except (ValueError, TypeError):
        settle = DEFAULT_SETTLE_SECONDS

    iters = max(1, int(settle / POLL_SECONDS))
    stable_needed = max(1, int(STABLE_HOLD_SECONDS / POLL_SECONDS))
    stable = 0
    wrong_streak = 0
    corrections = 0

    for _ in range(iters):
        if _headphones_active():
            log.info("av_reconcile[%s]: headphones connected mid-settle -> stop", activity)
            return
        cur = _sound_output()
        if cur == DESIRED_SOUND_OUTPUT:
            if vol is not None and not vol_applied:
                try:
                    level = _set_tv_volume(vol)
                    vol_applied = True
                    log.info("av_reconcile[%s]: TV volume set to %.0f (%.2f) on external_arc, "
                             "user-adjustable", activity, vol, level)
                except Exception as err:
                    log.warning("av_reconcile[%s]: TV volume set failed: %s (retry next poll)",
                                activity, err)
            wrong_streak = 0
            stable += 1
            if stable >= stable_needed:
                log.info("av_reconcile[%s]: settled on %s after %d correction(s)",
                         activity, DESIRED_SOUND_OUTPUT, corrections)
                return
        else:
            stable = 0
            wrong_streak += 1
            # Debounce: only correct once it's *confirmed* wrong, not on a transient.
            if wrong_streak >= WRONG_CONFIRM_POLLS:
                log.info("av_reconcile[%s]: soundOutput=%s for %d polls, correcting to %s",
                         activity, cur, wrong_streak, DESIRED_SOUND_OUTPUT)
                try:
                    service.call("lg_webos_bsc", "set_sound_output", entity_id=TV,
                                 output=DESIRED_SOUND_OUTPUT, blocking=True)
                    corrections += 1
                except Exception as err:
                    log.warning("av_reconcile[%s]: set_sound_output failed: %s (retry next poll)",
                                activity, err)
                wrong_streak = 0
        task.sleep(POLL_SECONDS)

    # Window expired without a stable settle -> notify only (per decision 4).
    cur = _sound_output()
    if cur != DESIRED_SOUND_OUTPUT:
        service.call(
            "persistent_notification", "create",
            title="AV: TV overrode audio output",
            message=(f"{activity}: soundOutput is '{cur}', wanted "
                     f"'{DESIRED_SOUND_OUTPUT}' after {settle:.0f}s "
                     f"({corrections} correction(s) attempted)."),
            notification_id=f"av_audio_override_{activity}",
        )
