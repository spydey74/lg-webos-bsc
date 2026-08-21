"""Runtime manifest replacement for bscpylgtv (handover sec.2, Layer-1 fix).

bscpylgtv still ships the blacklisted `com.lge.test` + `signatures` manifest in
`bscpylgtv/manifest.py`. webOS 26 rejects it on EVERY register with
`403 blacklisted certificate`, even with a valid key -- the firmware re-checks
the manifest on each reconnect. The fix is to present aiowebostv's canonical
webOS 26 manifest (PR #719 / commit 2eff7398, shipped in aiowebostv >= 0.9.2):
no `signatures`, no `signed`, a single top-level `permissions` list including
LAUNCH and WRITE_SETTINGS, `appVersion` "1.1".

We do NOT fork bscpylgtv and we do NOT hand-maintain the permission list.
`bscpylgtv.WebOsClient.__init__` sets `self.manifest = MANIFEST` and
`registration_msg()` reads `self.manifest`, so the coordinator just assigns
`client.manifest = CANONICAL_MANIFEST` before `connect()`. Source of truth is
aiowebostv, so we inherit any future permission additions for free.

The vendored fallback below is only used if the import fails or the installed
aiowebostv predates #719; pin `aiowebostv>=0.9.2` in manifest.json so the import
path is the one that actually runs.
"""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)


def _is_canonical(manifest: dict) -> bool:
    """True only for the post-#719 canonical manifest shape."""
    return (
        isinstance(manifest, dict)
        and "signed" not in manifest
        and "signatures" not in manifest
        and isinstance(manifest.get("permissions"), list)
        and "LAUNCH" in manifest["permissions"]
        and "WRITE_SETTINGS" in manifest["permissions"]
    )


# Preferred path: reuse aiowebostv's maintained manifest verbatim.
try:
    from aiowebostv.handshake import REGISTRATION_PAYLOAD as _AIO

    _CANDIDATE = dict(_AIO["manifest"])
    if _is_canonical(_CANDIDATE):
        CANONICAL_MANIFEST: dict = _CANDIDATE
        _MANIFEST_SOURCE = "aiowebostv.handshake.REGISTRATION_PAYLOAD"
    else:  # installed aiowebostv predates #719 -> fall through to the vendored copy
        raise ValueError("installed aiowebostv manifest is not the post-#719 shape")
except Exception as exc:  # noqa: BLE001 -- any import/shape problem falls back
    _LOGGER.warning(
        "Falling back to vendored webOS 26 manifest (%s); pin aiowebostv>=0.9.2 "
        "to track upstream instead",
        exc,
    )
    # Verbatim copy of aiowebostv 0.9.2 REGISTRATION_PAYLOAD["manifest"] (post-#719).
    CANONICAL_MANIFEST = {
        "appVersion": "1.1",
        "manifestVersion": 1,
        "permissions": [
            "APP_TO_APP",
            "CLOSE",
            "CONTROL_AUDIO",
            "CONTROL_DISPLAY",
            "CONTROL_INPUT_JOYSTICK",
            "CONTROL_INPUT_MEDIA_PLAYBACK",
            "CONTROL_INPUT_MEDIA_RECORDING",
            "CONTROL_INPUT_TEXT",
            "CONTROL_INPUT_TV",
            "CONTROL_MOUSE_AND_KEYBOARD",
            "CONTROL_POWER",
            "CONTROL_TV_SCREEN",
            "LAUNCH",
            "LAUNCH_WEBAPP",
            "READ_APP_STATUS",
            "READ_COUNTRY_INFO",
            "READ_CURRENT_CHANNEL",
            "READ_INPUT_DEVICE_LIST",
            "READ_INSTALLED_APPS",
            "READ_LGE_SDX",
            "READ_LGE_TV_INPUT_EVENTS",
            "READ_NETWORK_STATE",
            "READ_NOTIFICATIONS",
            "READ_POWER_STATE",
            "READ_RUNNING_APPS",
            "READ_SETTINGS",
            "READ_TV_CHANNEL_LIST",
            "READ_TV_CURRENT_TIME",
            "READ_UPDATE_INFO",
            "SEARCH",
            "TEST_OPEN",
            "TEST_PROTECTED",
            "TEST_SECURE",
            "UPDATE_FROM_REMOTE_APP",
            "WRITE_NOTIFICATION_ALERT",
            "WRITE_NOTIFICATION_TOAST",
            "WRITE_SETTINGS",
        ],
    }
    _MANIFEST_SOURCE = "vendored (aiowebostv 0.9.2 copy)"


def apply_manifest(client) -> None:
    """Set the canonical manifest on a bscpylgtv WebOsClient before connect().

    Must be called on every (re)connect: the firmware re-checks the manifest on
    each register, so a stock manifest 403s regardless of a held key.
    """
    client.manifest = CANONICAL_MANIFEST
    _LOGGER.debug(
        "Applied canonical webOS 26 manifest (appVersion %s, %d permissions) from %s",
        CANONICAL_MANIFEST.get("appVersion"),
        len(CANONICAL_MANIFEST.get("permissions", [])),
        _MANIFEST_SOURCE,
    )
