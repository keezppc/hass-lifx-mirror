"""Extra support for LIFX Mirror."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started

from .const import DISCOVERY_INTERVAL, DOMAIN, SERVICE_LIFX_MIRROR_SET_STATE
from .manager import SET_STATE_SCHEMA, LIFXMirrorConfigEntry, LIFXMirrorManager

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

PLATFORMS: list[Platform] = [Platform.LIGHT]


async def async_setup_entry(hass: HomeAssistant, entry: LIFXMirrorConfigEntry) -> bool:
    """Set up LIFX Mirror."""
    manager = LIFXMirrorManager(hass, entry)
    manager.async_scan()
    entry.runtime_data = manager

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    hass.services.async_register(
        DOMAIN,
        SERVICE_LIFX_MIRROR_SET_STATE,
        manager.async_set_state,
        schema=SET_STATE_SCHEMA,
    )

    # Core LIFX entries may finish loading after this one, so look again once
    # Home Assistant has started and then at a regular interval
    entry.async_on_unload(async_at_started(hass, manager.async_scan))
    entry.async_on_unload(
        async_track_time_interval(hass, manager.async_scan, DISCOVERY_INTERVAL)
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: LIFXMirrorConfigEntry) -> bool:
    """Unload LIFX Mirror config entry."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.services.async_remove(DOMAIN, SERVICE_LIFX_MIRROR_SET_STATE)
    return unloaded
