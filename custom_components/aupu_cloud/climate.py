"""AI heating with the device's verified temperature range and mode semantics."""
from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError

from .client import CloudError, normalize_temperature
from .device_profile import MAX_TEMPERATURE, MIN_TEMPERATURE, OPERATING_FUNCTIONS, TEMPERATURE_STEP
from .entity import AupuEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([AupuAIHeating(entry.runtime_data)])


class AupuAIHeating(AupuEntity, ClimateEntity):
    _attr_name = "AI恒温暖"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = MIN_TEMPERATURE
    _attr_max_temp = MAX_TEMPERATURE
    _attr_target_temperature_step = TEMPERATURE_STEP
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_supported_features = (ClimateEntityFeature.TARGET_TEMPERATURE |
                                ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF)

    def __init__(self, coordinator):
        super().__init__(coordinator, "ai_heating")

    @property
    def hvac_mode(self):
        value = self.property_value("3", "2")
        if type(value) is not int or value not in OPERATING_FUNCTIONS:
            return None
        return HVACMode.HEAT if value == 18 else HVACMode.OFF

    @property
    def target_temperature(self):
        value = self.property_value("3", "3")
        return value if type(value) is int and MIN_TEMPERATURE <= value <= MAX_TEMPERATURE else None

    @property
    def current_temperature(self):
        return self.reported_temperature

    async def async_set_temperature(self, **kwargs):
        # Like the original panel, changing temperature requires AI heating.
        # The command must never start heating as a side effect.
        if ATTR_TEMPERATURE not in kwargs:
            raise HomeAssistantError("请指定 30–42℃ 的整数目标温度")
        try:
            temperature = normalize_temperature(kwargs[ATTR_TEMPERATURE])
        except CloudError as exc:
            raise HomeAssistantError(str(exc)) from exc
        if "hvac_mode" in kwargs:
            if kwargs["hvac_mode"] != HVACMode.HEAT:
                raise HomeAssistantError("调温要求 AI 恒温暖开启；关闭请单独操作")
            await self.coordinator.async_set_ai_enabled(True)
        await self.coordinator.async_set_target_temperature(temperature)

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode not in (HVACMode.OFF, HVACMode.HEAT):
            raise HomeAssistantError("不支持的运行模式")
        await self.coordinator.async_set_ai_enabled(hvac_mode == HVACMode.HEAT)

    async def async_turn_on(self):
        await self.coordinator.async_set_ai_enabled(True)

    async def async_turn_off(self):
        await self.coordinator.async_set_ai_enabled(False)
