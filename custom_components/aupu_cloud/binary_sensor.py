"""Connectivity from the configured transport and independent motion state."""
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory

from .entity import AupuEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([AupuDeviceOnline(entry.runtime_data), AupuHumanDetection(entry.runtime_data)])


class AupuDeviceOnline(AupuEntity, BinarySensorEntity):
    _attr_name = "设备在线"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator):
        super().__init__(coordinator, "device_online")

    @property
    def available(self):
        return self.cloud_available and type(self.coordinator.data.get("device_online")) is bool

    @property
    def is_on(self):
        return self.coordinator.data.get("device_online")


class AupuHumanDetection(AupuEntity, BinarySensorEntity):
    _attr_name = "人体感应"
    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(self, coordinator):
        super().__init__(coordinator, "human_detection")

    @property
    def available(self):
        value = self.property_value("6", "23")
        return super().available and type(value) is int and value in (0, 1)

    @property
    def is_on(self):
        value = self.property_value("6", "23")
        return bool(value) if type(value) is int and value in (0, 1) else None
