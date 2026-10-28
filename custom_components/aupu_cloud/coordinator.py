"""Bridge bounded blocking I/O and MQTT push into the HA event loop."""
import asyncio
from datetime import timedelta
import logging

from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import AuthenticationError, CloudAPI, CloudError, ShadowSession, StateChannelDisconnected
from .const import (CONF_ACCESS_TOKEN, CONF_DEVICE_ID, DOMAIN, CONF_TRANSPORT,
                    TRANSPORT_MQTT, CONF_MQTT_CREDENTIALS, CONF_LOCAL_HOST, CONF_EXPECTED_MAC)
from .mqtt_api import CachedMQTTAPI

_LOGGER = logging.getLogger(__name__)


class AupuCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN,
                         update_interval=timedelta(seconds=30))
        self.entry = entry
        self.api = None
        self.session = None
        self.device = None
        self._closed = False
        self._session_generation = 0
        self._data_generation = 0
        self._io_lock = asyncio.Lock()

    def _push_from_thread(self, snapshot, generation=None):
        if not self._closed:
            self.hass.loop.call_soon_threadsafe(self._apply_push, snapshot, generation)

    @callback
    def _apply_push(self, snapshot, generation=None):
        generation = self._session_generation if generation is None else generation
        if self._closed or generation != self._session_generation:
            return
        old_version = (self.data or {}).get("version", -1)
        new_version = snapshot.get("version", -1)
        if (self._data_generation == generation and
                type(old_version) is int and type(new_version) is int and new_version < old_version):
            return
        was_connected = (self._data_generation == generation and
                         bool(self.data and self.data.get("connected")))
        self._data_generation = generation
        self.async_set_updated_data(snapshot)
        if was_connected and snapshot.get("connected") is False:
            # One transition requests one debounced GET-only reconnect. A
            # failed initial connection cannot form an immediate retry loop.
            self.hass.async_create_task(self.async_request_refresh())

    def _start_session(self):
        if self.session:
            self.session.close()
        self._session_generation += 1
        generation = self._session_generation
        self.session = ShadowSession(self.api, self.entry.data[CONF_DEVICE_ID],
            lambda snapshot: self._push_from_thread(snapshot, generation))
        return self.session.start()

    def _read(self):
        if self.api is None:
            if self.entry.data.get(CONF_TRANSPORT) == TRANSPORT_MQTT:
                api = CachedMQTTAPI(self.entry.data.get(CONF_MQTT_CREDENTIALS), {
                    "did": self.entry.data[CONF_DEVICE_ID],
                    "name": self.entry.data.get("device_name") or "奥普浴霸",
                    "product_name": self.entry.data.get("product_name") or "奥普热能环系列双氧浴霸"},
                    self.entry.data.get(CONF_LOCAL_HOST), self.entry.data.get(CONF_EXPECTED_MAC))
            else:
                api = CloudAPI(self.entry.data[CONF_ACCESS_TOKEN])
            devices = api.discover()
            device = next((d for d in devices if d["did"] == self.entry.data[CONF_DEVICE_ID]), None)
            if device is None:
                raise AuthenticationError("Configured device is not accessible in this account")
            device = dict(device)
            if self.entry.data.get("device_name"):
                device["name"] = self.entry.data["device_name"]
            self.api, self.device = api, device
        if self.session is None or not self.session.connected:
            return self._start_session()
        try:
            return self.session.refresh()
        except StateChannelDisconnected:
            # Only a read is repeated, once. Authentication failures, query
            # timeouts and uncertain controls retain their existing errors.
            return self._start_session()

    async def _async_update_data(self):
        async with self._io_lock:
            if self._closed:
                raise UpdateFailed("Integration is shutting down")
            try:
                snapshot = await self.hass.async_add_executor_job(self._read)
                # A push may have arrived while the executor was returning.
                # Preserve that newer state when both belong to this session.
                if (self._data_generation == self._session_generation and self.data and
                        self.data.get("version", -1) > snapshot.get("version", -1)):
                    return self.data
                self._data_generation = self._session_generation
                return snapshot
            except AuthenticationError as exc:
                raise ConfigEntryAuthFailed("Access token needs reauthorization") from exc
            except CloudError as exc:
                raise UpdateFailed(str(exc)) from exc

    async def async_set_light(self, enabled):
        return await self._async_command("set_light", enabled)

    async def async_set_mode(self, mode):
        return await self._async_command("set_mode", mode)

    async def async_set_night_light(self, enabled):
        return await self._async_command("set_night_light", enabled)

    async def async_set_external_light(self, enabled):
        return await self._async_command("set_external_light", enabled)

    async def async_set_target_temperature(self, temperature):
        return await self._async_command("set_target_temperature", temperature)

    async def async_set_ai_enabled(self, enabled):
        return await self._async_command("set_ai_enabled", enabled)

    async def async_set_ventilation_level(self, level):
        return await self._async_command("set_ventilation_level", level)

    async def async_set_blow_speed(self, level):
        return await self._async_command("set_blow_speed", level)

    async def async_set_human_scene_enabled(self, mode, enabled):
        return await self._async_command("set_human_scene_enabled", (mode, enabled))

    async def async_set_motion_night_light_enabled(self, enabled):
        return await self._async_command("set_motion_night_light_enabled", enabled)

    async def async_set_unoccupied_standby(self, enabled):
        return await self._async_command("set_unoccupied_standby", enabled)

    async def async_set_holiday_mode(self, enabled):
        return await self._async_command("set_holiday_mode", enabled)

    async def async_set_schedule_enabled(self, warm, mode, enabled):
        return await self._async_command("set_schedule_enabled", (warm, mode, enabled))

    async def async_set_presence_parameter(self, key, value):
        return await self._async_command("set_presence_parameter", (key, value))

    async def async_configure_human_scene(self, mode, changes):
        return await self._async_command("configure_human_scene", (mode, changes))

    async def async_configure_motion_night_light(self, changes):
        return await self._async_command("configure_motion_night_light", changes)

    async def async_configure_schedule(self, warm, mode, changes):
        return await self._async_command("configure_schedule", (warm, mode, changes))

    async def _async_command(self, operation, value):
        async with self._io_lock:
            if self._closed or self.session is None or not self.session.connected:
                raise HomeAssistantError("浴霸云状态连接不可用")
            try:
                snapshot = await self.hass.async_add_executor_job(getattr(self.session, operation), value)
            except AuthenticationError as exc:
                self.entry.async_start_reauth(self.hass)
                raise HomeAssistantError("奥普账户需要重新授权") from exc
            except CloudError as exc:
                raise HomeAssistantError(str(exc)) from exc
            self._apply_push(snapshot, self._session_generation)

    async def async_shutdown(self):
        if self._closed:
            return
        self._closed = True
        await super().async_shutdown()
        async with self._io_lock:
            if self.session:
                await self.hass.async_add_executor_job(self.session.close)
                self.session = None

    async def async_handle_stop(self, event):
        await self.async_shutdown()
