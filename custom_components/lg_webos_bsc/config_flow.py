"""Config flow for LG webOS (bscpylgtv).

Offers BOTH entry paths (handover sec.3a): paste an existing (grandfathered)
key, or PROMPT-pair fresh on the TV. Which one is the *recommended* happy path
is decided by the sec.3a decision-gate test (tools/webos26_decision_gate_probe.py):

  * Hypothesis A (fresh key on the new manifest gets full perms) -> prefer FRESH.
  * Hypothesis B (fresh key still restricted)                    -> prefer EXISTING.

Until that test is run against the real TV, EXISTING (grandfathered) is the
CONFIRMED-working default and is listed first. Flip DEFAULT_RECOMMENDED_MODE
once the gate result is known.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    ConfigEntry,
)
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv

from bscpylgtv import WebOsClient

from .const import (
    CONF_CLIENT_KEY,
    CONF_ENABLE_INPUT_SWITCHING,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_POLL_INTERVAL,
    DEFAULT_ENABLE_INPUT_SWITCHING,
    DEFAULT_NAME,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    PAIR_MODE_EXISTING,
    PAIR_MODE_FRESH,
)
from .coordinator import _MemoryKeyStore
from .patch import apply_manifest

_LOGGER = logging.getLogger(__name__)

# sec.3a RESOLVED 2026-08-22 -> HYPOTHESIS A, confirmed live on real hardware:
# a FRESH pairing with the new canonical manifest grants FULL permissions
# (launch_app OK, setSystemSettings via bridge visibly flipped gameGenre on
# screen; only the direct setSystemSettings alias 401s, as expected). The
# grandfathered key is NOT required. FRESH is therefore the recommended default;
# the existing-key path is kept as a fallback (e.g. reusing a key from
# LGTVCompanion) but is no longer the happy path.
DEFAULT_RECOMMENDED_MODE = PAIR_MODE_FRESH

# Fresh pairing needs time for the user to accept the on-screen PROMPT.
FRESH_PAIR_WAIT = 35.0
# Reusing a known key should be quick; don't hang the UI on an off TV.
EXISTING_KEY_WAIT = 12.0


class CannotConnect(Exception):
    """TV unreachable / off."""


class ManifestRejected(Exception):
    """403 blacklisted certificate -- the manifest was not replaced (a bug)."""


class RestrictedKey(Exception):
    """401 on a core call -- restricted (freshly-paired) key; see sec.3a."""


class PairingFailed(Exception):
    """Fresh PROMPT pairing did not complete (prompt not accepted in time)."""


async def _connect(host: str, key: str | None, wait: float) -> tuple[str | None, str | None]:
    """Connect once (bounded), map failures to typed errors.

    Returns (client_key, current_app_id). client_key is the freshly-paired key
    when key was None.
    """
    deadline = time.monotonic() + wait
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        client = await WebOsClient.create(
            host,
            client_key=key,
            key_file_path=None,
            storage=_MemoryKeyStore(host, key),
            states=[],
        )
        apply_manifest(client)
        try:
            await client.connect()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            msg = str(exc).lower()
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            if "403" in msg or "blacklist" in msg:
                raise ManifestRejected(str(exc)) from exc
            if "401" in msg or "permission" in msg or "denied" in msg:
                raise RestrictedKey(str(exc)) from exc
            # Otherwise treat as a not-ready TV and keep retrying within the window.
            await asyncio.sleep(0.4)
            continue

        new_key = client.client_key
        try:
            app_id = await client.get_current_app()
        except Exception:  # noqa: BLE001 -- a connected TV with a read hiccup is still valid
            app_id = None
        finally:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        return new_key, app_id

    if key is None:
        raise PairingFailed(str(last_exc))
    raise CannotConnect(str(last_exc))


def _base_schema(defaults: dict[str, Any], *, with_key: bool) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): cv.string,
    }
    if with_key:
        fields[vol.Required(CONF_CLIENT_KEY, default=defaults.get(CONF_CLIENT_KEY, ""))] = cv.string
    fields[vol.Optional(CONF_MAC, default=defaults.get(CONF_MAC, ""))] = cv.string
    fields[vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME))] = cv.string
    return vol.Schema(fields)


class LgWebosBscConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the LG webOS (bscpylgtv) config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """First step: choose how to obtain the client key."""
        # Order the menu so the sec.3a-recommended mode comes first.
        if DEFAULT_RECOMMENDED_MODE == PAIR_MODE_FRESH:
            menu = ["fresh", "existing_key"]
        else:
            menu = ["existing_key", "fresh"]
        return self.async_show_menu(step_id="user", menu_options=menu)

    async def async_step_existing_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Paste a known-good (grandfathered) client key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            key = user_input[CONF_CLIENT_KEY].strip()
            try:
                await self.async_set_unique_id_for(host, user_input.get(CONF_MAC))
                _, _ = await _connect(host, key, EXISTING_KEY_WAIT)
            except ManifestRejected:
                errors["base"] = "manifest_rejected"
            except RestrictedKey:
                errors["base"] = "restricted_key"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating existing key")
                errors["base"] = "unknown"
            else:
                return self._create_entry(host, key, user_input)
            return self.async_show_form(
                step_id="existing_key",
                data_schema=_base_schema(user_input, with_key=True),
                errors=errors,
            )
        return self.async_show_form(
            step_id="existing_key",
            data_schema=_base_schema({}, with_key=True),
            errors=errors,
        )

    async def async_step_fresh(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """PROMPT-pair fresh: accept the pairing prompt on the TV screen."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                await self.async_set_unique_id_for(host, user_input.get(CONF_MAC))
                new_key, _ = await _connect(host, None, FRESH_PAIR_WAIT)
            except ManifestRejected:
                errors["base"] = "manifest_rejected"
            except PairingFailed:
                errors["base"] = "pairing_failed"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during fresh pairing")
                errors["base"] = "unknown"
            else:
                if not new_key:
                    errors["base"] = "pairing_failed"
                else:
                    return self._create_entry(host, new_key, user_input)
            return self.async_show_form(
                step_id="fresh",
                data_schema=_base_schema(user_input, with_key=False),
                errors=errors,
            )
        return self.async_show_form(
            step_id="fresh",
            data_schema=_base_schema({}, with_key=False),
            errors=errors,
        )

    async def async_set_unique_id_for(self, host: str, mac: str | None) -> None:
        uid = (mac or "").strip().lower() or host
        await self.async_set_unique_id(uid)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

    @callback
    def _create_entry(
        self, host: str, key: str, user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        name = (user_input.get(CONF_NAME) or DEFAULT_NAME).strip() or DEFAULT_NAME
        data = {
            CONF_HOST: host,
            CONF_CLIENT_KEY: key,
        }
        mac = (user_input.get(CONF_MAC) or "").strip()
        if mac:
            data[CONF_MAC] = mac
        return self.async_create_entry(title=name, data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return LgWebosBscOptionsFlow()


class LgWebosBscOptionsFlow(OptionsFlow):
    """Options: poll interval, input-switching toggle, MAC."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options
        data = self.config_entry.data
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_POLL_INTERVAL,
                    default=opts.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL)),
                vol.Optional(
                    CONF_ENABLE_INPUT_SWITCHING,
                    default=opts.get(CONF_ENABLE_INPUT_SWITCHING, DEFAULT_ENABLE_INPUT_SWITCHING),
                ): cv.boolean,
                vol.Optional(
                    CONF_MAC,
                    default=opts.get(CONF_MAC, data.get(CONF_MAC, "")),
                ): cv.string,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
