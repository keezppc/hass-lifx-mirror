"""Tests for LIFX Mirror discovery and its services."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

pytest.importorskip("lifx")

from lifx import HSBK, LifxError, MirrorLight
from lifx.products.quirks import MIRROR_LAYOUTS
from lifx.theme import ThemeLibrary

from custom_components.lifx_mirror import manager as manager_module
from custom_components.lifx_mirror import util as util_module
from custom_components.lifx_mirror.manager import (
    SET_STATE_SCHEMA,
    SET_ZONES_SCHEMA,
    LIFXMirrorManager,
)
from custom_components.lifx_mirror.util import gradient_zones, segment_zones

from .fakes import FakeHass

SERIAL = "d073d5000001"


@pytest.fixture(autouse=True)
def fast_settle(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Skip the settle wait and capture timers scheduled for a transition end."""
    monkeypatch.setattr(util_module, "STATE_SETTLE_DELAY", 0)
    timer = MagicMock()
    monkeypatch.setattr(util_module, "async_call_later", timer)
    return timer


class _FakeMirror(MirrorLight):
    """A MirrorLight with a real product layout and no connection."""

    layout = next(iter(MIRROR_LAYOUTS.values()))


def _coordinator(serial: str = SERIAL) -> SimpleNamespace:
    """Return a stand-in core coordinator whose device really is a MirrorLight."""
    device = object.__new__(_FakeMirror)
    for name in (
        "set_power",
        "turn_front_on",
        "turn_front_off",
        "turn_back_on",
        "turn_back_off",
        "apply_front_theme",
        "apply_back_theme",
    ):
        setattr(device, name, AsyncMock())
    return SimpleNamespace(
        device=device,
        data=SimpleNamespace(serial=serial, label="Mirror"),
        async_request_refresh=AsyncMock(),
        async_refresh=AsyncMock(),
    )


def _manager(*coordinators: SimpleNamespace) -> LIFXMirrorManager:
    """Return a manager whose hass reports the given loaded LIFX entries."""
    entries = [SimpleNamespace(runtime_data=c) for c in coordinators]
    hass = FakeHass()
    hass.config_entries = SimpleNamespace(
        async_loaded_entries=lambda _domain: entries,
        async_schedule_reload=MagicMock(),
    )
    entry = SimpleNamespace(entry_id="mirror-entry")
    return LIFXMirrorManager(hass, entry), entries


def test_scan_announces_each_mirror_once() -> None:
    """A Mirror is handed to the platform on the first scan only."""
    coordinator = _coordinator()
    manager, _ = _manager(coordinator)
    found = MagicMock()
    manager.set_discovery_callback(found)

    manager.async_scan()
    manager.async_scan()

    found.assert_called_once_with(coordinator)
    assert manager.coordinators == [coordinator]


def test_scan_reloads_when_core_replaced_the_coordinator() -> None:
    """Entities holding a coordinator that core has dropped are rebuilt."""
    old = _coordinator()
    manager, entries = _manager(old)
    manager.async_scan()

    entries[0].runtime_data = _coordinator()
    manager.async_scan()

    manager.hass.config_entries.async_schedule_reload.assert_called_once_with(
        "mirror-entry"
    )


def test_service_schema_defaults_match_the_documented_ones() -> None:
    """Omitted fields mean white at 3500K and full brightness, as before."""
    data = SET_STATE_SCHEMA({"device_id": "abc"})

    assert data["device_id"] == ["abc"]
    assert data["frontlight_hue"] == 0
    assert data["frontlight_saturation"] == 0
    assert data["frontlight_brightness"] == 100
    assert data["frontlight_kelvin"] == 3500
    assert data["backlight_brightness"] == 100
    assert data["transition"] == 0


def test_service_schema_rejects_out_of_range_values() -> None:
    """Values the device cannot take fail before any command is sent."""
    with pytest.raises(vol.Invalid):
        SET_STATE_SCHEMA({"device_id": "abc", "backlight_kelvin": 100})


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, SimpleNamespace]:
    """Replace the device registry with one holding the devices a test adds."""
    devices: dict[str, SimpleNamespace] = {}
    monkeypatch.setattr(
        manager_module.dr,
        "async_get",
        lambda _hass: SimpleNamespace(async_get=devices.get),
    )
    return devices


def _call(**data: float) -> SimpleNamespace:
    """Return a service call carrying schema-validated data."""
    return SimpleNamespace(data=SET_STATE_SCHEMA({"device_id": "dev", **data}))


