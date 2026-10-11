"""LIFX Mirror light entities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.light import (
    ATTR_EFFECT,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo, format_mac
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from lifx import LifxError
from lifx.theme import ThemeLibrary

from .const import COMPONENT_BACK, COMPONENT_FRONT, DOMAIN
from .util import (
    async_schedule_refresh_after_command,
    hsbk_changes,
    scale_zones,
    theme_effects,
)

if TYPE_CHECKING:
    from homeassistant.components.lifx.coordinator import LIFXUpdateCoordinator
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
    from lifx import HSBK, MirrorLight, MirrorLightState
    from lifx.theme import Theme

    from .manager import LIFXMirrorConfigEntry

PARALLEL_UPDATES = 1

DEFAULT_MIN_KELVIN = 1500
DEFAULT_MAX_KELVIN = 9000


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LIFXMirrorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up LIFX Mirror light entities."""
    manager = entry.runtime_data

    @callback
    def _add_mirror_entities(coordinator: LIFXUpdateCoordinator) -> None:
        async_add_entities(
            [
                LIFXMirrorLight(coordinator, COMPONENT_FRONT),
                LIFXMirrorLight(coordinator, COMPONENT_BACK),
            ]
        )

    for coordinator in manager.coordinators:
        _add_mirror_entities(coordinator)

    manager.set_discovery_callback(_add_mirror_entities)


class LIFXMirrorLight(CoordinatorEntity["LIFXUpdateCoordinator"], LightEntity):
    """One lighting component, front or back, of a LIFX Mirror."""

    _attr_has_entity_name = True
    _attr_supported_features = LightEntityFeature.TRANSITION | LightEntityFeature.EFFECT
    _unrecorded_attributes = frozenset({"zones"})

    def __init__(self, coordinator: LIFXUpdateCoordinator, component: str) -> None:
        """Instantiate the light for one component of a Mirror."""
        super().__init__(coordinator)
        self._component = component
        self._attr_supported_color_modes = {ColorMode.COLOR_TEMP, ColorMode.HS}
        # A theme is a one-off palette, not a running effect, so none is ever current
        self._attr_effect_list = list(theme_effects())

        state = coordinator.data
        mac = format_mac(state.serial)
        self._attr_name = "Frontlight" if component == COMPONENT_FRONT else "Backlight"
        self._attr_unique_id = f"{mac}_{component}light"
        self._attr_min_color_temp_kelvin = (
            state.capabilities.kelvin_min or DEFAULT_MIN_KELVIN
        )
        self._attr_max_color_temp_kelvin = (
            state.capabilities.kelvin_max or DEFAULT_MAX_KELVIN
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            connections={(dr.CONNECTION_NETWORK_MAC, mac)},
            manufacturer="LIFX",
            name=state.label,
            serial_number=state.serial,
            model=state.model,
            sw_version=(
                f"{state.host_firmware.version_major}."
                f"{state.host_firmware.version_minor}"
            ),
            suggested_area=state.group.label,
        )

    @property
    def _device(self) -> MirrorLight:
        """Return the library object for the Mirror."""
        return cast("MirrorLight", self.coordinator.device)

    @property
    def _state(self) -> MirrorLightState:
        """Return the latest state the core coordinator read from the Mirror."""
        return cast("MirrorLightState", self.coordinator.data)

    @property
    def _colors(self) -> list[HSBK]:
        """Return the colors of every zone of this component."""
        state = self._state
        if self._component == COMPONENT_FRONT:
            return state.front_colors
        return state.back_colors

    @property
    def is_on(self) -> bool:
        """Return true if the Mirror is on and a zone of this component is lit."""
        state = self._state
        if self._component == COMPONENT_FRONT:
            return state.front_is_on
        return state.back_is_on

    @property
    def brightness(self) -> int:
        """Return the brightest zone of this component, between 0 and 255."""
        return round(max(color.brightness for color in self._colors) * 255)

    @property
    def hs_color(self) -> tuple[float, float]:
        """Return the hue and saturation of the first zone, as degrees and percent."""
        color = self._colors[0]
        return color.hue, color.saturation * 100

    @property
    def color_temp_kelvin(self) -> int:
        """Return the color temperature of the first zone."""
        return self._colors[0].kelvin

    @property
    def color_mode(self) -> ColorMode:
        """Return how the first zone is colored."""
        if self._colors[0].saturation > 0:
            return ColorMode.HS
        return ColorMode.COLOR_TEMP

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return every zone's color, in zone order."""
        return {
            "zones": [
                {
                    "hue": round(color.hue, 1),
                    "saturation": round(color.saturation * 100, 1),
                    "brightness": round(color.brightness * 100, 1),
                    "kelvin": color.kelvin,
                }
                for color in self._colors
            ]
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn this component on, restoring its last colors if none are given."""
        duration = float(kwargs.get(ATTR_TRANSITION, 0))
        if ATTR_EFFECT in kwargs:
            await self._async_apply_theme(kwargs[ATTR_EFFECT], duration)
            return
        changes = hsbk_changes(**kwargs)
        color = (
            None
            if all(value is None for value in changes.values())
            else scale_zones(self._colors, changes)
        )
        await self._async_command(f"turn_{self._component}_on", color, duration)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn this component off, powering the Mirror off if the other is off."""
        duration = float(kwargs.get(ATTR_TRANSITION, 0))
        await self._async_command(f"turn_{self._component}_off", None, duration)

    async def _async_apply_theme(self, effect: str, duration: float) -> None:
        """Paint this component with the LIFX theme an effect name stands for."""
        key = theme_effects().get(effect)
        if key is None:
            msg = f"Unknown effect {effect!r}; choose one of this light's effects"
            raise ServiceValidationError(msg)
        await self._async_command(
            f"apply_{self._component}_theme",
            ThemeLibrary.get(key),
            duration,
            power_on=True,
        )

    async def _async_command(
        self,
        method: str,
        argument: HSBK | list[HSBK] | Theme | None,
        duration: float,
        **options: Any,
    ) -> None:
        """Send a command to the library, then read the result back."""
        try:
            await getattr(self._device, method)(argument, duration=duration, **options)
        except LifxError as err:
            msg = f"{self._state.label} did not accept the command: {err}"
            raise HomeAssistantError(msg) from err
        async_schedule_refresh_after_command(self.hass, self.coordinator, duration)
