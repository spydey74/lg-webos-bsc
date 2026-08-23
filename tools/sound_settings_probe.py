#!/usr/bin/env python3
"""
sound_settings_probe.py -- what does the TV report for the "sound" category?

Goal: expose the TV's own audio settings so automations can compare them to the
'desired state' pushed to the soundbar and detect when the TV silently overrides
them. Reads go through bscpylgtv.get_system_settings("sound", keys) -- the same
generic getSystemSettings path get_picture_settings uses.

On this firmware some categories read (picture, system) and some 500 (other), and
a single unsupported key can 500 the whole batch (as sharpness/oled_light did for
picture). So this probe reads the candidate keys BOTH as one batch AND one-by-one,
so we learn exactly which keys are readable and their current values.

Changes nothing (reads only).

REQUIREMENTS (HA Terminal add-on)
---------------------------------
    pip install --upgrade "aiowebostv>=0.9.2" "bscpylgtv==0.5.2"

USAGE
-----
    python3 sound_settings_probe.py 192.168.1.245 --key <YOUR_CLIENT_KEY>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

try:
    from bscpylgtv import WebOsClient
except ImportError:
    print('ERROR: pip install "bscpylgtv==0.5.2"')
    raise SystemExit(1)

try:
    from aiowebostv.handshake import REGISTRATION_PAYLOAD
    CANONICAL_MANIFEST = dict(REGISTRATION_PAYLOAD["manifest"])
except Exception:  # noqa: BLE001
    print('ERROR: pip install --upgrade "aiowebostv>=0.9.2"')
    raise SystemExit(1)

# The keys of interest (the user's list) plus a few eARC/soundbar-relevant extras
# to see whether they also read on this model. Unsupported ones just fail here.
CANDIDATE_KEYS = [
    # Confirmed readable on webOS 26 (OLED G6):
    "soundMode",
    "soundOutput",
    "soundOutputDigital",
    "avSync",
    "avSyncSpdif",
    # eARC / digital-audio negotiation knobs the TV may override -- re-run to see
    # which of these also read on your model:
    "eArcSupport",
    "hdmiArcMode",
    "digitalAudioPriority",
    "forceOutputDDPLUS",
    "inputAudioFormatHDMI3",
    "digitalSoundOutput",
    "clearVoice",
    "drc",
    "equalizerStatus",
    "autoVolume",
    # Known to 500 on read on this firmware (kept to reconfirm on other models):
    "aiSound",
    "aigamesound",
    "bluetoothMode",
    "soundModeModified",
    "soundModeSync",
    "soundOptimizer",
    "eArc",
]


class _MemStore:
    def __init__(self, ip, key):
        self._d = {ip: key} if key else {}

    async def get_key(self, key):
        return self._d.get(key)

    async def set_key(self, key, val):
        self._d[key] = val

    async def list_keys(self):
        return dict(self._d)


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host")
    p.add_argument("--key", required=True)
    args = p.parse_args()

    client = WebOsClient(
        args.host, client_key=args.key, key_file_path=None,
        storage=_MemStore(args.host, args.key), states=[],
        timeout_connect=3, connect_retry_attempts=3,
    )
    client.manifest = CANONICAL_MANIFEST
    await client.async_init()

    print(f"Connecting to {args.host} ...")
    try:
        await client.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to connect: {type(exc).__name__}: {exc}")
        return 1
    print("Connected.\n")

    # 1) Batch read of all candidates (may 500 if any key is unsupported).
    print("BATCH read of all candidate keys:")
    try:
        res = await client.get_system_settings("sound", list(CANDIDATE_KEYS))
        settings = res.get("settings", res)
        print("  OK:", json.dumps(settings, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        print(f"  BATCH FAILED ({type(exc).__name__}: {str(exc)[:80]}) -- reading one by one below.")

    # 2) Per-key reads: reveals which keys are supported + their current value.
    print("\nPER-KEY read (supported keys and current values):")
    readable = {}
    for k in CANDIDATE_KEYS:
        try:
            res = await client.get_system_settings("sound", [k])
            s = res.get("settings", {})
            if k in s:
                readable[k] = s[k]
                print(f"  [OK  ] {k:22} = {json.dumps(s[k], ensure_ascii=False)}")
            else:
                print(f"  [-- ] {k:22} (no value returned)")
        except Exception as exc:  # noqa: BLE001
            print(f"  [ERR] {k:22} {type(exc).__name__}: {str(exc)[:60]}")

    try:
        await client.disconnect()
    except Exception:  # noqa: BLE001
        pass

    print("\n==================== RESULT ====================")
    print(f"  Readable sound keys ({len(readable)}): {', '.join(readable) or '(none)'}")
    print("  -> the integration will expose these as sensors (state + attributes).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
