"""Constants for the LG webOS (bscpylgtv) integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "lg_webos_bsc"

# Config entry / config flow keys
CONF_HOST = "host"
CONF_MAC = "mac"
CONF_NAME = "name"
CONF_CLIENT_KEY = "client_key"
CONF_PAIR_MODE = "pair_mode"

# Options
CONF_POLL_INTERVAL = "poll_interval"
CONF_ENABLE_INPUT_SWITCHING = "enable_input_switching"
CONF_WOL_BROADCAST = "wol_broadcast"

# Pairing modes offered by the config flow (see handover sec.3a)
PAIR_MODE_EXISTING = "existing_key"   # paste a known-good (grandfathered) key
PAIR_MODE_FRESH = "fresh"             # PROMPT-pair on the TV screen

# Defaults
DEFAULT_NAME = "LG webOS TV"
DEFAULT_POLL_INTERVAL = 10  # seconds
DEFAULT_ENABLE_INPUT_SWITCHING = False  # handover sec.8: soundbar eARC/DAFC drift risk
MIN_POLL_INTERVAL = 5
MAX_POLL_INTERVAL = 120

SCAN_INTERVAL = timedelta(seconds=DEFAULT_POLL_INTERVAL)

# Connect-retry envelope, mirrors lg_webos_net.py connect_with_retry (~90s / ~0.3s).
CONNECT_WAIT_SECONDS = 90.0
CONNECT_RETRY_INTERVAL = 0.3
# Shorter, non-blocking envelope used inside the config-flow validation step so the
# UI does not hang for 90s on an unreachable TV.
CONFIG_FLOW_CONNECT_WAIT = 12.0

# Game Optimizer genre (setSystemSettings category "other", key "gameGenre").
# Standard + FPS are CONFIRMED live; the rest are the LG webOS 26 genre names and
# are UNCONFIRMED on this TV -- verify against the on-screen Game Optimizer menu.
GAME_GENRE_KEY = "gameGenre"
GAME_GENRE_CATEGORY = "other"
GAME_GENRE_OPTIONS = [
    "Standard",   # CONFIRMED
    "FPS",        # CONFIRMED
    "RTS",
    "RPG",
    "Sports",
]

# Picture mode (set_system_picture_mode, webOS v9+). These are the raw API
# values. Valid options depend on the current content (some HDR/Dolby modes only
# apply to HDR sources), so setting one may fail depending on what's playing --
# surfaced as an error rather than silently. UNCONFIRMED on this TV; SDR core set.
PICTURE_MODE_OPTIONS = [
    "standard",
    "vivid",
    "eco",
    "cinema",
    "sports",
    "game",
    "filmMaker",
    "expert1",
    "expert2",
]

# Picture number sliders. Only the 4 keys below read back on this firmware --
# get_picture_settings with sharpness/oled_light 500s the whole batch, so we read
# exactly these (they are also bscpylgtv's get_picture_settings default keys).
# Writes go through set_settings("picture", {key: value}) via the alert bridge.
PICTURE_CATEGORY = "picture"
PICTURE_READ_KEYS = ["contrast", "backlight", "brightness", "color"]
# key -> (min, max) for the number entities.
PICTURE_NUMBERS = {
    "backlight": (0, 100),
    "contrast": (0, 100),
    "brightness": (0, 100),
    "color": (0, 100),
}

# App ids that represent physical inputs rather than launchable apps; used to
# split the media_player source list sensibly.
HDMI_APP_PREFIX = "com.webos.app.hdmi"

# Sound output (audio) -> friendly label. webOS reports "external_arc" for both
# ARC and eARC. Unknown values fall through to the raw string.
SOUND_OUTPUT_NAMES = {
    "tv_speaker": "TV Speaker",
    "external_speaker": "External Speaker (optical/wired)",
    "external_optical": "Optical",
    "external_arc": "HDMI ARC / eARC",
    "lineout": "Line Out",
    "headphone": "Headphones",
    "tv_speaker_headphone": "TV Speaker + Headphones",
    "tv_external_speaker": "TV Speaker + Optical",
    "bt_soundbar": "Bluetooth Soundbar",
    "bt_headset": "Bluetooth Headset",
    "soundbar": "Soundbar",
}
