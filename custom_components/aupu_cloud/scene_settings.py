"""Captured T5-Pro settings codecs. Research only; no network or device writes."""
from __future__ import annotations

import re

from .device_profile import VENTILATION_ASSOCIATED_VALUES, SCHEDULE_BASE_MINUTES

SCENE_NAMES = {0: "人感照明", 2: "夏日吹风", 7: "人感换气", 21: "人感除臭除菌"}
SCHEDULE_NAMES = {7: "预约换气", 4: "预约恒温干燥", 21: "预约除臭除菌"}
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def decode_repeat(mask: int, *, once=False) -> list[str]:
    if once and mask == 128:
        return []
    if type(mask) is not int or not 1 <= mask <= 127:
        raise ValueError("Unrecognized repeat mask")
    return [day for index, day in enumerate(WEEKDAYS) if mask & (1 << index)]


def _repeat_mask(days, *, once=False):
    if (type(days) is not list or any(type(day) is not str or day not in WEEKDAYS for day in days)
            or len(set(days)) != len(days) or (not days and not once)):
        raise ValueError("Invalid repeat days")
    return sum(1 << WEEKDAYS.index(day) for day in days) if days else 128


def _parse_clock(value):
    if type(value) is not str or not re.fullmatch(r"[0-9]{2}:[0-9]{2}(?::00)?", value):
        raise ValueError("Time must be HH:MM")
    hour, minute = map(int, value.split(":")[:2])
    _clock(hour, minute)
    return bytes((hour, minute))


def validate_configuration_changes(kind: str, changes, *, mode=None, warm=False):
    allowed = {"start", "end", "presence_delay_seconds", "absence_delay_seconds"} if kind == "human" else (
        {"start", "end", "absence_delay_seconds", "repeat_days"} if kind == "night" else
        {"start", "repeat_days", "duration_minutes"} if kind == "schedule" else set())
    if kind == "schedule":
        if type(warm) is not bool or type(mode) is not int or mode not in ({18} if warm else SCHEDULE_NAMES):
            raise ValueError("Unsupported schedule")
        if warm:
            allowed.add("target_temperature")
        elif mode == 7:
            allowed.add("fan_speed")
    if (type(changes) is not dict or not changes or not set(changes) <= allowed):
        raise ValueError("Unsupported or empty configuration changes")
    for key, value in changes.items():
        if key in ("start", "end"):
            _parse_clock(value)
        elif key.endswith("_delay_seconds"):
            maximum = 3600 if key == "presence_delay_seconds" else 1200
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError(f"Delay must be 0 to {maximum} whole seconds")
        elif key == "repeat_days":
            _repeat_mask(value, once=kind == "schedule")
        elif key == "duration_minutes":
            if type(value) is not int or value not in DURATION_OPTIONS:
                raise ValueError("Choose a supported whole-minute duration")
        elif key == "fan_speed":
            if type(value) is not int or not 1 <= value <= 5:
                raise ValueError("Fan speed must be 1 to 5")
        elif key == "target_temperature":
            if type(value) is not int or not 30 <= value <= 42:
                raise ValueError("Temperature must be 30 to 42 whole degrees")


def decode_presence_parameters(value: int) -> dict:
    if type(value) is not int or value not in (17, 18, 19, 20, 49, 50, 51, 52):
        raise ValueError("Unrecognized presence parameters")
    return {"sensitivity": "高" if value >> 4 == 1 else "低", "distance_meters": value & 15}


def patch_presence_parameter(value: int, key: str, setting) -> int:
    """Modify one calibrated subfield from fresh state; preserve its paired setting."""
    decode_presence_parameters(value)
    if key == "sensitivity" and type(setting) is str and setting in ("低", "高"):
        return (value & 15) | (16 if setting == "高" else 48)
    if key == "distance_meters" and type(setting) is int and 1 <= setting <= 4:
        return (value & 240) | setting
    raise ValueError("Invalid presence parameter")


def _hex_bytes(value: str, length: int) -> bytearray:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{" + str(length * 2) + r"}", value):
        raise ValueError("Unrecognized settings encoding")
    return bytearray.fromhex(value)


def _clock(hour: int, minute: int) -> str:
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Invalid settings clock")
    return f"{hour:02}:{minute:02}"


