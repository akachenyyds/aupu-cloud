"""Only already verified reported fields are exposed."""
import math
from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfTemperature

from .entity import AupuEntity
from .device_profile import OPERATING_FUNCTIONS


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([AupuTargetTemperature(entry.runtime_data),
                        AupuDeviceTemperature(entry.runtime_data), AupuLastReported(entry.runtime_data)])


class AupuDeviceTemperature(AupuEntity, SensorEntity):
    _attr_name = "设备温度"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator):
        super().__init__(coordinator, "device_temperature")

    @property
    def available(self):
        return super().available and self.reported_temperature is not None

    @property
    def native_value(self):
        return self.reported_temperature


class AupuTargetTemperature(AupuEntity, SensorEntity):
    _attr_name = "设定温度"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator):
        super().__init__(coordinator, "target_temperature")

    @property
    def native_value(self):
        value = self.property_value("3", "3")
        return value if type(value) in (int, float) and math.isfinite(value) else None


class AupuOperatingFunction(AupuEntity, SensorEntity):
    _attr_name = "运行功能"

    def __init__(self, coordinator):
        super().__init__(coordinator, "operating_function")

    @property
    def native_value(self):
        value = self.property_value("3", "2")
        return OPERATING_FUNCTIONS.get(value) if type(value) is int else None


class AupuLastReported(AupuEntity, SensorEntity):
    _attr_name = "状态更新时间"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator):
        super().__init__(coordinator, "last_reported")

    @property
    def available(self):
        return self.cloud_available

    @property
    def native_value(self):
        value = (self.coordinator.data or {}).get("reported_updated_at")
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value < 253402300799:
            return None
        return datetime.fromtimestamp(value, timezone.utc)
