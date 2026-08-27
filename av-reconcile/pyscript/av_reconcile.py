"""
AV reconcile engine (pyscript) -- LG webOS TV audio-output reconciliation.

Scope (deliberately narrow): after an Activity switches the TV source, the TV
sometimes overrides the audio path (the §8 drift in AV_Control_Handover.md, now
observable via the bscpylgtv integration's audio sensor). This engine switches
the TV to the Activity's source and then, for a short *settle window*, holds the
TV's sound output at the desired value (external_arc), correcting it if the TV
flips it away. It does NOT do power (that stays IR/Sofabaton -- WOL doesn't work
here) and it does NOT touch the soundbar (that stays on the proven native
h7_soundbar_preset_native path in the Activity script).

Bluetooth-headphones guard: headphones are only ever selected mid-Activity, never
at startup. If the TV's sound output is already a Bluetooth-headphone output when
this runs (or becomes one during the settle window), the engine leaves the audio
entirely to the TV -- no output force, and the caller skips the soundbar preset.

Exposed service: pyscript.av_tv_reconcile(activity=<name>)

Deploy: copy this file to <config>/pyscript/av_reconcile.py (HACS 'pyscript'
integration installed). It is data-only until an Activity script calls it.
"""

# --- configuration (entity ids confirmed live 2026-08-24) --------------------
TV = "media_player.lg_webos_tv_oled83g67lw"
AUDIO = "sensor.lg_webos_tv_oled83g67lw_audio_settings"  # has the soundOutput attribute

DESIRED_SOUND_OUTPUT = "external_arc"
# Headphone detection is centralised in this template binary_sensor (it also
# disambiguates the ambiguous 'bt_soundbar' output from the real soundbar by
# checking the soundbar isn't the Bluetooth sink). When on: audio is managed by
# the TV, not the soundbar (which auto-powers-off with no eARC input).
HEADPHONE_SENSOR = "binary_sensor.tv_bluetooth_headphones"
# Bluetooth headphones want their own volume scale (default ~50, vs soundbar 10-20).
BT_VOLUME_HELPER = "input_number.av_bluetooth_headphone_volume"
BT_VOLUME_DEFAULT = 50.0

SETTLE_HELPER = "input_number.av_settle_window_seconds"
DEFAULT_SETTLE_SECONDS = 12.0
POLL_SECONDS = 1.0
STABLE_HOLD_SECONDS = 3.0  # consider it settled after this long unchanged at desired
# Ignore a transient wrong value during cold-boot eARC negotiation: only correct
# after soundOutput has been wrong for this many consecutive polls. Avoids an
# unnecessary set_sound_output (which itself causes an eARC re-handshake / brief
# black) when the TV settles to external_arc on its own within a second.
WRONG_CONFIRM_POLLS = 2

# Root-level sound-mode control: the TV's soundMode drives the soundbar's eq
# (confirmed live -- setting the TV mode changes the soundbar in one shot). For
# the modes with a TV equivalent, assert the TV mode after the input switch so
# the TV's per-input memory is correct and it's less likely to drift the soundbar
# afterwards. The per-input desired eq lives in input_select.av_sound_mode_<activity>.
SOUND_MODE_HELPER = "input_select.av_sound_mode_"
# Official TV supportSoundMode values (bscpylgtv G6 docs). Only the 1:1 mappings
# are asserted at the root: standard/aiSoundPlus are confirmed live; bass/custom
# are 1:1 per the docs. Clear Voice is deliberately excluded -- the TV has a single
# 'voiceEnhance' for the soundbar's two Clear Voice modes, so a TV assert can't
# target the exact one and would override the precise soundbar setting; those stay
# soundbar-only via the h7 preset + the drift-watch's soundbar-side branch.
EQ_TO_TV_SOUNDMODE = {
    "standard": "standard",
    "ai_sound": "aiSoundPlus",
    "bass": "bassBoost",
    "custom": "customEq",
}

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


def _sound_output():
    attrs = state.getattr(AUDIO) or {}
    return attrs.get("soundOutput")


def _headphones_active():
    return state.get(HEADPHONE_SENSOR) == "on"


def _switch_source(activity, profile):
    if profile.get("app_id"):
        log.info("av_reconcile[%s]: launch_app %s", activity, profile["app_id"])
        service.call("lg_webos_bsc", "launch_app", entity_id=TV,
                     app_id=profile["app_id"], blocking=True)
    elif profile.get("app_title"):
        log.info("av_reconcile[%s]: select_source %s", activity, profile["app_title"])
        service.call("media_player", "select_source", entity_id=TV,
                     source=profile["app_title"], blocking=True)


@service
def av_tv_reconcile(activity=None):
    """Switch the TV to the Activity source and hold external_arc for a settle window."""
    profile = PROFILES.get(activity)
    if not profile:
        log.warning("av_tv_reconcile: unknown activity %r", activity)
        return

    # 1) Switch the TV source over the network.
    _switch_source(activity, profile)

    # 2) Headphones: audio is managed by the TV (no soundbar, no output force, no
    #    upmix). Set the TV volume to the Bluetooth-headphone level and stop.
    if _headphones_active():
        try:
            bt_vol = float(state.get(BT_VOLUME_HELPER))
        except (ValueError, TypeError):
            bt_vol = BT_VOLUME_DEFAULT
        service.call("media_player", "volume_set", entity_id=TV,
                     volume_level=max(0.0, min(1.0, bt_vol / 100.0)), blocking=True)
        log.info("av_reconcile[%s]: BT headphones active -> TV volume %.0f, "
                 "soundbar left alone", activity, bt_vol)
        return

    # 3) Attack the §8 drift at the root: assert the TV sound mode (which drives
    #    the soundbar) for the modes that map. Also fixes the TV's per-input
    #    memory so it's less likely to re-drift. Non-mapping eqs (bass/custom/
    #    clear_voice) stay soundbar-only via the h7 preset + drift-watch.
    tv_mode = EQ_TO_TV_SOUNDMODE.get(state.get(SOUND_MODE_HELPER + activity))
    if tv_mode:
        service.call("lg_webos_bsc", "set_settings", entity_id=TV,
                     category="sound", settings={"soundMode": tv_mode}, blocking=True)
        log.info("av_reconcile[%s]: asserted TV soundMode=%s", activity, tv_mode)

    # 4) Assert + hold the desired sound output for the settle window.
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

    # 4) Window expired without a stable settle -> notify only (per decision 4).
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