def decode_human_scenes(value: str) -> list[dict]:
    """Read 4 x 18-byte records; leave uncalibrated action bytes uninterpreted."""
    raw = _hex_bytes(value, 73)
    if raw[0] != 4:
        raise ValueError("Unrecognized scene count")
    result = []
    for index in range(4):
        start = 1 + index * 18
        record = raw[start:start + 18]
        mode = record[5]
        if record[0] not in (0, 1) or mode not in SCENE_NAMES:
            raise ValueError("Unrecognized scene record")
        result.append({
            "record_index": index,
            "name": SCENE_NAMES[mode],
            "mode_identifier": mode,
            "enabled": bool(record[0]),
            "presence_delay_seconds": int.from_bytes(record[1:3], "big"),
            "absence_delay_seconds": int.from_bytes(record[3:5], "big"),
            "start": _clock(record[14], record[15]),
            "end": _clock(record[16], record[17]),
        })
    if len({item["mode_identifier"] for item in result}) != 4:
        raise ValueError("Duplicate scene modes")
    return result


def patch_human_scene_enabled(value: str, mode: int, enabled: bool) -> str:
    """Draft patch: preserve every delay, time, action parameter and other scene."""
    if type(enabled) is not bool or type(mode) is not int:
        raise ValueError("Invalid scene switch value")
    scenes = decode_human_scenes(value)
    selected = next((item for item in scenes if item["mode_identifier"] == mode), None)
    if selected is None:
        raise ValueError("Unsupported scene mode")
    raw = _hex_bytes(value, 73)
    raw[1 + selected["record_index"] * 18] = int(enabled)
    return raw.hex()


def patch_human_scene(value: str, mode: int, changes: dict) -> str:
    """Patch calibrated time/delay bytes; preserve enable, action and other records."""
    validate_configuration_changes("human", changes)
    if type(mode) is not int or mode not in SCENE_NAMES:
        raise ValueError("Unsupported human scene")
    selected = next(item for item in decode_human_scenes(value) if item["mode_identifier"] == mode)
    raw = _hex_bytes(value, 73)
    offset = 1 + selected["record_index"] * 18
    for key, setting in changes.items():
        start = {"presence_delay_seconds": 1, "absence_delay_seconds": 3, "start": 14, "end": 16}[key]
        raw[offset + start:offset + start + 2] = (
            setting.to_bytes(2, "big") if key.endswith("_seconds") else _parse_clock(setting))
    return raw.hex()


def decode_motion_night_light(value: str) -> dict:
    raw = _hex_bytes(value, 9)
    if raw[0] not in (0, 1) or raw[1] != 8 or raw[8] & 0x80:
        raise ValueError("Unrecognized night-light settings")
    days = decode_repeat(raw[8])
    return {
        "enabled": bool(raw[0]),
        "start": _clock(raw[2], raw[3]),
        "end": _clock(raw[4], raw[5]),
        "absence_delay_seconds": int.from_bytes(raw[6:8], "big"),
        "repeat_mask": raw[8],
        "repeat_days": days,
        "repeat": "每天" if raw[8] == 0x7F else "、".join(DAY_NAMES[WEEKDAYS.index(day)] for day in days),
    }


def patch_motion_night_light_enabled(value: str, enabled: bool) -> str:
    """Preserve the entire captured schedule when changing only its enable byte."""
    if type(enabled) is not bool:
        raise ValueError("Invalid night-light switch value")
    decode_motion_night_light(value)
    raw = _hex_bytes(value, 9)
    raw[0] = int(enabled)
    return raw.hex()


def patch_motion_night_light(value: str, changes: dict) -> str:
    validate_configuration_changes("night", changes)
    decode_motion_night_light(value)
    raw = _hex_bytes(value, 9)
    for key, setting in changes.items():
        if key == "repeat_days":
            raw[8] = _repeat_mask(setting)
        elif key == "absence_delay_seconds":
            raw[6:8] = setting.to_bytes(2, "big")
        else:
            offset = 2 if key == "start" else 4
            raw[offset:offset + 2] = _parse_clock(setting)
    return raw.hex()



# Same picker sequence observed in the original miniapp: 10-minute choices
# through one hour, then half-hour choices up to the selected mode's base.
DURATION_OPTIONS = frozenset((*range(10, 61, 10), *range(90, 481, 30)))


def _base_minutes(mode, fan=None):
    if mode == 7:
        if type(fan) is not int or fan not in VENTILATION_ASSOCIATED_VALUES:
            return None
        return VENTILATION_ASSOCIATED_VALUES[fan]
    return SCHEDULE_BASE_MINUTES.get(mode)


