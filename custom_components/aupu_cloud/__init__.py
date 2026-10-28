"""AUPU cloud for the verified Q360T5-Pro profile."""
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform

from .coordinator import AupuCoordinator

PLATFORMS = [Platform.LIGHT, Platform.SENSOR, Platform.SELECT, Platform.CLIMATE, Platform.BINARY_SENSOR, Platform.SWITCH]


async def async_setup_entry(hass, entry):
    coordinator = AupuCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await coordinator.async_shutdown()
        raise
    entry.runtime_data = coordinator
    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, coordinator.async_handle_stop))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await coordinator.async_shutdown()
        raise
    return True


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_shutdown()
    return True
