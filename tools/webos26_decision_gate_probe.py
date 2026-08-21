#!/usr/bin/env python3
"""
webos26_decision_gate_probe.py -- the CLAUDE_CODE_HANDOVER sec.3a DECISION GATE.

QUESTION THIS ANSWERS
---------------------
Does a *FRESH* PROMPT pairing using aiowebostv's NEW canonical webOS 26 manifest
(the one from PR #719 / commit 2eff7398, shipped in aiowebostv >= 0.9.2) grant
FULL permissions -- specifically launch_app (LAUNCH) and setSystemSettings via
the alert bridge (WRITE_SETTINGS) -- or only the restricted subset that a fresh
key got under the OLD manifest?

  - If FULL  -> hypothesis A: the HACS integration can pair fresh normally and the
                grandfathered key (and all its custody fragility) is UNNECESSARY.
  - If NOT   -> hypothesis B: the config flow must accept a pasted grandfathered
                key and must NOT pair fresh in the happy path.

This gates the config-flow design. Run it, paste the whole output back.

WHAT IT DOES (changes nothing permanent beyond creating a new pairing)
----------------------------------------------------------------------
1. Confirms the installed aiowebostv ships the NEW manifest (appVersion 1.1, a
   single top-level permissions list, NO 'signed' / NO 'signatures'). If it does
   not, it stops and tells you to upgrade -- the whole point is to test the new
   manifest.
2. Pairs FRESH (client_key=None, pairingType PROMPT). ACCEPT THE PROMPT ON THE
   TV SCREEN when it appears.
3. Runs the permission battery on the freshly-obtained key, same battery as
   lg_webos_permission_probe.py:
       read getForegroundAppInfo / getVolume          (basic reads)
       LAUNCH com.webos.app.screensaver  (== launch_app)
       WRITE gameGenre=FPS  direct ssap  (expected 401 -- reference line)
       WRITE gameGenre=FPS  via alert bridge  (watch the TV screen)
       WRITE gameGenre=Standard via alert bridge (restore)
4. Prints a verdict (hypothesis A vs B) and the NEW client-key it obtained, so
   you can reuse or discard it.

Compare the verdict to the grandfathered-key baseline in
webos_grandfathered_key_finding.md:
    launch_app (screensaver)              -> OK
    setSystemSettings gameGenre (bridge)  -> gameGenre flips on screen
    setSystemSettings gameGenre (direct)  -> 401 (expected, not a wall)

REQUIREMENTS
------------
    pip install --upgrade "aiowebostv>=0.9.2"      # MUST be >=0.9.2 for the new manifest

USAGE
-----
    python3 webos26_decision_gate_probe.py 192.168.1.245

    # skip the visible screensaver launch (still tests the bridge):
    python3 webos26_decision_gate_probe.py 192.168.1.245 --no-launch

Exit 0 if fresh pairing succeeded and the battery ran, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

try:
    from aiowebostv import WebOsClient
    from aiowebostv.handshake import REGISTRATION_PAYLOAD
except ImportError:
    print("ERROR: aiowebostv is required. Install it with:\n"
          '    pip install --upgrade "aiowebostv>=0.9.2"')
    raise SystemExit(1)

# The canonical webOS 26 manifest, taken straight from the installed aiowebostv.
# We do NOT hand-copy it -- if aiowebostv is >= 0.9.2 this IS the post-#719 manifest.
CANONICAL_MANIFEST = REGISTRATION_PAYLOAD["manifest"]

EP_CREATE_ALERT = "system.notifications/createAlert"
EP_CLOSE_ALERT = "system.notifications/closeAlert"
LUNA_SET_SYSTEM_SETTINGS = "com.webos.settingsservice/setSystemSettings"


def manifest_is_new(m: dict) -> bool:
    """True only for the post-#719 canonical manifest shape."""
    return (
        "signed" not in m
        and "signatures" not in m
        and isinstance(m.get("permissions"), list)
        and "LAUNCH" in m["permissions"]
        and "WRITE_SETTINGS" in m["permissions"]
    )


def classify(resp: dict) -> str:
    if resp.get("type") == "error":
        err = str(resp.get("error", ""))
        return "DENIED" if "401" in err or "permission" in err.lower() else "ERROR"
    payload = resp.get("payload") or {}
    if payload.get("returnValue") is False:
        err = str(payload.get("errorText") or payload.get("errorCode") or "")
        return "DENIED" if "401" in err or "permission" in err.lower() else "ERROR"
    return "OK"