def _duration_minutes(record, mode, warm):
    base = _base_minutes(mode, record[8])
    # Warm-up uses its own byte 6. Byte 7 has only been observed as zero.
    # Other appointments use the verified final uint16 big-endian offset.
    if base is None or (warm and record[7] != 0):
        return None
    delta = record[6] if warm else int.from_bytes(record[14:16], "big")
    duration = base - delta
    return duration if duration in DURATION_OPTIONS and duration <= base else None


def decode_schedules(value: str, *, warm: bool = False) -> list[dict]:
    """Read captured schedule headers; preserve uncalibrated action bytes and padding."""
    if type(warm) is not bool or not isinstance(value, str):
        raise ValueError("Invalid schedule kind")
    count = 1 if warm else 3
    # The official warm-up UI sends 11-byte records, while factory state has 16.
    sizes = (11, 16) if warm else (16,)
    size = next((n for n in sizes if len(value) == 2 * (1 + count * n)), None)
    if size is None:
        raise ValueError("Unrecognized schedule layout")
    raw = _hex_bytes(value, 1 + count * size)
    if raw[0] != count:
        raise ValueError("Unrecognized schedule count")
    expected = {18} if warm else set(SCHEDULE_NAMES)
    result = []
    for index in range(count):
        record = raw[1 + index * size:1 + (index + 1) * size]
        mode = record[5]
        if record[0] not in (0, 1) or record[1] != 8 or mode not in expected:
            raise ValueError("Unrecognized schedule record")
        days = decode_repeat(record[4], once=True)
        result.append({"record_index": index, "record_size": size,
                       "mode_identifier": mode, "enabled": bool(record[0]),
                       "start": _clock(record[2], record[3]),
                       "repeat_days": days,
                       "repeat": "不重复" if not days else "每天" if record[4] == 127 else "、".join(DAY_NAMES[WEEKDAYS.index(day)] for day in days),
                       "name": "预约暖房" if warm else SCHEDULE_NAMES[mode],
                       "fan_speed": record[8] if not warm and mode == 7 and 1 <= record[8] <= 5 else None,
                       "target_temperature": record[8] if warm and 30 <= record[8] <= 42 else None,
                       "duration_minutes": _duration_minutes(record, mode, warm)})
    if {r["mode_identifier"] for r in result} != expected:
        raise ValueError("Duplicate or missing schedule mode")
    return result


def patch_schedule_enabled(value: str, mode: int, enabled: bool, *, warm: bool = False) -> str:
    """Change a single enable bit without compacting or rebuilding any record."""
    if type(enabled) is not bool or type(mode) is not int:
        raise ValueError("Invalid schedule switch value")
    schedules = decode_schedules(value, warm=warm)
    selected = next((s for s in schedules if s["mode_identifier"] == mode), None)
    if selected is None:
        raise ValueError("Unsupported schedule mode")
    raw = bytearray.fromhex(value)
    raw[1 + selected["record_index"] * selected["record_size"]] = int(enabled)
    return raw.hex()


def patch_schedule(value: str, mode: int, changes: dict, *, warm=False) -> str:
    """Keep actual duration when changing speed; preserve other records and flags."""
    validate_configuration_changes("schedule", changes, mode=mode, warm=warm)
    selected = next(item for item in decode_schedules(value, warm=warm) if item["mode_identifier"] == mode)
    raw = bytearray.fromhex(value)
    offset = 1 + selected["record_index"] * selected["record_size"]
    duration = None
    if "duration_minutes" in changes or "fan_speed" in changes:
        duration = changes.get("duration_minutes", selected["duration_minutes"])
        new_fan = changes.get("fan_speed", selected["fan_speed"])
        limit = _base_minutes(mode, new_fan)
        if (duration is None or limit is None or duration not in DURATION_OPTIONS or duration > limit
                or (warm and raw[offset + 7] != 0)):
            raise ValueError("Duration is unknown or exceeds the new mode/speed limit; specify a compatible duration")
    for key, setting in changes.items():
        if key == "start":
            raw[offset + 2:offset + 4] = _parse_clock(setting)
        elif key == "repeat_days":
            raw[offset + 4] = _repeat_mask(setting, once=True)
        elif key in ("fan_speed", "target_temperature"):
            raw[offset + 8] = setting
    if duration is not None:
        limit = _base_minutes(mode, raw[offset + 8])
        delta = limit - duration
        if warm:
            raw[offset + 6] = delta
        else:
            raw[offset + 14:offset + 16] = delta.to_bytes(2, "big")
    return raw.hex()
