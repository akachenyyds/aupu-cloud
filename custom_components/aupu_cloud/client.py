"""Bounded AUPU cloud client for the verified product 10363."""
from __future__ import annotations

from copy import deepcopy
import json
import math
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import paho.mqtt.client as mqtt

from .device_profile import MAX_TEMPERATURE, MIN_TEMPERATURE, OPERATING_FUNCTIONS, VENTILATION_LEVELS
from .scene_settings import (SCENE_NAMES, decode_human_scenes, decode_motion_night_light,
                             patch_human_scene_enabled, patch_motion_night_light_enabled,
                             SCHEDULE_NAMES, decode_schedules, patch_schedule_enabled,
                             decode_presence_parameters, patch_presence_parameter,
                             validate_configuration_changes, patch_human_scene,
                             patch_motion_night_light, patch_schedule)

ORIGIN = "https://cn-north-1-prod.aupu.net:17606"
IOT_HOST = "aii5h05kuofsj.ats.iot.cn-north-1.amazonaws.com.cn"
FAMILY = "/iotservice/api/iot/family/list2"
DEVICES = "/iotservice/api/iot/room/and/device/list"
STATE_AUTH = "/iotservice/api/iot/wss/getWssToken"
CONTROL = "/appapi/iot/control"
CONTROL_PROPERTIES = frozenset((("2", "1"), ("3", "2"), ("3", "3"), ("6", "5"),
    ("6", "4"), ("6", "3"), ("5", "2"), ("6", "6"), ("6", "12"),
    ("6", "14"), ("6", "9"), ("6", "10"), ("6", "11"), ("6", "7")))


class CloudError(Exception):
    """An error safe to show without request credentials or raw responses."""


class AuthenticationError(CloudError):
    """The account needs reauthentication."""


class StateChannelDisconnected(CloudError):
    """The established transport ended; a read may reconnect once."""


def validate_state_credentials(result):
    expected = {"x-amz-customauthorizer-name", "x-amz-customauthorizer-signature", "tokenKeyName"}
    if not isinstance(result, dict) or set(result) != expected or not all(
            isinstance(v, str) and 0 < len(v) <= 4096 and "\r" not in v and "\n" not in v
            for v in result.values()):
        raise AuthenticationError("Unrecognized state authentication")
    return dict(result)