async def recv(ws, timeout: float = 8.0) -> dict:
    try:
        return await asyncio.wait_for(ws.receive_json(), timeout=timeout)
    except asyncio.TimeoutError:
        return {"type": "error", "error": "no response (silent drop)"}


async def send_ssap(ws, uri: str, payload: dict | None, label: str, req_id: str):
    await ws.send_json({"type": "request", "id": req_id, "uri": f"ssap://{uri}", "payload": payload or {}})
    resp = await recv(ws)
    v = classify(resp)
    print(f"  [{v:6}] {label}")
    if v != "OK":
        print(f"           -> {json.dumps(resp.get('error') or resp.get('payload'), ensure_ascii=False)[:200]}")
    return v, resp


async def send_luna(ws, luna_uri: str, params: dict, label: str, req_id: str):
    lunauri = f"luna://{luna_uri}"
    payload = {
        "message": " ",
        "buttons": [{"label": "", "onClick": lunauri, "params": params}],
        "onclose": {"uri": lunauri, "params": params},
        "onfail": {"uri": lunauri, "params": params},
    }
    await ws.send_json({"type": "request", "id": req_id + "_a",
                        "uri": f"ssap://{EP_CREATE_ALERT}", "payload": payload})
    resp = await recv(ws)
    v = classify(resp)
    if v != "OK":
        print(f"  [{v:6}] {label}  (createAlert failed)")
        print(f"           -> {json.dumps(resp.get('error') or resp.get('payload'), ensure_ascii=False)[:200]}")
        return v, resp
    alert_id = (resp.get("payload") or {}).get("alertId")
    await ws.send_json({"type": "request", "id": req_id + "_b",
                        "uri": f"ssap://{EP_CLOSE_ALERT}", "payload": {"alertId": alert_id}})
    resp2 = await recv(ws)
    v2 = classify(resp2)
    tag = "SENT" if v2 == "OK" else v2
    print(f"  [{tag:6}] {label}  (via alert bridge -- WATCH THE TV SCREEN)")
    if v2 != "OK":
        print(f"           -> {json.dumps(resp2.get('error') or resp2.get('payload'), ensure_ascii=False)[:200]}")
    return v2, resp2


