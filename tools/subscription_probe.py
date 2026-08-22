#!/usr/bin/env python3
"""
subscription_probe.py -- can we get PUSH state updates on webOS 26?

Core Home Assistant webostv uses push: register_state_update_callback fires on
every TV-side change (volume, app, power, ...) and the entity updates instantly,
with polling only as a reconnect heartbeat. bscpylgtv supports the same API.

We defaulted to polling (states=[]) because bscpylgtv's connect_handler awaits
subscription setup with NO timeout, so a *silently-dropped* subscription on this
firmware could hang connect(). This probe checks two things on YOUR TV:

  1. Does connect() with a curated subscription subset COMPLETE (not hang) --
     it's wrapped in a 20s timeout here.
  2. Do push callbacks actually FIRE when you change something on the TV.

If both are yes, the integration can move to a hybrid push+poll model (instant
updates) with a timeout fallback to polling. If connect hangs or no pushes
arrive, we stay on polling. Changes nothing permanent.

REQUIREMENTS (HA Terminal add-on)
---------------------------------
    pip install --upgrade "aiowebostv>=0.9.2" "bscpylgtv==0.5.2"

USAGE
-----
    python3 subscription_probe.py 192.168.1.245 --key <YOUR_CLIENT_KEY>

Then, while it runs (~40s), change the VOLUME and switch the APP/INPUT on the TV
with the physical remote and watch for [PUSH] lines.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

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

# Curated dynamic subset -- deliberately EXCLUDES system_info and software_info
# (software_info 401s and both are unguarded static fetches during connect).
SUBSCRIBE_STATES = [
    "power",
    "current_app",
    "muted",
    "volume",
    "sound_output",
    "apps",
    "inputs",
    "picture_settings",
]

CONNECT_TIMEOUT = 20.0
WATCH_SECONDS = 40.0


class _MemStore:
    def __init__(self, ip, key):
        self._d = {ip: key} if key else {}

    async def get_key(self, key):
        return self._d.get(key)

    async def set_key(self, key, val):
        self._d[key] = val

    async def list_keys(self):
        return dict(self._d)


def snapshot(client) -> dict:
    return {
        "power": getattr(client, "power_state", None),
        "app": getattr(client, "current_appId", None),
        "volume": getattr(client, "volume", None),
        "muted": getattr(client, "muted", None),
        "sound_output": getattr(client, "sound_output", None),
        "apps": len(getattr(client, "apps", None) or []),
        "inputs": len(getattr(client, "inputs", None) or []),
    }


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host")
    p.add_argument("--key", required=True)
    args = p.parse_args()

    t0 = time.monotonic()
    push_count = 0
    last = {}

    client = WebOsClient(
        args.host,
        client_key=args.key,
        key_file_path=None,
        storage=_MemStore(args.host, args.key),
        states=SUBSCRIBE_STATES,
        timeout_connect=3,
        connect_retry_attempts=3,
    )
    client.manifest = CANONICAL_MANIFEST
    await client.async_init()

    async def on_push(c) -> None:
        nonlocal push_count, last
        push_count += 1
        snap = snapshot(c)
        changed = {k: v for k, v in snap.items() if last.get(k) != v}
        last = snap
        print(f"  [PUSH +{time.monotonic() - t0:4.1f}s] changed={changed}")

    await client.register_state_update_callback(on_push)

    print(f"Connecting to {args.host} with subscriptions {SUBSCRIBE_STATES} ...")
    print(f"(connect is wrapped in a {CONNECT_TIMEOUT:.0f}s timeout to detect a hang)")
    try:
        await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
    except asyncio.TimeoutError:
        print("\nRESULT: connect() HUNG past the timeout -> a subscription silently")
        print("dropped. Push mode is unsafe on this firmware; stay on polling.")
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\nRESULT: connect() FAILED: {type(exc).__name__}: {exc}")
        return 1

    print("Connected (no hang). Initial state:")
    last = snapshot(client)
    for k, v in last.items():
        print(f"    {k:14} = {v}")

    print(f"\nNow CHANGE THE VOLUME and SWITCH APP/INPUT on the TV. Watching {WATCH_SECONDS:.0f}s "
          "for [PUSH] lines...")
    await asyncio.sleep(WATCH_SECONDS)

    try:
        await client.disconnect()
    except Exception:  # noqa: BLE001
        pass

    print("\n==================== RESULT ====================")
    print(f"  connect completed: YES (no hang)")
    print(f"  push callbacks received: {push_count}")
    if push_count > 1:
        print("  => PUSH WORKS. The integration can move to hybrid push+poll for")
        print("     instant updates (with a timeout fallback to polling).")
    else:
        print("  => Few/no pushes. Either you changed nothing, or subscriptions don't")
        print("     emit on this firmware -- if the latter, stay on polling.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