@pytest.mark.asyncio
async def test_set_state_lights_both_components(registry) -> None:
    """Both components are lit with their own colors."""
    registry["dev"] = SimpleNamespace(
        identifiers={("lifx_mirror", "d0:73:d5:00:00:01")}
    )
    coordinator = _coordinator()
    manager, _ = _manager(coordinator)
    manager.async_scan()

    await manager.async_set_state(
        _call(
            frontlight_kelvin=2700,
            backlight_hue=120,
            backlight_saturation=100,
            backlight_brightness=50,
            transition=2,
        )
    )

    await manager.hass.drain()

    device = coordinator.device
    device.turn_front_on.assert_awaited_once_with(
        HSBK(hue=0, saturation=0, brightness=1, kelvin=2700), duration=2.0
    )
    device.turn_back_on.assert_awaited_once_with(
        HSBK(hue=120, saturation=1, brightness=0.5, kelvin=3500), duration=2.0
    )
    device.turn_front_off.assert_not_awaited()
    device.turn_back_off.assert_not_awaited()
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_state_with_one_component_at_zero_turns_it_off(registry) -> None:
    """Zero brightness turns that component off and lights the other."""
    registry["dev"] = SimpleNamespace(
        identifiers={("lifx_mirror", "d0:73:d5:00:00:01")}
    )
    coordinator = _coordinator()
    manager, _ = _manager(coordinator)
    manager.async_scan()

    await manager.async_set_state(_call(frontlight_brightness=0))

    device = coordinator.device
    device.turn_back_on.assert_awaited_once()
    device.turn_front_off.assert_awaited_once_with(duration=0.0)
    device.turn_front_on.assert_not_awaited()
    device.turn_back_off.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_state_with_both_at_zero_powers_off(registry) -> None:
    """Both components at zero is the same as powering the Mirror off."""
    registry["dev"] = SimpleNamespace(
        identifiers={("lifx_mirror", "d0:73:d5:00:00:01")}
    )
    coordinator = _coordinator()
    manager, _ = _manager(coordinator)
    manager.async_scan()

    await manager.async_set_state(
        _call(frontlight_brightness=0, backlight_brightness=0, transition=4)
    )

    coordinator.device.set_power.assert_awaited_once_with(
        False,  # noqa: FBT003
        duration=4.0,
    )
    coordinator.device.turn_front_on.assert_not_awaited()
    coordinator.device.turn_back_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_state_accepts_the_core_lifx_device(registry) -> None:
    """Targeting the Mirror's core LIFX device works as well as ours."""
    registry["dev"] = SimpleNamespace(identifiers={("lifx", SERIAL)})
    coordinator = _coordinator()
    manager, _ = _manager(coordinator)
    manager.async_scan()

    await manager.async_set_state(_call())

    coordinator.device.turn_front_on.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_state_skips_devices_that_are_not_mirrors(registry, caplog) -> None:
    """An unknown device is reported in the log and nothing is sent."""
    registry["dev"] = SimpleNamespace(identifiers={("hue", "abc")})
    coordinator = _coordinator()
    manager, _ = _manager(coordinator)
    manager.async_scan()

    await manager.async_set_state(_call())

    assert "No LIFX Mirror found" in caplog.text
    coordinator.device.turn_front_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_state_reports_a_device_failure(registry) -> None:
    """A Mirror that rejects a command is reported to the caller."""
    registry["dev"] = SimpleNamespace(
        identifiers={("lifx_mirror", "d0:73:d5:00:00:01")}
    )
    coordinator = _coordinator()
    coordinator.device.turn_front_on.side_effect = LifxError("no reply")
    manager, _ = _manager(coordinator)
    manager.async_scan()

    with pytest.raises(HomeAssistantError, match="did not accept"):
        await manager.async_set_state(_call())


def _zones_call(**data: object) -> SimpleNamespace:
    """Return a set_zones service call carrying schema-validated data."""
    return SimpleNamespace(data=SET_ZONES_SCHEMA({"device_id": "dev", **data}))


def _registered_manager(
    registry: dict[str, SimpleNamespace],
) -> tuple[LIFXMirrorManager, SimpleNamespace]:
    """Return a manager that knows one Mirror, registered as device "dev"."""
    registry["dev"] = SimpleNamespace(
        identifiers={("lifx_mirror", "d0:73:d5:00:00:01")}
    )
    coordinator = _coordinator()
    manager, _ = _manager(coordinator)
    manager.async_scan()
    return manager, coordinator


RED = {"hue": 0, "saturation": 100, "brightness": 50}
BLUE = {"hue": 240, "saturation": 100, "brightness": 100, "kelvin": 4000}


def test_zones_schema_takes_exactly_one_source() -> None:
    """Two sources, or none, are refused before anything is sent."""
    with pytest.raises(vol.Invalid):
        SET_ZONES_SCHEMA({"device_id": "dev", "component": "front"})
    with pytest.raises(vol.Invalid):
        SET_ZONES_SCHEMA(
            {
                "device_id": "dev",
                "component": "front",
                "theme": "evening",
                "gradient": [RED],
            }
        )


