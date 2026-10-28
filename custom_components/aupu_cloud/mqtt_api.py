"""Cached cloud MQTT transport: no account token, discovery or HTTP at runtime."""
from copy import deepcopy
import threading

from .client import CloudAPI, CloudError, CONTROL_PROPERTIES, validate_state_credentials
from .lan import LANProbe


class CachedMQTTAPI(CloudAPI):
    control_via_mqtt = True

    def __init__(self, credentials, device, local_host, expected_mac, *, probe=None):
        self._credentials = validate_state_credentials(credentials)
        if not isinstance(device, dict) or not str(device.get("did", "")).isdigit():
            raise CloudError("Invalid configured device")
        self._device = deepcopy(device)
        self._device["did"] = str(self._device["did"])
        self._owned_devices = {self._device["did"]}
        try:
            self._probe = probe if probe is not None else LANProbe(local_host, expected_mac)
        except ValueError:
            raise CloudError("Invalid local device identity") from None
        self._sender = None
        self._sender_lock = threading.RLock()

    def _request(self, *args, **kwargs):
        raise CloudError("Account HTTP is disabled in cached MQTT mode")

    def discover(self):
        return [deepcopy(self._device)]

    def state_credentials(self):
        return dict(self._credentials)

    def device_online(self, did):
        self.require_owned(did)
        return self._probe.check()

    def bind_command_sender(self, sender):
        with self._sender_lock:
            self._sender = sender

    def unbind_command_sender(self, sender):
        with self._sender_lock:
            if self._sender == sender:
                self._sender = None

    def _set_property(self, did, function, prop, value):
        self.require_owned(did)
        if (function, prop) not in CONTROL_PROPERTIES:
            raise CloudError("Unverified control field")
        # Recheck before every publish, including configuration string merges.
        if self.device_online(did) is not True:
            raise CloudError("Local device is offline or its identity is unknown")
        with self._sender_lock:
            sender = self._sender
        if sender is None:
            raise CloudError("MQTT control session is unavailable")
        sender(str(did), function, prop, value)
