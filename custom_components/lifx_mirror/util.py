"""Helpful methods."""

from __future__ import annotations

import asyncio
from functools import cache
from typing import TYPE_CHECKING, Any, TypedDict, cast

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
)
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_call_later
from lifx import HSBK, MirrorLight
from lifx.theme import ThemeLibrary
from lifx.theme.generators import MatrixGenerator

from .const import DEFAULT_KELVIN, LIFX_DOMAIN, MIRROR_WIDTH, STATE_SETTLE_DELAY

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.components.lifx.coordinator import LIFXUpdateCoordinator
    from homeassistant.core import HomeAssistant
    from lifx.theme import Theme


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


@cache
def theme_effects() -> dict[str, str]:
    """Return the LIFX themes offered as light effects, from display name to key."""
    effects: dict[str, str] = {}
    for key in ThemeLibrary.get_available_themes():
        theme = ThemeLibrary.get(key)
        # Retired themes duplicate a current one, so they would only clutter the list
        if theme.disposition in ("deprecated", "renamed"):
            continue
        effects[theme.name or key] = key
    return dict(sorted(effects.items(), key=lambda item: item[0].casefold()))


def find_theme(name: str) -> Theme | None:
    """Return the LIFX theme with this key or display name, ignoring case."""
    try:
        return ThemeLibrary.get(name)
    except KeyError:
        pass
    wanted = name.strip().casefold()
    for display_name, key in theme_effects().items():
        if display_name.casefold() == wanted:
            return ThemeLibrary.get(key)
    return None


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


def scale_zones(colors: list[HSBK], changes: HSBKChanges) -> list[HSBK]:
    """
    Apply requested changes to every zone, keeping the zones' brightness profile.

    A new hue, saturation or kelvin replaces that part of every zone. A new
    brightness becomes the brightest zone's brightness and the other zones keep
    their share of it, so a gradient dims without flattening. A component that is
    dark comes on at the requested brightness, or at full, in every zone.
    """
    peak = max(color.brightness for color in colors)
    brightness = changes["brightness"]
    colour_only = {key: value for key, value in changes.items() if key != "brightness"}
    if peak == 0:
        base = {**colour_only, "brightness": 1.0 if brightness is None else brightness}
        return [merge_hsbk(color, cast("HSBKChanges", base)) for color in colors]
    zones: list[HSBK] = []
    for color in colors:
        zone = {**colour_only}
        if brightness is not None:
            zone["brightness"] = color.brightness / peak * brightness
        zones.append(merge_hsbk_keep_dark(color, cast("HSBKChanges", zone)))
    return zones


def merge_hsbk_keep_dark(base: HSBK, changes: HSBKChanges) -> HSBK:
    """Apply changes to one zone, leaving a dark zone dark."""
    try:
        return base.replace(**changes)
    except ValueError as err:
        msg = f"Invalid color: {err}"
        raise ServiceValidationError(msg) from err


def gradient_zones(positions: tuple[int, ...], stops: list[HSBK]) -> list[HSBK]:
    """
    Spread colour stops evenly along the Mirror's length, one colour per zone.

    The Mirror's buffer is 4 wide and 13 tall; a zone's row is its position along
    the long axis, and both sides of the capsule at the same row share a colour.
    Hues blend along the shorter way round the colour wheel.
    """
    if len(stops) == 1:
        return [stops[0]] * len(positions)
    last_row = max(position // MIRROR_WIDTH for position in positions) or 1
    zones: list[HSBK] = []
    for position in positions:
        along = position // MIRROR_WIDTH / last_row * (len(stops) - 1)
        index = min(int(along), len(stops) - 2)
        zones.append(_blend(stops[index], stops[index + 1], along - index))
    return zones


def theme_zones(device: MirrorLight, component: str, theme: Theme) -> list[HSBK]:
    """
    Render a theme over the Mirror's matrix and pick one light's zones from it.

    This is what the library does when it applies a theme, done here so the
    colours can be rescaled to a requested brightness before they are sent.
    """
    layout = device.layout
    generator = MatrixGenerator([((0, 0), (layout.width, layout.height))])
    rendered = generator.get_theme_colors(theme)[0]
    return [
        rendered[position] for position in getattr(device, f"{component}_positions")
    ]


def segment_zones(
    positions: tuple[int, ...], segments: list[tuple[float, float, HSBK]]
) -> list[HSBK]:
    """
    Colour stretches of the Mirror's length, leaving the rest dark.

    Each segment covers a start to an end in percent along the length, from the
    end opposite the buttons (0) to the end with the buttons (100). A zone belongs
    to a segment when the middle of its row falls within it, so a third of the
    length is a third of the rows. Later segments win where they overlap.
    """
    rows = max(position // MIRROR_WIDTH for position in positions) + 1
    dark = HSBK(hue=0, saturation=0, brightness=0, kelvin=DEFAULT_KELVIN)
    zones = [dark] * len(positions)
    for index, position in enumerate(positions):
        along = (position // MIRROR_WIDTH + 0.5) / rows * 100
        for start, end, color in segments:
            if start <= along < end:
                zones[index] = color
    return zones


def _blend(start: HSBK, end: HSBK, fraction: float) -> HSBK:
    """Return the colour a fraction of the way from start to end."""
    arc = (end.hue - start.hue + 180) % 360 - 180
    return HSBK(
        hue=(start.hue + arc * fraction) % 360,
        saturation=start.saturation + (end.saturation - start.saturation) * fraction,
        brightness=start.brightness + (end.brightness - start.brightness) * fraction,
        kelvin=round(start.kelvin + (end.kelvin - start.kelvin) * fraction),
    )


async def _async_refresh_after_command(
    hass: HomeAssistant, coordinator: LIFXUpdateCoordinator, duration: float
) -> None:
    """Read a Mirror back once the state it was just given has settled."""
    await asyncio.sleep(STATE_SETTLE_DELAY)
    await coordinator.async_request_refresh()

    if duration > 0:

        async def _async_refresh(_now: datetime) -> None:
            await coordinator.async_refresh()

        async_call_later(hass, duration, _async_refresh)


@callback
def async_schedule_refresh_after_command(
    hass: HomeAssistant, coordinator: LIFXUpdateCoordinator, duration: float
) -> None:
    """
    Read a Mirror back after a command, without tying the read to the caller.

    A read straight after a command still returns the state from before it, which
    makes the lights flap, so the read waits. A transition is read again when it
    ends, as the state at its start is not where it finishes.

    The read runs as its own task because Home Assistant marks the whole
    connection failed when a task is cancelled inside a refresh, which makes every
    light on it unavailable until the next poll. An automation in restart mode
    cancels its previous run, and that run must not be the one doing the refresh.
    """
    hass.async_create_background_task(
        _async_refresh_after_command(hass, coordinator, duration),
        name="lifx_mirror refresh after command",
    )