@pytest.mark.asyncio
async def test_set_zones_sends_each_colour_to_its_zone(registry) -> None:
    """A full list of colours reaches the chosen light in zone order."""
    manager, coordinator = _registered_manager(registry)
    device = coordinator.device
    colors = [RED if i % 2 else BLUE for i in range(device.front_zone_count)]

    await manager.async_set_zones(
        _zones_call(component="front", colors=colors, transition=2)
    )
    await manager.hass.drain()

    sent = device.turn_front_on.await_args.args[0]
    assert device.turn_front_on.await_args.kwargs == {"duration": 2.0}
    assert sent[0] == HSBK(hue=240, saturation=1, brightness=1, kelvin=4000)
    assert sent[1] == HSBK(hue=0, saturation=1, brightness=0.5, kelvin=3500)
    assert len(sent) == device.front_zone_count
    device.turn_back_on.assert_not_awaited()
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_zones_refuses_the_wrong_number_of_colours(registry) -> None:
    """A colour list that does not match the zones is an error, not a guess."""
    manager, coordinator = _registered_manager(registry)

    with pytest.raises(ServiceValidationError, match="25 zones, but 3 colors"):
        await manager.async_set_zones(
            _zones_call(component="front", colors=[RED, RED, RED])
        )

    coordinator.device.turn_front_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_zones_spreads_a_gradient_over_the_mirror(registry) -> None:
    """A gradient is laid out along the real zone positions of the light."""
    manager, coordinator = _registered_manager(registry)
    device = coordinator.device

    await manager.async_set_zones(_zones_call(component="back", gradient=[RED, BLUE]))

    expected = gradient_zones(
        device.back_positions,
        [
            HSBK(hue=0, saturation=1, brightness=0.5, kelvin=3500),
            HSBK(hue=240, saturation=1, brightness=1, kelvin=4000),
        ],
    )
    device.turn_back_on.assert_awaited_once_with(expected, duration=0.0)
    device.turn_front_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_zones_lights_segments(registry) -> None:
    """Segments light their stretch of the light and leave the rest dark."""
    manager, coordinator = _registered_manager(registry)
    device = coordinator.device

    await manager.async_set_zones(
        _zones_call(component="front", segments=[{"start": 0, "end": 50, **RED}])
    )

    expected = segment_zones(
        device.front_positions,
        [(0, 50, HSBK(hue=0, saturation=1, brightness=0.5, kelvin=3500))],
    )
    device.turn_front_on.assert_awaited_once_with(expected, duration=0.0)


@pytest.mark.asyncio
async def test_set_zones_refuses_a_backwards_segment(registry) -> None:
    """A segment that ends before it starts is an error."""
    manager, coordinator = _registered_manager(registry)

    with pytest.raises(ServiceValidationError, match="start must be below its end"):
        await manager.async_set_zones(
            _zones_call(component="front", segments=[{"start": 60, "end": 40}])
        )

    coordinator.device.turn_front_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_zones_refuses_all_dark_zones(registry) -> None:
    """Every zone at zero brightness would light nothing, so it is refused."""
    manager, coordinator = _registered_manager(registry)

    with pytest.raises(ServiceValidationError, match="brightness above 0"):
        await manager.async_set_zones(
            _zones_call(component="front", gradient=[{"brightness": 0}])
        )

    coordinator.device.turn_front_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_zones_applies_a_theme_to_one_light(registry) -> None:
    """A theme is painted on the chosen light only, powering the Mirror on."""
    manager, coordinator = _registered_manager(registry)
    device = coordinator.device

    await manager.async_set_zones(
        _zones_call(component="back", theme="Evening", transition=1)
    )

    device.apply_back_theme.assert_awaited_once()
    theme = device.apply_back_theme.await_args.args[0]
    assert theme.palette_equals(ThemeLibrary.get("evening"))
    assert device.apply_back_theme.await_args.kwargs == {
        "power_on": True,
        "duration": 1.0,
    }
    device.apply_front_theme.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_zones_names_the_themes_when_one_is_unknown(registry) -> None:
    """An unknown theme is refused with the list of names to choose from."""
    manager, coordinator = _registered_manager(registry)

    with pytest.raises(ServiceValidationError, match="Unknown theme.*evening"):
        await manager.async_set_zones(_zones_call(component="front", theme="nope"))

    coordinator.device.apply_front_theme.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_zones_skips_devices_that_are_not_mirrors(registry, caplog) -> None:
    """An unknown device is reported in the log and nothing is sent."""
    registry["dev"] = SimpleNamespace(identifiers={("hue", "abc")})
    coordinator = _coordinator()
    manager, _ = _manager(coordinator)
    manager.async_scan()

    await manager.async_set_zones(_zones_call(component="front", theme="evening"))

    assert "No LIFX Mirror found" in caplog.text
    coordinator.device.apply_front_theme.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_zones_reports_a_device_failure(registry) -> None:
    """A Mirror that rejects the zones is reported to the caller."""
    manager, coordinator = _registered_manager(registry)
    coordinator.device.apply_front_theme.side_effect = LifxError("no reply")

    with pytest.raises(HomeAssistantError, match="did not accept"):
        await manager.async_set_zones(_zones_call(component="front", theme="evening"))

    coordinator.async_request_refresh.assert_not_awaited()
