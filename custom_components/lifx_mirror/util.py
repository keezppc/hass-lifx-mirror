"""Helpful methods."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, TypedDict

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_call_later
from lifx import HSBK, MirrorLight

from .const import DEFAULT_KELVIN, LIFX_DOMAIN, STATE_SETTLE_DELAY

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.components.lifx.coordinator import LIFXUpdateCoordinator
    from homeassistant.core import HomeAssistant


class HSBKChanges(TypedDict):
    """Color components a Home Assistant service call asks to change."""

    hue: float | None
    saturation: float | None
    brightness: float | None
    kelvin: int | None


def find_mirror_coordinators(hass: HomeAssistant) -> list[LIFXUpdateCoordinator]:
    """Return the core LIFX coordinators whose device is a LIFX Mirror."""
    coordinators: list[LIFXUpdateCoordinator] = []
    for entry in hass.config_entries.async_loaded_entries(LIFX_DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        if isinstance(getattr(coordinator, "device", None), MirrorLight):
            coordinators.append(coordinator)
    return coordinators


def normalize_serial(value: str) -> str:
    """Return a serial or MAC string as lowercase hex without separators."""
    return value.replace(":", "").replace("-", "").lower()


def hsbk_changes(**kwargs: Any) -> HSBKChanges:
    """Return the color components a light.turn_on call asks to change."""
    hue: float | None = None
    saturation: float | None = None
    brightness: float | None = None
    kelvin: int | None = None

    if ATTR_HS_COLOR in kwargs:
        hue, saturation = kwargs[ATTR_HS_COLOR]
        saturation /= 100
        kelvin = DEFAULT_KELVIN

    if ATTR_COLOR_TEMP_KELVIN in kwargs:
        kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
        saturation = 0.0

    if ATTR_BRIGHTNESS in kwargs:
        brightness = kwargs[ATTR_BRIGHTNESS] / 255

    return HSBKChanges(
        hue=hue, saturation=saturation, brightness=brightness, kelvin=kelvin
    )


def merge_hsbk(base: HSBK, changes: HSBKChanges) -> HSBK:
    """
    Apply requested changes to a color, rejecting values the device cannot take.

    A light that is currently dark and is asked for a color without a brightness
    comes on at full brightness, as the device cannot be turned on at zero.
    """
    try:
        merged = base.replace(**changes)
        if merged.brightness == 0:
            merged = merged.replace(brightness=1.0)
    except ValueError as err:
        msg = f"Invalid color: {err}"
        raise ServiceValidationError(msg) from err
    return merged


async def async_refresh_after_command(
    hass: HomeAssistant, coordinator: LIFXUpdateCoordinator, duration: float
) -> None:
    """
    Read a Mirror back once the state it was just given has settled.

    A read straight after a command still returns the state from before it, which
    makes the lights flap, so the read waits. A transition is read again when it
    ends, as the state at its start is not where it finishes.
    """
    await asyncio.sleep(STATE_SETTLE_DELAY)
    await coordinator.async_request_refresh()

    if duration > 0:

        async def _async_refresh(_now: datetime) -> None:
            await coordinator.async_refresh()

        async_call_later(hass, duration, _async_refresh)
