"""Independent automation settings; preserve every other field of each blob."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
import voluptuous as vol

from .entity import AupuEntity
from .scene_settings import (SCENE_NAMES, SCHEDULE_NAMES, decode_human_scenes,
                             decode_motion_night_light, decode_schedules)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities([AupuHumanSceneSwitch(coordinator, mode) for mode in SCENE_NAMES] +
                       [AupuMotionNightLightSwitch(coordinator), AupuUnoccupiedStandbySwitch(coordinator),
                        AupuHolidaySwitch(coordinator), AupuScheduleSwitch(coordinator, 18, warm=True)] +
                       [AupuScheduleSwitch(coordinator, mode) for mode in SCHEDULE_NAMES])
    platform = entity_platform.async_get_current_platform()
    target = {vol.Required("entity_id"): vol.All(cv.entity_ids, vol.Length(min=1, max=1))}
    clocks = {vol.Optional("start"): str, vol.Optional("end"): str}
    delays = {vol.Optional("absence_delay_seconds"): _integer}
    platform.async_register_entity_service("configure_human_scene", {
        **target, **clocks, **delays, vol.Optional("presence_delay_seconds"): _integer},
        _configure_human_scene)
    platform.async_register_entity_service("configure_motion_night_light", {
        **target, **clocks, **delays, vol.Optional("repeat_days"): list},
        _configure_night_light)
    platform.async_register_entity_service("configure_schedule", {
        **target, vol.Optional("start"): str, vol.Optional("repeat_days"): list,
        vol.Optional("fan_speed"): _integer, vol.Optional("target_temperature"): _integer,
        vol.Optional("duration_minutes"): _integer},
        _configure_schedule)


def _integer(value):
    if type(value) is not int:
        raise vol.Invalid("请输入整数")
    return value


def _changes(entity, call, expected):
    targets = call.data.get("entity_id")
    if (not isinstance(entity, expected) or type(targets) is not list or len(targets) != 1
            or targets[0] != entity.entity_id
            or any(key in call.data for key in ("device_id", "area_id", "floor_id", "label_id"))):
        raise HomeAssistantError("请明确选择单个对应的奥普配置开关，不能批量修改")
    return {key: value for key, value in call.data.items() if key != "entity_id"}


async def _configure_human_scene(entity, call):
    changes = _changes(entity, call, AupuHumanSceneSwitch)
    await entity.coordinator.async_configure_human_scene(entity._mode, changes)


async def _configure_night_light(entity, call):
    changes = _changes(entity, call, AupuMotionNightLightSwitch)
    await entity.coordinator.async_configure_motion_night_light(changes)


async def _configure_schedule(entity, call):
    changes = _changes(entity, call, AupuScheduleSwitch)
    await entity.coordinator.async_configure_schedule(entity._warm, entity._mode, changes)


class AupuHumanSceneSwitch(AupuEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, mode):
        super().__init__(coordinator, f"human_scene_{mode}")
        self._mode = mode
        self._attr_name = SCENE_NAMES[mode]

    def _settings(self):
        try:
            return next(item for item in decode_human_scenes(self.property_value("6", "6"))
                        if item["mode_identifier"] == self._mode)
        except (ValueError, StopIteration):
            return None

    @property
    def available(self):
        return super().available and self._settings() is not None

    @property
    def is_on(self):
        settings = self._settings()
        return settings["enabled"] if settings else None

    @property
    def extra_state_attributes(self):
        settings = self._settings()
        if not settings:
            return {}
        return {"人在后开启_秒": settings["presence_delay_seconds"],
                "人离后关闭_秒": settings["absence_delay_seconds"],
                "生效开始": settings["start"], "生效结束": settings["end"]}

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_set_human_scene_enabled(self._mode, True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_human_scene_enabled(self._mode, False)


class AupuMotionNightLightSwitch(AupuEntity, SwitchEntity):
    _attr_name = "感应夜灯"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator):
        super().__init__(coordinator, "motion_night_light")

    def _settings(self):
        try:
            return decode_motion_night_light(self.property_value("6", "14"))
        except ValueError:
            return None

    @property
    def available(self):
        return super().available and self._settings() is not None

    @property
    def is_on(self):
        settings = self._settings()
        return settings["enabled"] if settings else None

    @property
    def extra_state_attributes(self):
        settings = self._settings()
        if not settings:
            return {}
        return {"人离后关闭_秒": settings["absence_delay_seconds"],
                "生效开始": settings["start"], "生效结束": settings["end"],
                "重复日期": settings["repeat_days"],
                "重复": settings["repeat"]}

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_set_motion_night_light_enabled(True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_motion_night_light_enabled(False)


class AupuUnoccupiedStandbySwitch(AupuEntity, SwitchEntity):
    _attr_name = "无人待机模式"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_extra_state_attributes = {"连续无人待机_分钟": 20}

    def __init__(self, coordinator):
        super().__init__(coordinator, "unoccupied_standby")

    @property
    def available(self):
        return super().available and type(self.property_value("6", "12")) is bool

    @property
    def is_on(self):
        value = self.property_value("6", "12")
        return value if type(value) is bool else None

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_set_unoccupied_standby(True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_unoccupied_standby(False)


class AupuHolidaySwitch(AupuEntity, SwitchEntity):
    _attr_name = "假日模式"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator):
        super().__init__(coordinator, "holiday_mode")

    @property
    def available(self):
        return super().available and type(self.property_value("6", "9")) is bool

    @property
    def is_on(self):
        value = self.property_value("6", "9")
        return value if type(value) is bool else None

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_set_holiday_mode(True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_holiday_mode(False)


class AupuScheduleSwitch(AupuEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, mode, *, warm=False):
        super().__init__(coordinator, "warm_schedule" if warm else f"schedule_{mode}")
        self._mode, self._warm = mode, warm
        self._attr_name = "预约暖房" if warm else SCHEDULE_NAMES[mode]

    def _settings(self):
        try:
            return next(item for item in decode_schedules(
                self.property_value("6", "10" if self._warm else "11"), warm=self._warm)
                if item["mode_identifier"] == self._mode)
        except (ValueError, StopIteration):
            return None

    @property
    def available(self):
        return super().available and self._settings() is not None

    @property
    def is_on(self):
        settings = self._settings()
        return settings["enabled"] if settings else None

    @property
    def extra_state_attributes(self):
        settings = self._settings()
        if not settings:
            return {}
        result = {"开始时间": settings["start"], "重复": settings["repeat"], "重复日期": settings["repeat_days"]}
        if settings["duration_minutes"] is not None:
            result["预约时长_分钟"] = settings["duration_minutes"]
        if settings["fan_speed"] is not None:
            result["预约换气风档"] = settings["fan_speed"]
        if settings["target_temperature"] is not None:
            result["目标温度"] = settings["target_temperature"]
        return result

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_set_schedule_enabled(self._warm, self._mode, True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_schedule_enabled(self._warm, self._mode, False)
