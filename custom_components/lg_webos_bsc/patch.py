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


# --------------------------------------------------------------------------- #
# Keepalive patch (source-level fix for the bscpylgtv half-open-socket hang).  #
# --------------------------------------------------------------------------- #
#
# bscpylgtv opens BOTH its sockets (control + input) with
# `websockets.connect(..., ping_interval=None, ...)` -- keepalive OFF -- and its
# receive loop has no read timeout. `is_connected()` is just
# `connect_task is not None and not connect_task.done()`. So a *half-open* TCP socket
# (silently dead, no close frame -- what webOS leaves after a cold-boot handshake or a
# network blip) wedges the recv loop forever: connect_task never finishes,
# is_connected() keeps reporting True, and command futures never resolve -> commands
# hang indefinitely. On bscpylgtv 0.5.2 this self-healed *by accident* (a teardown
# TypeError completed the task); 0.5.4's PR #8 removed that crash, so on HA 2026.09 /
# Python 3.14 the wedge became permanent (the "0.5.4 made hangs worse" regression).
#
# The correct source-level fix is to re-enable websockets keepalive, which bscpylgtv
# never should have disabled: websockets then PINGs the TV, and a dead socket that
# doesn't PONG within ping_timeout raises ConnectionClosed in the recv loop -> the task
# completes -> is_connected() goes False -> the coordinator reconnects on the next poll.
# This restores (deterministically) the same auto-recovery 0.5.2 gave by accident.
#
# We do NOT fork bscpylgtv. bscpylgtv.webos_client does `import websockets` then calls
# `websockets.connect(...)`, so we replace that module's `websockets` reference with a
# thin proxy that injects keepalive whenever ping_interval is explicitly None, and
# passes everything else straight through. Scoped to bscpylgtv only -- HA core, Kodi,
# etc. keep the stock websockets. Generous, forgiving values so a healthy-but-briefly-
# quiet TV is never dropped: a PING every 30 s, and 20 s of grace for the PONG (a real
# TV answers in milliseconds; only a genuinely dead socket misses it). The coordinator's
# wait_for ceilings remain as a backstop if a firmware ever ignores protocol PINGs.
_KEEPALIVE_PING_INTERVAL = 30.0
_KEEPALIVE_PING_TIMEOUT = 20.0

_keepalive_patched = False


class _WebsocketsKeepaliveShim:
    """Proxy for the `websockets` module that forces keepalive on bscpylgtv's connects.

    Only `connect()` is intercepted, and only when the caller explicitly passed
    ping_interval=None (which is exactly what bscpylgtv does). Every other attribute
    access (exceptions, connection classes, ...) proxies to the real module unchanged.
    """

    def __init__(self, real) -> None:
        # Store on __dict__ directly so __getattr__ can't recurse resolving `_real`.
        self.__dict__["_real"] = real

    def connect(self, *args, **kwargs):
        # `False` sentinel distinguishes "explicitly None" (patch it) from "absent"
        # (leave whatever default the caller/library intends).
        if kwargs.get("ping_interval", False) is None:
            kwargs["ping_interval"] = _KEEPALIVE_PING_INTERVAL
            kwargs.setdefault("ping_timeout", _KEEPALIVE_PING_TIMEOUT)
        return self._real.connect(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def patch_bscpylgtv_keepalive() -> None:
    """Install the keepalive shim over bscpylgtv.webos_client.websockets (idempotent).

    Safe to call on every setup; wraps the real module exactly once. On any failure we
    log and carry on -- the coordinator's timeouts still bound the failure, just reacting
    after a poll cycle instead of preventing the wedge outright.
    """
    global _keepalive_patched
    if _keepalive_patched:
        return
    try:
        from bscpylgtv import webos_client

        current = getattr(webos_client, "websockets", None)
        if isinstance(current, _WebsocketsKeepaliveShim):
            _keepalive_patched = True
            return
        webos_client.websockets = _WebsocketsKeepaliveShim(current)
        _keepalive_patched = True
        _LOGGER.info(
            "Patched bscpylgtv websockets keepalive (ping_interval=%.0fs, "
            "ping_timeout=%.0fs) -- half-open sockets now self-heal at the source",
            _KEEPALIVE_PING_INTERVAL, _KEEPALIVE_PING_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 -- never block setup on the patch
        _LOGGER.warning(
            "Could not patch bscpylgtv keepalive (%s); falling back to the "
            "coordinator's wait_for ceilings", exc,
        )
