"""Manual token setup and reauthorization; no invented automatic account renewal."""
import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .client import AuthenticationError, CloudAPI, CloudError, ShadowSession
from .mqtt_api import CachedMQTTAPI
from .lan import normalize_host, normalize_mac
from .const import (CONF_ACCESS_TOKEN, CONF_DEVICE_ID, DOMAIN, CONF_TRANSPORT,
                    TRANSPORT_REST, TRANSPORT_MQTT, CONF_MQTT_CREDENTIALS,
                    CONF_LOCAL_HOST, CONF_EXPECTED_MAC)


_LOGGER = logging.getLogger(__name__)


class LocalVerificationError(CloudError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__("Local device verification failed")


def _discover(token):
    return CloudAPI(token).discover()


def _prepare_mqtt(token, device, host, mac):
    api = CloudAPI(token)
    if not any(d["did"] == device["did"] for d in api.discover()):
        raise AuthenticationError("Configured device is not accessible in this account")
    credentials = api.state_credentials()
    cached = CachedMQTTAPI(credentials, device, host, mac)
    if cached.device_online(device["did"]) is not True:
        reason = cached._probe.last_failure or "local_probe_error"
        _LOGGER.warning("LAN verification failed: %s (%s)", reason,
                        cached._probe.last_error_type or "no exception")
        raise LocalVerificationError(reason)
    session = ShadowSession(cached, device["did"])
    try:
        session.start()
    finally:
        session.close()
    return credentials


TOKEN_SCHEMA = vol.Schema({vol.Required(CONF_ACCESS_TOKEN): TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD))})
TRANSPORT_FIELD = {vol.Optional(CONF_TRANSPORT, default=TRANSPORT_REST): vol.In({
    TRANSPORT_REST: "REST", TRANSPORT_MQTT: "MQTT"})}
USER_SCHEMA = TOKEN_SCHEMA.extend({vol.Optional("device_name"): vol.All(str, vol.Length(max=64)),
                                  **TRANSPORT_FIELD})


class AupuConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                self._devices = await self.hass.async_add_executor_job(_discover, user_input[CONF_ACCESS_TOKEN])
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except CloudError:
                errors["base"] = "cannot_connect"
            else:
                if not self._devices:
                    return self.async_abort(reason="no_devices")
                self._token = user_input[CONF_ACCESS_TOKEN].strip()
                self._transport = user_input.get(CONF_TRANSPORT, TRANSPORT_REST)
                self._editing_entry = None
                self._device_name = user_input.get("device_name", "").strip()
                if len(self._devices) == 1:
                    return await self._create(self._devices[0])
                return await self.async_step_device()
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_device(self, user_input=None):
        choices = {d["did"]: f"{d['name']} ({d['did']})" for d in self._devices}
        if user_input is not None:
            device = next((d for d in self._devices if d["did"] == user_input[CONF_DEVICE_ID]), None)
            if device:
                return await self._create(device)
        return self.async_show_form(step_id="device", data_schema=vol.Schema({
            vol.Required(CONF_DEVICE_ID): vol.In(choices)}))

    async def _create(self, device):
        await self.async_set_unique_id(device["did"])
        self._abort_if_unique_id_configured()
        self._chosen_device = device
        if self._transport == TRANSPORT_MQTT:
            return await self.async_step_mqtt()
        return self._finish_create(device, {CONF_ACCESS_TOKEN: self._token})

    def _finish_create(self, device, extra):
        name = self._device_name or device["name"]
        return self.async_create_entry(title=name, data={
            CONF_DEVICE_ID: device["did"], "device_name": name, **extra})

    async def async_step_mqtt(self, user_input=None):
        errors = {}
        entry = getattr(self, "_editing_entry", None)
        defaults = entry.data if entry else {}
        if user_input is not None:
            try:
                host = normalize_host(user_input[CONF_LOCAL_HOST])
                mac = normalize_mac(user_input[CONF_EXPECTED_MAC])
                credentials = await self.hass.async_add_executor_job(_prepare_mqtt,
                    self._token, self._chosen_device, host, mac)
            except (ValueError, KeyError):
                errors["base"] = "invalid_local_identity"
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except LocalVerificationError as exc:
                errors["base"] = exc.reason
            except CloudError:
                errors["base"] = "cannot_verify_mqtt"
            else:
                data = {CONF_TRANSPORT: TRANSPORT_MQTT, CONF_MQTT_CREDENTIALS: credentials,
                        CONF_LOCAL_HOST: host, CONF_EXPECTED_MAC: mac,
                        "product_name": self._chosen_device.get("product_name") or "奥普热能环系列双氧浴霸"}
                if entry:
                    # Replace rather than merge so an old REST token is removed.
                    data.update({CONF_DEVICE_ID: entry.data[CONF_DEVICE_ID],
                                 "device_name": entry.data.get("device_name") or entry.title})
                    return self.async_update_reload_and_abort(entry, data=data)
                return self._finish_create(self._chosen_device, data)
        schema = vol.Schema({
            vol.Required(CONF_LOCAL_HOST, default=defaults.get(CONF_LOCAL_HOST, "")): str,
            vol.Required(CONF_EXPECTED_MAC, default=defaults.get(CONF_EXPECTED_MAC, "")): str})
        return self.async_show_form(step_id="mqtt", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        return await self._replace_token("reauth_confirm", self._get_reauth_entry(), user_input)

    async def async_step_reconfigure(self, user_input=None):
        return await self._replace_token("reconfigure", self._get_reconfigure_entry(), user_input)

    async def _replace_token(self, step, entry, user_input):
        errors = {}
        if user_input is not None:
            try:
                devices = await self.hass.async_add_executor_job(_discover, user_input[CONF_ACCESS_TOKEN])
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except CloudError:
                errors["base"] = "cannot_connect"
            else:
                if not any(d["did"] == entry.data[CONF_DEVICE_ID] for d in devices):
                    errors["base"] = "wrong_account"
                else:
                    mode = (user_input.get(CONF_TRANSPORT, entry.data.get(CONF_TRANSPORT, TRANSPORT_REST))
                            if step == "reconfigure" else entry.data.get(CONF_TRANSPORT, TRANSPORT_REST))
                    if mode == TRANSPORT_MQTT:
                        self._token = user_input[CONF_ACCESS_TOKEN].strip()
                        self._transport = mode
                        self._chosen_device = next(d for d in devices if d["did"] == entry.data[CONF_DEVICE_ID])
                        self._editing_entry = entry
                        return await self.async_step_mqtt()
                    return self.async_update_reload_and_abort(entry, data={
                        CONF_ACCESS_TOKEN: user_input[CONF_ACCESS_TOKEN].strip(),
                        CONF_DEVICE_ID: entry.data[CONF_DEVICE_ID],
                        "device_name": entry.data.get("device_name") or entry.title})
        schema = TOKEN_SCHEMA
        if step == "reconfigure":
            schema = TOKEN_SCHEMA.extend({vol.Optional(CONF_TRANSPORT,
                default=entry.data.get(CONF_TRANSPORT, TRANSPORT_REST)): vol.In({TRANSPORT_REST: "REST", TRANSPORT_MQTT: "MQTT"})})
        return self.async_show_form(step_id=step, data_schema=schema, errors=errors)
