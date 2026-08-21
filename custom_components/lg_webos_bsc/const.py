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

# App ids that represent physical inputs rather than launchable apps; used to
# split the media_player source list sensibly.
HDMI_APP_PREFIX = "com.webos.app.hdmi"
