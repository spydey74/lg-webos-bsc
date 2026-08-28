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

# Soundbar reachability wait before any soundbar-side write (upmix / Clear Voice).
SOUNDBAR_READY_HELPER = "input_number.h7_soundbar_ready_ceiling"
SOUNDBAR_READY_DEFAULT = 8.0

SETTLE_HELPER = "input_number.av_settle_window_seconds"
DEFAULT_SETTLE_SECONDS = 12.0
POLL_SECONDS = 1.0
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
    if profile.get("app_id"):
        log.info("av_reconcile[%s]: launch_app %s", activity, profile["app_id"])
        service.call("lg_webos_bsc", "launch_app", entity_id=TV,
                     app_id=profile["app_id"], blocking=True)
    elif profile.get("app_title"):
        log.info("av_reconcile[%s]: select_source %s", activity, profile["app_title"])
        service.call("media_player", "select_source", entity_id=TV,
                     source=profile["app_title"], blocking=True)


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


@service
def av_tv_reconcile(activity=None):
    """Network-mode audio controller: switch source, set TV-driven audio, hold external_arc."""
    profile = PROFILES.get(activity)
    if not profile:
        log.warning("av_tv_reconcile: unknown activity %r", activity)
        return

    # 1) Switch the TV source over the network.
    _switch_source(activity, profile)

    # 2) Headphones: audio is managed by the TV (no soundbar, no output force).
    #    Set the TV volume to the Bluetooth-headphone level and stop.
    if _headphones_active():
        _set_tv_volume(_num(BT_VOLUME_HELPER, BT_VOLUME_DEFAULT))
        log.info("av_reconcile[%s]: BT headphones active -> TV volume set, "
                 "soundbar left alone", activity)
        return

    # 3) Resolve the desired soundbar eq + upmix from the per-activity helpers.
    eq = state.get(SOUND_MODE_HELPER + activity)
    eq_label = EQ_TO_SOUNDBAR_LABEL.get(eq)
    upmix_on = state.get(UPMIX_HELPER + activity) == "on"

    # 4) Stamp the desired soundbar state for the drift-watch (was in h7).
    if eq_label:
        try:
            service.call("script", STAMP_SCRIPT, blocking=True,
                         sound_mode_label=eq_label, upmix=upmix_on)
        except Exception as err:
            log.warning("av_reconcile[%s]: drift stamp failed: %s", activity, err)

    # 5) Sound mode: TV root for the four mapping eqs (drives the soundbar in one
    #    shot + fixes the TV's per-input memory); soundbar-side for Clear Voice.
    tv_mode = EQ_TO_TV_SOUNDMODE.get(eq)
    if tv_mode:
        service.call("lg_webos_bsc", "set_settings", entity_id=TV,
                     category="sound", settings={"soundMode": tv_mode}, blocking=True)
        log.info("av_reconcile[%s]: asserted TV soundMode=%s", activity, tv_mode)

    # 6) Soundbar-only writes (Clear Voice eq + AI upmix). Wait for the soundbar to
    #    be reachable first; guard each so a not-yet-ready bar can't abort the run
    #    (the drift-watch backstops the mode afterwards).
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

    # AI upmix is unavailable while eq is AI Sound Pro (the mode disables it).
    if eq != "ai_sound":
        try:
            if upmix_on:
                service.call("switch", "turn_on", entity_id=UPMIX_SWITCH, blocking=True)
            else:
                service.call("switch", "turn_off", entity_id=UPMIX_SWITCH, blocking=True)
            log.info("av_reconcile[%s]: AI upmix -> %s", activity, upmix_on)
        except Exception as err:
            log.warning("av_reconcile[%s]: upmix set failed: %s", activity, err)

    # 7) Volume: read the target now, but apply it inside the settle loop below,
    #    the first time external_arc is confirmed up. On a warm switch that's the
    #    very first poll (instant); on a cold boot it's after the eARC handshake,
    #    so the TV can't re-apply its remembered eARC volume over ours. Set ONCE
    #    on the TV (which drives the soundbar), then it's the user's -- nothing
    #    re-asserts it (the TV integration still reports it live).
    vol = _num(VOLUME_HELPER + activity, None)
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
                level = _set_tv_volume(vol)
                vol_applied = True
                log.info("av_reconcile[%s]: TV volume set to %.0f (%.2f) on external_arc, "
                         "user-adjustable", activity, vol, level)
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
                service.call("lg_webos_bsc", "set_sound_output", entity_id=TV,
                             output=DESIRED_SOUND_OUTPUT, blocking=True)
                corrections += 1
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
