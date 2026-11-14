#!/usr/bin/env python3
"""
bscpylgtv_055_probe.py -- are the two new bscpylgtv 0.5.5 calls useful on webOS 26?

0.5.5 (2026-09-30) added:
  * get_current_system_settings() -> SSAP settings/currentSystemSettings
  * toggle_screen(enable)         -> com.webos.service.panelcontroller/setScreenOnOff

The interesting one is the READ: getSystemSettings 500s on this firmware for
gameGenre and for sharpness/oled_light, so those entities are optimistic. If
currentSystemSettings returns them, we can make them real read-back entities.

RESULT (ran 2026-10-02, webOS 26 / OLED83G67LW) -- 0.5.5 gives us nothing, STAY ON 0.5.4
-------------------------------------------------------------------------------------
currentSystemSettings does NOT return the settings we need:
  * It ignores the `category` parameter -- all four calls returned the same payload.
  * Its only actual TV setting is soundOutput: external_arc, which get_sound_output()
    already gives us. gameGenre / sharpness / oled_light are NOT in it.
  * The baseline getSystemSettings(other, [gameGenre]) still fails, now with
    "Some keys are not allowed for the request ( gameGenre )" -- the TV refuses that
    key for our pairing key by design. So those entities stay write-only (Luna-alert
    bridge remains the only way to set them). Pin stays at bscpylgtv==0.5.4.
Possible nice-to-have (not implemented): the raw response also carries panel usage
time (~2099, units unconfirmed) and the last 15 power on/off reasons (e.g.
OFF_BY_PNWASHDONE) -- candidates for diagnostic sensors, useful for the TV
dead-state / unexpected-power investigation. --toggle-screen was not run (the
Screen on/off buttons work fine).

This probe calls the raw endpoints via client.request(), so it works with the
bscpylgtv 0.5.4 the integration currently pins -- no upgrade needed to test.

By default it changes nothing on the TV. --toggle-screen additionally blanks the
panel for ~3 s via setScreenOnOff and turns it back on.

USAGE (HA Terminal add-on)
--------------------------
    pip install --upgrade "aiowebostv>=0.9.2" "bscpylgtv==0.5.4"
    python3 bscpylgtv_055_probe.py 192.168.1.245 --key <CLIENT_KEY> [--toggle-screen]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from bscpylgtv import WebOsClient
from aiowebostv.handshake import REGISTRATION_PAYLOAD

CANONICAL_MANIFEST = dict(REGISTRATION_PAYLOAD["manifest"])
CURRENT_SYSTEM_SETTINGS = "settings/currentSystemSettings"
TOGGLE_SCREEN = "com.webos.service.panelcontroller/setScreenOnOff"


class _MemStore:
    def __init__(self, ip, key):
        self._d = {ip: key} if key else {}

    async def get_key(self, key):
        return self._d.get(key)

    async def set_key(self, key, val):
        self._d[key] = val

    async def list_keys(self):
        return dict(self._d)


def show(label: str, value, limit: int = 4000) -> None:
    try:
        rendered = json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        rendered = repr(value)
    print(f"--- {label}\n{rendered[:limit]}\n")


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host")
    p.add_argument("--key", required=True, help="Existing client key the integration paired")
    p.add_argument("--toggle-screen", action="store_true", help="Also test setScreenOnOff (blanks panel ~3 s)")
    args = p.parse_args()

    client = WebOsClient(
        args.host,
        client_key=args.key,
        key_file_path=None,
        storage=_MemStore(args.host, args.key),
        states=[],
        timeout_connect=3,
        connect_retry_attempts=3,
    )
    client.manifest = CANONICAL_MANIFEST
    await client.async_init()
    try:
        await client.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to connect: {type(exc).__name__}: {exc}")
        return 1

    async def probe(label, coro):
        try:
            show(label, await coro)
        except Exception as exc:  # noqa: BLE001
            show(label, f"ERROR {type(exc).__name__}: {str(exc)[:200]}")

    await probe("currentSystemSettings {}", client.request(CURRENT_SYSTEM_SETTINGS, {}))
    # Some SSAP settings endpoints filter by category; try the ones we care about.
    for cat in ("picture", "sound", "other"):
        await probe(
            f"currentSystemSettings category={cat}",
            client.request(CURRENT_SYSTEM_SETTINGS, {"category": cat}),
        )
    # Baseline for comparison: the call that 500s today.
    await probe(
        "getSystemSettings other/gameGenre (baseline)",
        client.get_system_settings("other", ["gameGenre"]),
    )

    if args.toggle_screen:
        await probe("setScreenOnOff OnOff=false", client.request(TOGGLE_SCREEN, {"OnOff": False}))
        await asyncio.sleep(3)
        await probe("setScreenOnOff OnOff=true", client.request(TOGGLE_SCREEN, {"OnOff": True}))
        await probe("get_power_state after", client.get_power_state())

    try:
        await client.disconnect()
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
