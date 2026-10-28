"""Verified exclusive operating functions and neutral ventilation level labels."""
from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from .device_profile import OPERATING_FUNCTIONS, VENTILATION_LEVELS
from .entity import AupuEntity
from .scene_settings import decode_presence_parameters


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([AupuOperatingFunctionSelect(entry.runtime_data),
                        AupuVentilationLevelSelect(entry.runtime_data),
                        AupuBlowSpeedSelect(entry.runtime_data),
                        AupuPresenceParameterSelect(entry.runtime_data, "sensitivity", "感应灵敏度", {"低": "低", "高": "高"}),
                        AupuPresenceParameterSelect(entry.runtime_data, "distance_meters", "感应探测范围", {f"{n} 米": n for n in range(1, 5)})])


class AupuOperatingFunctionSelect(AupuEntity, SelectEntity):
    _attr_name = "运行功能"
    _attr_options = list(OPERATING_FUNCTIONS.values())

    def __init__(self, coordinator):
        super().__init__(coordinator, "operating_function_control")

    @property
    def current_option(self):
        value = self.property_value("3", "2")
        return OPERATING_FUNCTIONS.get(value) if type(value) is int else None

    async def async_select_option(self, option):
        modes = {name: value for value, name in OPERATING_FUNCTIONS.items()}
        if option not in modes:
            raise HomeAssistantError("未验证的运行功能")
        await self.coordinator.async_set_mode(modes[option])


class AupuVentilationLevelSelect(AupuEntity, SelectEntity):
    _attr_name = "换气档位"
    _attr_options = [str(n) for n in VENTILATION_LEVELS]
    _function = "6"
    _property = "5"
    _mode = 7

    def __init__(self, coordinator):
        super().__init__(coordinator, "ventilation_level")

    @property
    def available(self):
        mode = self.property_value("3", "2")
        return super().available and type(mode) is int and mode == self._mode

    @property
    def current_option(self):
        value = self.property_value(self._function, self._property)
        return str(value) if type(value) is int and value in VENTILATION_LEVELS else None

    async def async_select_option(self, option):
        if option not in self.options:
            raise HomeAssistantError("换气档位必须在 1 到 5 之间")
        await self.coordinator.async_set_ventilation_level(int(option))


class AupuBlowSpeedSelect(AupuVentilationLevelSelect):
    _attr_name = "吹风风速"
    _function = "5"
    _property = "2"
    _mode = 2

    def __init__(self, coordinator):
        AupuEntity.__init__(self, coordinator, "blow_speed")

    async def async_select_option(self, option):
        if option not in self.options:
            raise HomeAssistantError("吹风档位必须在 1 到 5 之间")
        await self.coordinator.async_set_blow_speed(int(option))


class AupuPresenceParameterSelect(AupuEntity, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, key, name, options):
        super().__init__(coordinator, "presence_" + key)
        self._key, self._options = key, options
        self._attr_name = name
        self._attr_options = list(options)

    def _settings(self):
        try:
            return decode_presence_parameters(self.property_value("6", "7"))
        except ValueError:
            return None

    @property
    def available(self):
        return super().available and self._settings() is not None

    @property
    def current_option(self):
        settings = self._settings()
        if settings is None:
            return None
        return next(option for option, value in self._options.items() if value == settings[self._key])

    async def async_select_option(self, option):
        if type(option) is not str or option not in self._options:
            raise HomeAssistantError("未验证的感应参数")
        await self.coordinator.async_set_presence_parameter(self._key, self._options[option])
