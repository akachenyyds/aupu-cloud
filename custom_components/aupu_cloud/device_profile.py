"""Product 10363 fields backed by this device's captured commands and reports."""

OPERATING_FUNCTIONS = {0: "关闭", 18: "AI恒温暖", 21: "除臭除菌", 7: "换气", 2: "吹风", 9: "普通干燥", 4: "恒温干燥"}
VENTILATION_LEVELS = (1, 2, 3, 4, 5)
MIN_TEMPERATURE = 30
MAX_TEMPERATURE = 42
TEMPERATURE_STEP = 1

# User confirmed actual fan-speed change and UI level 1 = protocol 1,
# with original UI level 5 = protocol 5. Their appointment bases now match independently captured duration offsets.
VENTILATION_ASSOCIATED_VALUES = {1: 480, 2: 300, 3: 180, 4: 60, 5: 30}

# Verified appointment duration bases, in minutes.
SCHEDULE_BASE_MINUTES = {18: 90, 4: 120, 21: 60}


def read_device_temperature(value, fault):
    """T5-Pro miniapp module 15180 defines Temperature as prop.4.1 uint8.

    Module 22883 defines E1 temperature-probe fault as bit 1. Preserve the
    device's integer reading; never substitute the target or apply an offset.
    """
    if (type(value) is not int or not 0 <= value <= 255
            or type(fault) is not int or not 0 <= fault <= 255 or fault & 1):
        return None
    return value