def normalize_temperature(value):
    if (type(value) not in (int, float) or not MIN_TEMPERATURE <= value <= MAX_TEMPERATURE or
            not math.isfinite(value) or int(value) != value):
        raise CloudError("Target temperature must be a whole degree from 30 to 42 Celsius")
    return int(value)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CloudAPI:
    control_via_mqtt = False

    def __init__(self, token: str):
        token = token.strip().removeprefix("Bearer ").strip()
        if not token or len(token) > 8192 or any(c.isspace() for c in token):
            raise AuthenticationError("Invalid access token format")
        self._token = token
        self._owned_devices: set[str] = set()
        self._device_families: dict[str, int] = {}
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def _request(self, method, path, payload=None, family_id=None):
        if (method, path) not in (("GET", FAMILY), ("GET", DEVICES),
                                  ("POST", STATE_AUTH), ("POST", CONTROL)):
            raise CloudError("Unsupported operation")
        query = ""
        if path == DEVICES:
            if not str(family_id).isdigit():
                raise CloudError("Invalid family ID")
            query = "?" + urllib.parse.urlencode({"id": family_id})
        raw = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
        if method == "POST" and raw is None:
            raw = b""
        req = urllib.request.Request(ORIGIN + path + query, data=raw, method=method,
                                     headers={"Authorization": "Bearer " + self._token,
                                              "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self._opener.open(req, timeout=15) as response:
                raw = response.read(1_048_577)
                if len(raw) > 1_048_576:
                    raise CloudError("Response too large")
                data = json.loads(raw)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise AuthenticationError("Account authentication rejected") from None
            raise CloudError("Cloud HTTP request failed") from None
        except (urllib.error.URLError, OSError, ValueError):
            raise CloudError("Cloud connection or response failed") from None
        if not isinstance(data, dict) or data.get("status") != 0:
            status = data.get("code", data.get("status")) if isinstance(data, dict) else None
            message = str(data.get("msg", data.get("message", ""))).lower() if isinstance(data, dict) else ""
            if status in (401, 403, "401", "403") or any(s in message for s in ("token", "unauthor", "未登录", "认证")):
                raise AuthenticationError("Account authentication rejected")
            raise CloudError("Cloud operation rejected")
        return data.get("result")

    def discover(self):
        result = self._request("GET", FAMILY)
        if not isinstance(result, dict) or not isinstance(result.get("list"), list):
            raise CloudError("Unrecognized family list")
        devices = {}
        families = {}
        for family in result["list"][:20]:
            if not isinstance(family, dict) or not isinstance(family.get("family"), dict):
                raise CloudError("Unrecognized family record")
            fid = family.get("family", {}).get("id")
            result = self._request("GET", DEVICES, family_id=fid)
            if not isinstance(result, dict) or not isinstance(result.get("device"), list):
                raise CloudError("Unrecognized device list")
            for device in result["device"]:
                if not isinstance(device, dict):
                    raise CloudError("Unrecognized device record")
                did = str(device.get("did", ""))
                if device.get("pid") == 10363 and did.isdigit():
                    families[did] = fid
                    devices[did] = {"did": did, "name": device.get("deviceName") or "奥普浴霸",
                                    "product_name": device.get("productName") or "奥普热能环系列双氧浴霸"}
        self._owned_devices = set(devices)
        self._device_families = families
        return list(devices.values())

    def require_owned(self, did):
        if str(did) not in self._owned_devices:
            raise CloudError("Device not found in this account")

    def device_online(self, did):
        """Physically calibrated list status: 4 online, 5 offline; others unknown."""
        self.require_owned(did)
        family_id = self._device_families.get(str(did))
        if family_id is None:
            raise CloudError("Device family has not been discovered")
        result = self._request("GET", DEVICES, family_id=family_id)
        if not isinstance(result, dict) or not isinstance(result.get("device"), list):
            raise CloudError("Unrecognized device list")
        device = next((d for d in result["device"] if isinstance(d, dict) and str(d.get("did")) == str(did)), None)
        if device is None:
            raise CloudError("Configured device is no longer in its family")
        status = device.get("status")
        return {4: True, 5: False}.get(status) if type(status) is int else None

    def state_credentials(self):
        result = self._request("POST", STATE_AUTH)
        return validate_state_credentials(result)

    def set_light(self, did: str, enabled: bool):
        if type(enabled) is not bool:
            raise CloudError("Lighting requires a boolean value")
        self._set_property(did, "2", "1", enabled)

    def set_mode(self, did: str, mode: int):
        if type(mode) is not int or mode not in OPERATING_FUNCTIONS:
            raise CloudError("Unverified operating function")
        self._set_property(did, "3", "2", mode)

    def set_night_light(self, did: str, enabled: bool):
        if type(enabled) is not bool:
            raise CloudError("Night lighting requires a boolean value")
        self._set_property(did, "6", "4", enabled)

    def set_external_light(self, did: str, enabled: bool):
        if type(enabled) is not bool:
            raise CloudError("External lighting requires a boolean value")
        self._set_property(did, "6", "3", enabled)

    def set_target_temperature(self, did: str, temperature):
        self._set_property(did, "3", "3", normalize_temperature(temperature))

    def set_ventilation_level(self, did: str, level: int):
        if type(level) is not int or level not in VENTILATION_LEVELS:
            raise CloudError("Ventilation level must be 1 to 5")
        self._set_property(did, "6", "5", level)

    def set_blow_speed(self, did: str, level: int):
        if type(level) is not int or level not in VENTILATION_LEVELS:
            raise CloudError("Blow speed must be 1 to 5")
        self._set_property(did, "5", "2", level)

    def _set_human_scenes(self, did: str, value: str):
        try:
            decode_human_scenes(value)
        except ValueError:
            raise CloudError("Unrecognized human-scene configuration") from None
        self._set_property(did, "6", "6", value.lower())

    def _set_motion_night_light(self, did: str, value: str):
        try:
            decode_motion_night_light(value)
        except ValueError:
            raise CloudError("Unrecognized motion night-light configuration") from None
        self._set_property(did, "6", "14", value.lower())

    def set_unoccupied_standby(self, did: str, enabled: bool):
        if type(enabled) is not bool:
            raise CloudError("Unoccupied standby requires a boolean value")
        self._set_property(did, "6", "12", enabled)

    def set_holiday_mode(self, did: str, enabled: bool):
        if type(enabled) is not bool:
            raise CloudError("Holiday mode requires a boolean value")
        self._set_property(did, "6", "9", enabled)

    def _set_presence_parameters(self, did: str, value: int):
        try:
            decode_presence_parameters(value)
        except ValueError:
            raise CloudError("Unrecognized presence parameters") from None
        self._set_property(did, "6", "7", value)

    def _set_schedules(self, did: str, value: str, *, warm=False):
        try:
            decode_schedules(value, warm=warm)
        except ValueError:
            raise CloudError("Unrecognized schedule configuration") from None
        self._set_property(did, "6", "10" if warm else "11", value.lower())

    def _set_property(self, did, function, prop, value):
        self.require_owned(did)
        if (function, prop) not in CONTROL_PROPERTIES:
            raise CloudError("Unverified control field")
        self._request("POST", CONTROL, {"did": int(did), "tag": "propertiesTag",
                      "topicName": f"$aws/things/{did}/shadow/update",
                      "sendBody": {"state": {"desired": {did: {function: {"properties": {prop: value}}}}}}})


class ShadowSession:
    def __init__(self, api: CloudAPI, did: str, listener=None):
        api.require_owned(did)
        self.api, self.did, self.listener = api, str(did), listener
        self._prefix = f"$aws/things/{self.did}/shadow"
        self._topics = {self._prefix + s for s in ("/get/accepted", "/get/rejected", "/update/documents")}
        self._mqtt_control = getattr(api, "control_via_mqtt", False) is True
        if self._mqtt_control:
            self._topics.update(self._prefix + s for s in ("/update/accepted", "/update/rejected"))
        self._lock = threading.RLock()
        self._operation_lock = threading.Lock()
        self._ready = threading.Event()
        self._query_done = threading.Event()
        self._pending_query_token = None
        self._pending_command_token = None
        self._command_done = threading.Event()
        self._command_error = None
        self._changed = threading.Condition(self._lock)
        self._reported = None
        self._version = -1
        self._reported_updated_at = None
        self._connected = False
        self._device_online = None
        self._error = None
        self._client = None
        self._closed = False

    @property
    def connected(self):
        with self._lock:
            return self._connected and self._reported is not None

    def snapshot(self):
        with self._lock:
            return {"reported": deepcopy(self._reported), "version": self._version,
                    "connected": self._connected, "received_at": getattr(self, "_received_at", None),
                    "device_online": self._device_online,
                    "reported_updated_at": self._reported_updated_at}

    def _notify(self):
        if self.listener and not self._closed:
            self.listener(self.snapshot())

    def start(self):
        if self._closed:
            raise CloudError("Closed state session cannot be reused")
        auth = self.api.state_credentials()
        client = self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                    client_id="ha-aupu-" + uuid.uuid4().hex[:20], clean_session=True,
                    protocol=mqtt.MQTTv311, transport="websockets", reconnect_on_failure=False)
        client.connect_timeout = 10
        client.tls_set_context(ssl.create_default_context())
        client.ws_set_options(path="/mqtt", headers=auth)
        client.on_connect = self._on_connect
        client.on_subscribe = self._on_subscribe
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        try:
            client.connect(IOT_HOST, 443, 45)
            client.loop_start()
            if not self._ready.wait(15):
                raise CloudError("Timed out waiting for device state")
            if self._error:
                raise self._error
            if not self.connected:
                raise CloudError("State channel unavailable")
            self._refresh_online_status()
            if self._mqtt_control:
                self.api.bind_command_sender(self._publish_mqtt_control)
            return self.snapshot()
        except Exception as exc:
            self.close()
            if isinstance(exc, CloudError):
                raise
            raise CloudError("State channel connection failed") from None

    def _fail(self, error):
        with self._lock:
            self._error = error
            self._connected = False
            self._pending_query_token = None
            self._pending_command_token = None
            self._command_error = error
            self._command_done.set()
            self._ready.set()
            self._query_done.set()
            self._changed.notify_all()
        self._notify()

    def _on_connect(self, client, userdata, flags, reason, properties):
        if self._closed:
            return
        if reason.is_failure:
            if reason.value in (4, 5, 134, 135):
                self._fail(AuthenticationError("State authentication rejected"))
            else:
                self._fail(CloudError("State server rejected connection"))
            return
        client.subscribe([(topic, 0) for topic in sorted(self._topics)])

    def _on_subscribe(self, client, userdata, mid, reasons, properties):
        if self._closed:
            return
        if len(reasons) != len(self._topics) or any(r.is_failure for r in reasons):
            self._fail(CloudError("State subscription rejected"))
            return
        with self._lock:
            self._connected = True
        self._publish_query()

    def _publish_query(self):
        with self._lock:
            if self._closed:
                return False
            self._pending_query_token = uuid.uuid4().hex
            payload = json.dumps({"clientToken": self._pending_query_token}).encode()
            self._query_done.clear()
        result = self._client.publish(self._prefix + "/get", payload, qos=0, retain=False)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            self._fail(CloudError("State query publish failed"))
            return False
        return True

    def _on_message(self, client, userdata, message):
        if self._closed or message.topic not in self._topics or len(message.payload) > 1_048_576:
            return
        try:
            body = json.loads(message.payload)
            if self._mqtt_control and message.topic.endswith(("/update/accepted", "/update/rejected")):
                with self._lock:
                    if (self._pending_command_token is None or
                            body.get("clientToken") != self._pending_command_token):
                        return
                    self._command_error = (CloudError("MQTT command rejected")
                        if message.topic.endswith("/rejected") else None)
                    self._pending_command_token = None
                    self._command_done.set()
                return
            is_query = message.topic.endswith(("/get/accepted", "/get/rejected"))
            if is_query:
                with self._lock:
                    if (self._pending_query_token is None or
                            body.get("clientToken") != self._pending_query_token):
                        # Shadow replies are shared with other clients for this device.
                        return
                if message.topic.endswith("/get/rejected"):
                    self._fail(CloudError("State query rejected"))
                    return
            document = body.get("current", body)
            reported = document["state"]["reported"][self.did]
            version = document.get("version", body.get("version", -1))
            if not isinstance(reported, dict) or type(version) is not int:
                return
            pending = [document.get("metadata", {}).get("reported", {}).get(self.did, {})]
            timestamps = []
            while pending:
                metadata = pending.pop()
                if not isinstance(metadata, dict):
                    continue
                value = metadata.get("timestamp")
                if type(value) in (int, float) and math.isfinite(value) and 0 < value < 253402300799:
                    timestamps.append(value)
                pending.extend(v for k, v in metadata.items() if k != "timestamp" and isinstance(v, dict))
            with self._lock:
                if version < self._version:
                    if is_query:
                        # A newer push can arrive before our matching query reply.
                        self._pending_query_token = None
                        self._query_done.set()
                        self._ready.set()
                    return
                if version == self._version and reported != self._reported:
                    # A Shadow version identifies one immutable document.
                    return
                self._version, self._reported = version, deepcopy(reported)
                self._reported_updated_at = max(timestamps) if timestamps else None
                self._received_at = time.time()
                self._received_monotonic = time.monotonic()
                self._error = None
                if is_query:
                    self._pending_query_token = None
                    self._query_done.set()
                    self._ready.set()
                self._changed.notify_all()
            self._notify()
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            return

    def _on_disconnect(self, client, userdata, flags, reason, properties):
        if self._closed:
            return
        self._fail(StateChannelDisconnected("State channel disconnected"))

    def _refresh_locked(self):
        if not self.connected or self._closed:
            raise StateChannelDisconnected("State channel disconnected")
        self._refresh_online_status()
        published = self._publish_query()
        if published and not self._query_done.wait(10):
            self._fail(CloudError("State query timed out"))
        if self._error:
            raise self._error
        return self.snapshot()

    def _refresh_online_status(self):
        try:
            online = self.api.device_online(self.did)
        except CloudError:
            with self._lock:
                self._device_online = None
            self._notify()
            raise
        with self._lock:
            self._device_online = online
        self._notify()

    def refresh(self):
        with self._operation_lock:
            return self._refresh_locked()

    def _publish_mqtt_control(self, did, function, prop, value):
        if not self._mqtt_control or str(did) != self.did or (function, prop) not in CONTROL_PROPERTIES:
            raise CloudError("Unverified control scope")
        with self._lock:
            if self._closed or not self._connected or self._client is None:
                raise CloudError("State channel unavailable")
            self._pending_command_token = uuid.uuid4().hex
            self._command_error = None
            self._command_done.clear()
            payload = json.dumps({"state": {"desired": {self.did: {
                function: {"properties": {prop: value}}}}},
                "clientToken": self._pending_command_token}, separators=(",", ":")).encode()
            client = self._client
        try:
            try:
                result = client.publish(self._prefix + "/update", payload, qos=1)
            except Exception:
                raise CloudError("MQTT publish failed; command outcome uncertain") from None
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                raise CloudError("MQTT publish failed; command outcome uncertain")
            if not self._command_done.wait(8):
                raise CloudError("MQTT acceptance timed out; command outcome uncertain")
            if self._command_error:
                raise self._command_error
        finally:
            with self._lock:
                self._pending_command_token = None

    def _set_and_confirm(self, function, prop, value, send, required_mode=None, only_if_mode=None, transform=None):
        with self._operation_lock:
            if not self.connected:
                raise CloudError("State channel unavailable")
            # A wall action can race a command even inside the polling interval.
            # Query current reported state before no-op and mode decisions.
            self._refresh_locked()
            if self._device_online is not True:
                raise CloudError("Device is offline or its online status is unknown")
            with self._lock:
                mode = self._reported.get("3", {}).get("properties", {}).get("2")
                if only_if_mode is not None and (type(mode) is not int or mode not in OPERATING_FUNCTIONS):
                    raise CloudError("Cannot stop an unknown operating function")
                if only_if_mode is not None and mode != only_if_mode:
                    # Turning this climate entity off must not stop a different function.
                    return self.snapshot()
                if (required_mode is not None and
                        (type(mode) is not int or mode != required_mode)):
                    raise CloudError("This setting requires its operating function to be active")
                current = self._reported.get(function, {}).get("properties", {}).get(prop)
                if transform is not None:
                    try:
                        value = transform(current)
                    except ValueError:
                        raise CloudError("Current settings are not recognized; no configuration was sent") from None
                if type(current) is type(value) and current == value:
                    return self.snapshot()
                baseline_version = self._version
            send(self.did, value)
            deadline = time.monotonic() + 12
            with self._changed:
                while time.monotonic() < deadline:
                    if not self._connected:
                        raise CloudError("State channel disconnected")
                    current = self._reported.get(function, {}).get("properties", {}).get(prop)
                    if (self._version > baseline_version and type(current) is type(value) and current == value):
                        return self.snapshot()
                    self._changed.wait(max(0, deadline - time.monotonic()))
            raise CloudError("Control accepted but device report not confirmed")

    def set_light(self, enabled: bool):
        if type(enabled) is not bool:
            raise CloudError("Lighting requires a boolean value")
        return self._set_and_confirm("2", "1", enabled, self.api.set_light)

    def set_mode(self, mode: int):
        if type(mode) is not int or mode not in OPERATING_FUNCTIONS:
            raise CloudError("Unverified operating function")
        return self._set_and_confirm("3", "2", mode, self.api.set_mode)

    def set_night_light(self, enabled: bool):
        if type(enabled) is not bool:
            raise CloudError("Night lighting requires a boolean value")
        return self._set_and_confirm("6", "4", enabled, self.api.set_night_light)

    def set_external_light(self, enabled: bool):
        if type(enabled) is not bool:
            raise CloudError("External lighting requires a boolean value")
        return self._set_and_confirm("6", "3", enabled, self.api.set_external_light)

    def set_target_temperature(self, temperature):
        temperature = normalize_temperature(temperature)
        return self._set_and_confirm("3", "3", temperature, self.api.set_target_temperature, required_mode=18)

    def set_ai_enabled(self, enabled: bool):
        if type(enabled) is not bool:
            raise CloudError("AI heating requires a boolean value")
        return self._set_and_confirm("3", "2", 18 if enabled else 0, self.api.set_mode,
                                     only_if_mode=None if enabled else 18)

    def set_ventilation_level(self, level: int):
        if type(level) is not int or level not in VENTILATION_LEVELS:
            raise CloudError("Ventilation level must be 1 to 5")
        return self._set_and_confirm("6", "5", level, self.api.set_ventilation_level, required_mode=7)

    def set_blow_speed(self, level: int):
        if type(level) is not int or level not in VENTILATION_LEVELS:
            raise CloudError("Blow speed must be 1 to 5")
        return self._set_and_confirm("5", "2", level, self.api.set_blow_speed, required_mode=2)

    def set_human_scene_enabled(self, setting):
        if not isinstance(setting, tuple) or len(setting) != 2:
            raise CloudError("Invalid scene setting")
        mode, enabled = setting
        if type(mode) is not int or mode not in SCENE_NAMES or type(enabled) is not bool:
            raise CloudError("Invalid scene setting")
        return self._set_and_confirm("6", "6", enabled, self.api._set_human_scenes,
            transform=lambda current: patch_human_scene_enabled(current, mode, enabled))

    def set_motion_night_light_enabled(self, enabled: bool):
        if type(enabled) is not bool:
            raise CloudError("Motion night lighting requires a boolean value")
        return self._set_and_confirm("6", "14", enabled, self.api._set_motion_night_light,
            transform=lambda current: patch_motion_night_light_enabled(current, enabled))

    def set_unoccupied_standby(self, enabled: bool):
        if type(enabled) is not bool:
            raise CloudError("Unoccupied standby requires a boolean value")
        return self._set_and_confirm("6", "12", enabled, self.api.set_unoccupied_standby)

    def set_holiday_mode(self, enabled: bool):
        if type(enabled) is not bool:
            raise CloudError("Holiday mode requires a boolean value")
        return self._set_and_confirm("6", "9", enabled, self.api.set_holiday_mode)

    def set_presence_parameter(self, setting):
        if not isinstance(setting, tuple) or len(setting) != 2:
            raise CloudError("Invalid presence parameter")
        key, value = setting
        if ((key != "sensitivity" or type(value) is not str or value not in ("低", "高")) and
                (key != "distance_meters" or type(value) is not int or not 1 <= value <= 4)):
            raise CloudError("Invalid presence parameter")
        return self._set_and_confirm("6", "7", value, self.api._set_presence_parameters,
            transform=lambda current: patch_presence_parameter(current, key, value))

    def set_schedule_enabled(self, setting):
        if (not isinstance(setting, tuple) or len(setting) != 3 or
                type(setting[0]) is not bool or type(setting[1]) is not int or
                type(setting[2]) is not bool):
            raise CloudError("Invalid schedule setting")
        warm, mode, enabled = setting
        if mode not in ({18} if warm else SCHEDULE_NAMES):
            raise CloudError("Unsupported schedule mode")
        return self._set_and_confirm("6", "10" if warm else "11", enabled,
            lambda did, value: self.api._set_schedules(did, value, warm=warm),
            transform=lambda current: patch_schedule_enabled(current, mode, enabled, warm=warm))

    def configure_human_scene(self, setting):
        if (type(setting) is not tuple or len(setting) != 2 or
                type(setting[0]) is not int or setting[0] not in SCENE_NAMES):
            raise CloudError("Invalid human scene configuration")
        mode, changes = setting
        try:
            validate_configuration_changes("human", changes)
        except ValueError:
            raise CloudError("Invalid human scene parameters") from None
        return self._set_and_confirm("6", "6", None, self.api._set_human_scenes,
            transform=lambda current: patch_human_scene(current, mode, changes))

    def configure_motion_night_light(self, changes):
        try:
            validate_configuration_changes("night", changes)
        except ValueError:
            raise CloudError("Invalid motion night-light parameters") from None
        return self._set_and_confirm("6", "14", None, self.api._set_motion_night_light,
            transform=lambda current: patch_motion_night_light(current, changes))

    def configure_schedule(self, setting):
        if type(setting) is not tuple or len(setting) != 3:
            raise CloudError("Invalid schedule configuration")
        warm, mode, changes = setting
        try:
            validate_configuration_changes("schedule", changes, mode=mode, warm=warm)
        except ValueError:
            raise CloudError("Invalid schedule parameters") from None
        return self._set_and_confirm("6", "10" if warm else "11", None,
            lambda did, value: self.api._set_schedules(did, value, warm=warm),
            transform=lambda current: patch_schedule(current, mode, changes, warm=warm))

    def close(self):
        if self._closed:
            return
        self._closed = True
        with self._lock:
            self._connected = False
            self._pending_query_token = None
            self._pending_command_token = None
            self._error = CloudError("State session closed")
            self._command_error = self._error
            self._command_done.set()
            self._ready.set()
            self._query_done.set()
            self._changed.notify_all()
        client, self._client = self._client, None
        if self._mqtt_control:
            self.api.unbind_command_sender(self._publish_mqtt_control)
        if client:
            client.disconnect()
            client.loop_stop()
        with self._lock:
            self._connected = False
