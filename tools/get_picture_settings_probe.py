#!/usr/bin/env python3
"""
get_picture_settings_probe.py -- does webOS 26 let us READ settings?

Phase-2 gate: the picture `number` sliders (backlight/contrast/brightness/...)
and the model/software sensors are only worth building if the TV actually
answers the read calls. On this firmware getSystemSettings returned 500 for the
'other'/gameGenre category (writes still work via the alert bridge), so reads are
UNCONFIRMED and must be tested per-category.

This probe uses the EXACT path the integration uses -- bscpylgtv with the
canonical manifest patched in and states=[] -- then calls the read getters and
prints what comes back. Whatever this prints is what the integration can show.

It changes nothing on the TV (reads only).

REQUIREMENTS (run in the HA Terminal add-on)
--------------------------------------------
    pip install --upgrade "aiowebostv>=0.9.2" "bscpylgtv==0.5.2"

USAGE
-----
    python3 get_picture_settings_probe.py 192.168.1.245 --key <YOUR_CLIENT_KEY>

(Use the key the integration paired, e.g. the one from the decision-gate probe.)

Exit 0 if it connected, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

try:
    from bscpylgtv import WebOsClient
except ImportError:
    print('ERROR: bscpylgtv is required. Install:\n    pip install "bscpylgtv==0.5.2"')
    raise SystemExit(1)

try:
    from aiowebostv.handshake import REGISTRATION_PAYLOAD
    CANONICAL_MANIFEST = dict(REGISTRATION_PAYLOAD["manifest"])
except Exception:  # noqa: BLE001
    print('ERROR: aiowebostv>=0.9.2 is required for the canonical manifest.\n'
          '    pip install --upgrade "aiowebostv>=0.9.2"')
    raise SystemExit(1)


class _MemStore:
    def __init__(self, ip, key):
        self._d = {ip: key} if key else {}

    async def get_key(self, key):
        return self._d.get(key)

    async def set_key(self, key, val):
        self._d[key] = val

    async def list_keys(self):
        return dict(self._d)


def show(label: str, value) -> None:
    try:
        rendered = json.dumps(value, ensure_ascii=False)
    except TypeError:
        rendered = repr(value)
    print(f"  {label:38} -> {rendered[:400]}")


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host")
    p.add_argument("--key", required=True, help="Existing client key the integration paired")
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

    print(f"Connecting to {args.host} ...")
    try:
        await client.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to connect: {type(exc).__name__}: {exc}")
        return 1
    print("Connected + authenticated.\n")

    print("READS (this is exactly what the integration can display):")

    async def probe(label, coro):
        try:
            show(label, await coro)
        except Exception as exc:  # noqa: BLE001
            show(label, f"ERROR {type(exc).__name__}: {str(exc)[:120]}")

    # The phase-2 picture number sliders depend on this one:
    await probe(
        "get_picture_settings(default keys)",
        client.get_picture_settings(),
    )
    await probe(
        "get_picture_settings(extended keys)",
        client.get_picture_settings(
            keys=["backlight", "contrast", "brightness", "color", "sharpness", "oled_light"]
        ),
    )
    # Sensors:
    await probe("get_software_info()", client.get_software_info())
    await probe("get_system_info()", client.get_system_info())
    # Already used in phase 1 (sanity):
    await probe("get_sound_output()", client.get_sound_output())
    await probe("get_current_app()", client.get_current_app())
    await probe("get_volume()", client.get_volume())
    await probe("get_muted()", client.get_muted())
    await probe("get_power_state()", client.get_power_state())

    try:
        await client.disconnect()
    except Exception:  # noqa: BLE001
        pass

    print("\nVERDICT GUIDE:")
    print("  - If get_picture_settings returned a dict of values -> build the picture")
    print("    number sliders with real current values.")
    print("  - If it ERRORed/500'd -> we make the sliders write-only/optimistic (like")
    print("    the game genre) or skip them.")
    print("  - get_software_info/get_system_info feed the model/version sensors; if they")
    print("    ERROR, those sensors stay best-effort/unavailable.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
