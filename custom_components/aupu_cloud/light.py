"""Independent built-in, night and optional external on/off lighting."""
from homeassistant.components.light import ColorMode, LightEntity

from .entity import AupuEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([AupuBuiltInLight(entry.runtime_data), AupuNightLight(entry.runtime_data),
                        AupuExternalLight(entry.runtime_data)])


class AupuBuiltInLight(AupuEntity, LightEntity):
    _attr_name = "照明"
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(self, coordinator):
        super().__init__(coordinator, "built_in_light")

    @property
    def is_on(self):
        value = self.property_value("2", "1")
        return value if type(value) is bool else None

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_set_light(True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_light(False)


class AupuNightLight(AupuEntity, LightEntity):
    _attr_name = "小夜灯"
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(self, coordinator):
        super().__init__(coordinator, "night_light")

    @property
    def is_on(self):
        value = self.property_value("6", "4")
        return value if type(value) is bool else None

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_set_night_light(True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_night_light(False)


class AupuExternalLight(AupuEntity, LightEntity):
    """Unconnected output on this installation; users may explicitly enable it."""

    _attr_name = "外接灯光"
    _attr_entity_registry_enabled_default = False
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(self, coordinator):
        super().__init__(coordinator, "external_light")

    @property
    def is_on(self):
        value = self.property_value("6", "3")
        return value if type(value) is bool else None

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_set_external_light(True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_external_light(False)