async def fresh_pair(ws) -> str | None:
    """Register with NO client-key -> PROMPT. Returns the new client-key or None."""
    reg = {
        "type": "register", "id": "register_0",
        "payload": {
            "forcePairing": False,
            "pairingType": "PROMPT",
            "manifest": CANONICAL_MANIFEST,
        },
    }
    await ws.send_json(reg)
    first = await recv(ws, timeout=10.0)
    if first.get("type") == "registered":
        # Some firmwares skip the prompt if a relationship already exists.
        return (first.get("payload") or {}).get("client-key")
    if first.get("type") != "response" or (first.get("payload") or {}).get("pairingType") != "PROMPT":
        print("Unexpected first register response:")
        print(json.dumps(first, indent=2, ensure_ascii=False)[:600])
        return None
    print("\n*** TV is showing a PAIRING PROMPT -- ACCEPT IT ON THE TV NOW (you have ~30s). ***\n")
    second = await recv(ws, timeout=30.0)
    if second.get("type") == "registered":
        return (second.get("payload") or {}).get("client-key")
    print("Pairing did not complete:")
    print(json.dumps(second, indent=2, ensure_ascii=False)[:600])
    return None


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host", help="TV IP address, e.g. 192.168.1.245")
    p.add_argument("--include-launch", action="store_true", default=True)
    p.add_argument("--no-launch", dest="include_launch", action="store_false")
    args = p.parse_args()

    print("=== webOS 26 sec.3a DECISION GATE: fresh pair with the NEW canonical manifest ===\n")
    print(f"aiowebostv manifest appVersion: {CANONICAL_MANIFEST.get('appVersion')!r}")
    if not manifest_is_new(CANONICAL_MANIFEST):
        print("\nERROR: the installed aiowebostv is NOT the post-#719 manifest (no top-level")
        print("LAUNCH/WRITE_SETTINGS, or a 'signed'/'signatures' block is present).")
        print("This test is meaningless on the old manifest. Upgrade first:")
        print('    pip install --upgrade "aiowebostv>=0.9.2"')
        return 1
    print("Manifest shape OK: appVersion 1.1, single top-level permissions list, "
          "no 'signed'/'signatures'.\n")

    # Fresh pair: client_key=None.
    client = WebOsClient(args.host, client_key=None, connect_timeout=5)
    client._ensure_client_session()
    print(f"Connecting to {args.host} ...")
    try:
        ws = await client._create_main_ws()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to open websocket: {type(exc).__name__}: {exc}")
        await client.close_client_session()
        return 1
    await client._get_hello_info(ws)
    try:
        await client._get_pre_reg_system_info(ws)
    except Exception as exc:  # noqa: BLE001
        print(f"(pre-reg system info step: {type(exc).__name__}: {exc} -- continuing)")

    new_key = await fresh_pair(ws)
    if not new_key:
        print("\nFAILED: fresh pairing did not yield a client-key.")
        await ws.close(); await client.close_client_session()
        return 1
    print(f"\n*** Fresh pairing SUCCEEDED. New client-key: {new_key} ***\n")

    print("Probing permissions on the FRESH key (OK/SENT worked, DENIED = 401):")
    results: list[tuple[str, str]] = []

    v, _ = await send_ssap(ws, "com.webos.applicationManager/getForegroundAppInfo", None,
                           "read  getForegroundAppInfo", "p0")
    results.append(("read getForegroundAppInfo", v))
    v, _ = await send_ssap(ws, "audio/getVolume", None, "read  getVolume", "p1")
    results.append(("read getVolume", v))

    if args.include_launch:
        v, _ = await send_ssap(ws, "system.launcher/launch", {"id": "com.webos.app.screensaver"},
                               "LAUNCH com.webos.app.screensaver (== launch_app)", "p2")
        results.append(("launch_app (screensaver)", v))

    v, _ = await send_ssap(ws, "settings/setSystemSettings",
                           {"category": "other", "settings": {"gameGenre": "FPS"}},
                           "WRITE gameGenre=FPS (direct ssap -- expected 401)", "p3")
    results.append(("setSystemSettings direct (expect DENIED)", v))

    v, _ = await send_luna(ws, LUNA_SET_SYSTEM_SETTINGS,
                           {"category": "other", "settings": {"gameGenre": "FPS"}},
                           "WRITE gameGenre=FPS (alert bridge)", "p4")
    results.append(("setSystemSettings via bridge (gameGenre=FPS)", v))
    await asyncio.sleep(0.5)
    v, _ = await send_luna(ws, LUNA_SET_SYSTEM_SETTINGS,
                           {"category": "other", "settings": {"gameGenre": "Standard"}},
                           "WRITE gameGenre=Standard (alert bridge, restore)", "p5")
    results.append(("setSystemSettings via bridge (restore Standard)", v))

    await ws.close(); await client.close_client_session()

    launch_ok = any("launch_app" in l and v == "OK" for l, v in results)
    bridge_ok = any("gameGenre=FPS" in l and v == "OK" for l, v in results)

    print("\n==================== SUMMARY ====================")
    for label, v in results:
        print(f"  {v:6}  {label}")
    print("\n---------------- sec.3a VERDICT ----------------")
    print(f"  launch_app (LAUNCH)            : {'GRANTED' if launch_ok else 'DENIED/failed'}")
    print(f"  setSystemSettings via bridge   : {'DISPATCHED' if bridge_ok else 'FAILED'} "
          "(confirm the Game Optimizer genre visibly flipped FPS->Standard)")
    print()
    if launch_ok and bridge_ok:
        print("  => HYPOTHESIS A LIKELY: a FRESH key on the new manifest gets full permissions.")
        print("     If the genre visibly changed on screen, the grandfathered key is NOT needed;")
        print("     the integration can default to normal fresh pairing.")
    else:
        print("  => HYPOTHESIS B LIKELY: the fresh key is still restricted.")
        print("     The integration must use a pasted grandfathered key in the happy path.")
    print("\n  Compare against the grandfathered baseline in webos_grandfathered_key_finding.md.")
    print(f"  New fresh key (reuse or discard): {new_key}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
