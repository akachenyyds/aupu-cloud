"""Shared AUPU entity metadata and availability."""
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_profile import read_device_temperature


class AupuEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, suffix):
        super().__init__(coordinator)
        device = coordinator.device
        self._attr_unique_id = f"{device['did']}_{suffix}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device["did"])},
                                           name=device["name"], manufacturer="奥普",
                                           model=device["product_name"])

    @property
    def cloud_available(self):
        return super().available and bool(self.coordinator.data and self.coordinator.data.get("connected"))

    @property
    def available(self):
        return self.cloud_available and self.coordinator.data.get("device_online") is True

    def property_value(self, function, prop):
        reported = (self.coordinator.data or {}).get("reported") or {}
        return reported.get(function, {}).get("properties", {}).get(prop)

    @property
    def reported_temperature(self):
        return read_device_temperature(self.property_value("4", "1"),
                                       self.property_value("3", "1"))
