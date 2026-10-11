"""Tests for LIFX Mirror discovery and the set_state service."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError

pytest.importorskip("lifx")

from lifx import HSBK, LifxError, MirrorLight

from custom_components.lifx_mirror import manager as manager_module
from custom_components.lifx_mirror import util as util_module
from custom_components.lifx_mirror.manager import (
    SET_STATE_SCHEMA,
    LIFXMirrorManager,
)

from .fakes import FakeHass

SERIAL = "d073d5000001"


@pytest.fixture(autouse=True)
def fast_settle(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Skip the settle wait and capture timers scheduled for a transition end."""
    monkeypatch.setattr(util_module, "STATE_SETTLE_DELAY", 0)
    timer = MagicMock()
    monkeypatch.setattr(util_module, "async_call_later", timer)
    return timer


def _coordinator(serial: str = SERIAL) -> SimpleNamespace:
    """Return a stand-in core coordinator whose device really is a MirrorLight."""
    device = object.__new__(MirrorLight)
    for name in (
        "set_power",
        "turn_front_on",
        "turn_front_off",
        "turn_back_on",
        "turn_back_off",
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
