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
# TV-only Bluetooth-headphone outputs -> suspend all audio reconciliation.
HEADPHONE_OUTPUTS = {"bt_headset", "bt_headphone", "bt_headphones", "headphone"}

SETTLE_HELPER = "input_number.av_settle_window_seconds"
DEFAULT_SETTLE_SECONDS = 12.0
POLL_SECONDS = 1.0
STABLE_HOLD_SECONDS = 3.0  # consider it settled after this long unchanged at desired
# Ignore a transient wrong value during cold-boot eARC negotiation: only correct
# after soundOutput has been wrong for this many consecutive polls. Avoids an
# unnecessary set_sound_output (which itself causes an eARC re-handshake / brief
# black) when the TV settles to external_arc on its own within a second.
WRONG_CONFIRM_POLLS = 2

# activity -> how to switch the TV source.
#   app_id    -> lg_webos_bsc.launch_app  (HDMI pseudo-apps + native apps by id)
#   app_title -> media_player.select_source (launch an app by its list title)
PROFILES = {
    "nlziet": {"app_title": "NLZIET"},
    "kodi": {"app_id": "com.webos.app.hdmi4"},
    "batocera": {"app_id": "com.webos.app.hdmi3"},
}


def _sound_output():
    attrs = state.getattr(AUDIO) or {}
    return attrs.get("soundOutput")


def _is_headphones(value):
    return value in HEADPHONE_OUTPUTS


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

    # 2) Headphones guard -- leave all audio to the TV.
    if _is_headphones(_sound_output()):
        log.info("av_reconcile[%s]: BT headphones active -> leaving audio to the TV", activity)
        return

    # 3) Assert + hold the desired sound output for the settle window.
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
        cur = _sound_output()
        if _is_headphones(cur):
            log.info("av_reconcile[%s]: headphones connected mid-settle -> stop", activity)
            return
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
