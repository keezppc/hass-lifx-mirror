"""Discovery and service handling for LIFX Mirror."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import voluptuous as vol
from homeassistant.components.light import ATTR_TRANSITION
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from lifx import HSBK, LifxError, MirrorLight

from .const import (
    _LOGGER,
    ATTR_BACKLIGHT_BRIGHTNESS,
    ATTR_BACKLIGHT_HUE,
    ATTR_BACKLIGHT_KELVIN,
    ATTR_BACKLIGHT_SATURATION,
    ATTR_FRONTLIGHT_BRIGHTNESS,
    ATTR_FRONTLIGHT_HUE,
    ATTR_FRONTLIGHT_KELVIN,
    ATTR_FRONTLIGHT_SATURATION,
    DEFAULT_KELVIN,
    DOMAIN,
    LIFX_DOMAIN,
)
from .util import (
    async_schedule_refresh_after_command,
    find_mirror_coordinators,
    normalize_serial,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.components.lifx.coordinator import LIFXUpdateCoordinator
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall

type LIFXMirrorConfigEntry = ConfigEntry[LIFXMirrorManager]

_HUE = vol.All(vol.Coerce(float), vol.Range(min=0, max=360))
_PERCENT = vol.All(vol.Coerce(float), vol.Range(min=0, max=100))
_KELVIN = vol.All(vol.Coerce(int), vol.Range(min=1500, max=9000))

SET_STATE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_FRONTLIGHT_HUE, default=0): _HUE,
        vol.Optional(ATTR_FRONTLIGHT_SATURATION, default=0): _PERCENT,
        vol.Optional(ATTR_FRONTLIGHT_BRIGHTNESS, default=100): _PERCENT,
        vol.Optional(ATTR_FRONTLIGHT_KELVIN, default=DEFAULT_KELVIN): _KELVIN,
        vol.Optional(ATTR_BACKLIGHT_HUE, default=0): _HUE,
        vol.Optional(ATTR_BACKLIGHT_SATURATION, default=0): _PERCENT,
        vol.Optional(ATTR_BACKLIGHT_BRIGHTNESS, default=100): _PERCENT,
        vol.Optional(ATTR_BACKLIGHT_KELVIN, default=DEFAULT_KELVIN): _KELVIN,
        vol.Optional(ATTR_TRANSITION, default=0): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=3600)
        ),
    }
)


def _service_color(
    data: dict[str, Any], hue: str, saturation: str, brightness: str, kelvin: str
) -> HSBK | None:
    """Return the color a service call asks for, or None if it asks for off."""
    if data[brightness] <= 0:
        return None
    return HSBK(
        hue=data[hue],
        saturation=data[saturation] / 100,
        brightness=data[brightness] / 100,
        kelvin=data[kelvin],
    )


class LIFXMirrorManager:
    """Track the Mirrors in the core LIFX integration and drive them as a pair."""

    def __init__(self, hass: HomeAssistant, entry: LIFXMirrorConfigEntry) -> None:
        """Initialize the manager."""
        self.hass = hass
        self.entry = entry
        self._coordinators: dict[str, LIFXUpdateCoordinator] = {}
        self._discovery_callback: Callable[[LIFXUpdateCoordinator], None] | None = None

    @property
    def coordinators(self) -> list[LIFXUpdateCoordinator]:
        """Return the core LIFX coordinators of the known Mirrors."""
        return list(self._coordinators.values())

    @callback
    def set_discovery_callback(
        self, discovery_callback: Callable[[LIFXUpdateCoordinator], None]
    ) -> None:
        """Set the function called with each newly found Mirror."""
        self._discovery_callback = discovery_callback

    @callback
    def async_scan(self, _now: datetime | None = None) -> None:
        """Find Mirrors that core LIFX has set up since the last scan."""
        for coordinator in find_mirror_coordinators(self.hass):
            serial = normalize_serial(coordinator.data.serial)
            known = self._coordinators.get(serial)
            if known is coordinator:
                continue
            if known is not None:
                # Core LIFX was reloaded, so the entities hold a dead coordinator
                _LOGGER.debug("LIFX entry for %s was reloaded; reloading", serial)
                self.hass.config_entries.async_schedule_reload(self.entry.entry_id)
                return
            self._coordinators[serial] = coordinator
            if self._discovery_callback is not None:
                self._discovery_callback(coordinator)

    def _coordinator_for_device(self, device_id: str) -> LIFXUpdateCoordinator | None:
        """Return the coordinator of a device registry entry, if it is a Mirror."""
        device_entry = dr.async_get(self.hass).async_get(device_id)
        if device_entry is None:
            return None
        for domain, identifier in device_entry.identifiers:
            if domain not in (DOMAIN, LIFX_DOMAIN):
                continue
            if coordinator := self._coordinators.get(normalize_serial(identifier)):
                return coordinator
        return None

    async def async_set_state(self, call: ServiceCall) -> None:
        """Handle the set_state service call."""
        data = call.data
        front = _service_color(
            data,
            ATTR_FRONTLIGHT_HUE,
            ATTR_FRONTLIGHT_SATURATION,
            ATTR_FRONTLIGHT_BRIGHTNESS,
            ATTR_FRONTLIGHT_KELVIN,
        )
        back = _service_color(
            data,
            ATTR_BACKLIGHT_HUE,
            ATTR_BACKLIGHT_SATURATION,
            ATTR_BACKLIGHT_BRIGHTNESS,
            ATTR_BACKLIGHT_KELVIN,
        )
        transition = data[ATTR_TRANSITION]

        for device_id in data[ATTR_DEVICE_ID]:
            coordinator = self._coordinator_for_device(device_id)
            if coordinator is None:
                _LOGGER.warning("No LIFX Mirror found for device ID %s", device_id)
                continue
            await self._async_apply(coordinator, front, back, transition)

    async def _async_apply(
        self,
        coordinator: LIFXUpdateCoordinator,
        front: HSBK | None,
        back: HSBK | None,
        transition: float,
    ) -> None:
        """Set both components of one Mirror, where None means off."""
        device = cast("MirrorLight", coordinator.device)
        try:
            if front is None and back is None:
                await device.set_power(False, duration=transition)
            else:
                # Lighting a component first lets the library blank the other one
                # when the Mirror is off, rather than flashing its old colors
                if front is not None:
                    await device.turn_front_on(front, duration=transition)
                if back is not None:
                    await device.turn_back_on(back, duration=transition)
                if front is None:
                    await device.turn_front_off(duration=transition)
                if back is None:
                    await device.turn_back_off(duration=transition)
        except LifxError as err:
            msg = f"{coordinator.data.label} did not accept the command: {err}"
            raise HomeAssistantError(msg) from err
        async_schedule_refresh_after_command(self.hass, coordinator, transition)
