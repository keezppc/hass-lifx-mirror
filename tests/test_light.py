"""Tests for the LIFX Mirror light entities."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ATTR_TRANSITION,
    ColorMode,
    LightEntityFeature,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

pytest.importorskip("lifx")

from lifx import HSBK, LifxError
from lifx.theme import ThemeLibrary

from custom_components.lifx_mirror import util as util_module
from custom_components.lifx_mirror.const import (
    COMPONENT_BACK,
    COMPONENT_FRONT,
)
from custom_components.lifx_mirror.light import LIFXMirrorLight

from .fakes import FakeHass


@pytest.fixture(autouse=True)
def fast_settle(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Skip the settle wait and capture timers scheduled for a transition end."""
    monkeypatch.setattr(util_module, "STATE_SETTLE_DELAY", 0)
    timer = MagicMock()
    monkeypatch.setattr(util_module, "async_call_later", timer)
    return timer


@pytest.fixture(autouse=True)
def fake_hass(monkeypatch: pytest.MonkeyPatch) -> FakeHass:
    """Give every light the same stand-in Home Assistant."""
    hass = FakeHass()
    monkeypatch.setattr(LIFXMirrorLight, "hass", hass, raising=False)
    return hass


WARM = HSBK(hue=30, saturation=0, brightness=0.5, kelvin=2700)
GREEN = HSBK(hue=120, saturation=1, brightness=1, kelvin=3500)
DARK = HSBK(hue=0, saturation=0, brightness=0, kelvin=3500)


def _coordinator(
    *, front: list[HSBK], back: list[HSBK], power: int = 65535
) -> SimpleNamespace:
    """Return a stand-in for core's LIFX coordinator holding a Mirror."""
    state = SimpleNamespace(
        serial="d073d5000001",
        label="Mirror",
        model="LIFX Mirror",
        capabilities=SimpleNamespace(kelvin_min=1500, kelvin_max=9000),
        host_firmware=SimpleNamespace(version_major=4, version_minor=145),
        group=SimpleNamespace(label="Bathroom"),
        front_colors=front,
        back_colors=back,
        front_is_on=power > 0 and any(c.brightness > 0 for c in front),
        back_is_on=power > 0 and any(c.brightness > 0 for c in back),
    )
    return SimpleNamespace(
        data=state,
        device=SimpleNamespace(
            turn_front_on=AsyncMock(),
            turn_front_off=AsyncMock(),
            turn_back_on=AsyncMock(),
            turn_back_off=AsyncMock(),
            apply_front_theme=AsyncMock(),
            apply_back_theme=AsyncMock(),
        ),
        async_request_refresh=AsyncMock(),
        async_refresh=AsyncMock(),
        async_add_listener=lambda *_args, **_kwargs: lambda: None,
    )


def test_identity_matches_previous_releases() -> None:
    """Unique IDs and device identity must not change, or entities are orphaned."""
    coordinator = _coordinator(front=[WARM], back=[GREEN])

    front = LIFXMirrorLight(coordinator, COMPONENT_FRONT)
    back = LIFXMirrorLight(coordinator, COMPONENT_BACK)
    front.hass = back.hass = SimpleNamespace()

    assert front.unique_id == "d0:73:d5:00:00:01_frontlight"
    assert back.unique_id == "d0:73:d5:00:00:01_backlight"
    assert front.name == "Frontlight"
    assert back.name == "Backlight"
    assert front.device_info["identifiers"] == {("lifx_mirror", "d0:73:d5:00:00:01")}
    assert front.device_info["connections"] == {("mac", "d0:73:d5:00:00:01")}
    assert front.device_info["sw_version"] == "4.145"
    assert front.min_color_temp_kelvin == 1500
    assert front.max_color_temp_kelvin == 9000


def test_each_entity_reads_its_own_component() -> None:
    """Front and back report their own colors and on state."""
    coordinator = _coordinator(front=[WARM, DARK], back=[GREEN])

    front = LIFXMirrorLight(coordinator, COMPONENT_FRONT)
    back = LIFXMirrorLight(coordinator, COMPONENT_BACK)

    assert front.is_on is True
    assert front.brightness == 128
    assert front.color_mode is ColorMode.COLOR_TEMP
    assert front.color_temp_kelvin == 2700
    assert back.is_on is True
    assert back.brightness == 255
    assert back.color_mode is ColorMode.HS
    assert back.hs_color == (120, 100)


def test_dark_component_is_off_even_though_the_mirror_is_powered() -> None:
    """A component with every zone dark is off while the other stays lit."""
    coordinator = _coordinator(front=[DARK], back=[GREEN])

    assert LIFXMirrorLight(coordinator, COMPONENT_FRONT).is_on is False
    assert LIFXMirrorLight(coordinator, COMPONENT_BACK).is_on is True


def test_powered_off_mirror_is_off_for_both_components() -> None:
    """Zones keep their brightness while powered off, but the lights are off."""
    coordinator = _coordinator(front=[WARM], back=[GREEN], power=0)

    assert LIFXMirrorLight(coordinator, COMPONENT_FRONT).is_on is False
    assert LIFXMirrorLight(coordinator, COMPONENT_BACK).is_on is False


@pytest.mark.asyncio
async def test_turn_on_without_options_restores_the_previous_colors(
    fake_hass: FakeHass,
) -> None:
    """Passing no color lets the library bring back what the light last showed."""
    coordinator = _coordinator(front=[WARM], back=[GREEN])
    entity = LIFXMirrorLight(coordinator, COMPONENT_FRONT)

    await entity.async_turn_on()
    await fake_hass.drain()

    coordinator.device.turn_front_on.assert_awaited_once_with(None, duration=0.0)
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_turn_on_merges_the_request_onto_the_current_color() -> None:
    """Brightness and transition reach the library as a color and a duration."""
    coordinator = _coordinator(front=[WARM], back=[GREEN])
    entity = LIFXMirrorLight(coordinator, COMPONENT_BACK)

    await entity.async_turn_on(**{ATTR_BRIGHTNESS: 51, ATTR_TRANSITION: 2})

    coordinator.device.turn_back_on.assert_awaited_once_with(
        [HSBK(hue=120, saturation=1, brightness=0.2, kelvin=3500)], duration=2.0
    )
    coordinator.device.turn_front_on.assert_not_awaited()


@pytest.mark.asyncio
async def test_turn_on_color_temp_desaturates() -> None:
    """A color temperature request turns a colored light white."""
    coordinator = _coordinator(front=[WARM], back=[GREEN])
    entity = LIFXMirrorLight(coordinator, COMPONENT_BACK)

    await entity.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4000})

    coordinator.device.turn_back_on.assert_awaited_once_with(
        [HSBK(hue=120, saturation=0, brightness=1, kelvin=4000)], duration=0.0
    )


@pytest.mark.asyncio
async def test_dimming_keeps_the_zones_relative_brightness() -> None:
    """A gradient dims as a whole instead of flattening to one colour."""
    ramp = [GREEN.replace(brightness=1.0), GREEN.replace(brightness=0.5), DARK]
    coordinator = _coordinator(front=ramp, back=[GREEN])
    entity = LIFXMirrorLight(coordinator, COMPONENT_FRONT)

    await entity.async_turn_on(**{ATTR_BRIGHTNESS: 128})

    sent = coordinator.device.turn_front_on.await_args.args[0]
    assert [round(c.brightness, 2) for c in sent] == [0.5, 0.25, 0.0]
    assert all(c.hue == 120 for c in sent[:2])


@pytest.mark.asyncio
async def test_a_new_colour_keeps_each_zones_brightness() -> None:
    """Changing hue recolours every zone but leaves the brightness profile."""
    ramp = [GREEN.replace(brightness=1.0), GREEN.replace(brightness=0.4)]
    coordinator = _coordinator(front=ramp, back=[GREEN])
    entity = LIFXMirrorLight(coordinator, COMPONENT_FRONT)

    await entity.async_turn_on(**{ATTR_HS_COLOR: (240, 50)})

    sent = coordinator.device.turn_front_on.await_args.args[0]
    assert [c.brightness for c in sent] == [1.0, 0.4]
    assert all((c.hue, c.saturation) == (240, 0.5) for c in sent)


def test_zones_attribute_is_not_recorded() -> None:
    """Fifty colours per state change would flood the recorder."""
    coordinator = _coordinator(front=[WARM], back=[GREEN])
    entity = LIFXMirrorLight(coordinator, COMPONENT_BACK)

    assert "zones" in entity._unrecorded_attributes
    assert entity.extra_state_attributes["zones"] == [
        {"hue": 120.0, "saturation": 100.0, "brightness": 100.0, "kelvin": 3500}
    ]


@pytest.mark.asyncio
async def test_turn_off_targets_only_this_component() -> None:
    """Turning one light off leaves the other to the library's off logic."""
    coordinator = _coordinator(front=[WARM], back=[GREEN])
    entity = LIFXMirrorLight(coordinator, COMPONENT_FRONT)

    await entity.async_turn_off(**{ATTR_TRANSITION: 3})

    coordinator.device.turn_front_off.assert_awaited_once_with(None, duration=3.0)
    coordinator.device.turn_back_off.assert_not_awaited()


@pytest.mark.asyncio
async def test_device_failure_becomes_a_home_assistant_error(
    fake_hass: FakeHass,
) -> None:
    """A Mirror that does not answer is reported rather than raised raw."""
    coordinator = _coordinator(front=[WARM], back=[GREEN])
    coordinator.device.turn_front_off.side_effect = LifxError("no reply")
    entity = LIFXMirrorLight(coordinator, COMPONENT_FRONT)

    with pytest.raises(HomeAssistantError, match="did not accept"):
        await entity.async_turn_off()

    assert fake_hass.tasks == []
    coordinator.async_request_refresh.assert_not_awaited()


class _ThreadOnlyState(SimpleNamespace):
    """State of a Mirror on Thread, where WiFi data does not exist."""

    @property
    def wifi_info(self) -> None:
        msg = "A Thread Mirror has no WiFi information"
        raise AssertionError(msg)


@pytest.mark.asyncio
async def test_a_mirror_on_thread_reads_and_commands_without_wifi_data() -> None:
    """Nothing the lights read or send depends on the Mirror being on WiFi."""
    coordinator = _coordinator(front=[WARM], back=[GREEN])
    coordinator.data = _ThreadOnlyState(**vars(coordinator.data))
    coordinator.device.connectivity = "thread"
    front = LIFXMirrorLight(coordinator, COMPONENT_FRONT)
    back = LIFXMirrorLight(coordinator, COMPONENT_BACK)

    assert front.is_on is True
    assert back.hs_color == (120, 100)
    assert front.device_info["model"] == "LIFX Mirror"

    await front.async_turn_on(**{ATTR_BRIGHTNESS: 255})
    await back.async_turn_off()

    coordinator.device.turn_front_on.assert_awaited_once()
    coordinator.device.turn_back_off.assert_awaited_once()


@pytest.mark.asyncio
async def test_state_is_read_back_only_after_the_mirror_settles(
    monkeypatch: pytest.MonkeyPatch, fake_hass: FakeHass
) -> None:
    """Reading straight after a command returns the old state and makes lights flap."""
    monkeypatch.setattr(util_module, "STATE_SETTLE_DELAY", 0.3)
    coordinator = _coordinator(front=[WARM], back=[GREEN])
    refreshed_at: list[float] = []

    async def _record_refresh() -> None:
        refreshed_at.append(time.monotonic())

    coordinator.async_request_refresh = _record_refresh
    entity = LIFXMirrorLight(coordinator, COMPONENT_FRONT)

    started = time.monotonic()
    await entity.async_turn_on()
    await fake_hass.drain()

    assert refreshed_at[0] - started >= 0.25


@pytest.mark.asyncio
async def test_a_transition_is_read_again_when_it_ends(
    fast_settle: MagicMock, fake_hass: FakeHass
) -> None:
    """The state at the start of a fade is not where it finishes."""
    coordinator = _coordinator(front=[WARM], back=[GREEN])
    entity = LIFXMirrorLight(coordinator, COMPONENT_FRONT)

    await entity.async_turn_on(**{ATTR_TRANSITION: 5})
    await fake_hass.drain()

    fast_settle.assert_called_once()
    hass, delay, action = fast_settle.call_args.args
    assert hass is entity.hass
    assert delay == 5.0
    await action(None)
    coordinator.async_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_command_without_transition_needs_no_second_read(
    fast_settle: MagicMock, fake_hass: FakeHass
) -> None:
    """Only a fade needs a read when it ends."""
    coordinator = _coordinator(front=[WARM], back=[GREEN])
    entity = LIFXMirrorLight(coordinator, COMPONENT_FRONT)

    await entity.async_turn_on()
    await fake_hass.drain()

    fast_settle.assert_not_called()


@pytest.mark.asyncio
async def test_a_command_does_not_wait_for_the_read_back(
    fake_hass: FakeHass,
) -> None:
    """A caller cancelled mid-read would mark the whole connection failed."""
    coordinator = _coordinator(front=[WARM], back=[GREEN])
    release = asyncio.Event()

    async def _slow_refresh() -> None:
        await release.wait()

    coordinator.async_request_refresh = _slow_refresh
    entity = LIFXMirrorLight(coordinator, COMPONENT_FRONT)

    await asyncio.wait_for(entity.async_turn_on(), timeout=1)

    assert not release.is_set()
    assert len(fake_hass.tasks) == 1
    assert not fake_hass.tasks[0].done()
    release.set()
    await fake_hass.drain()


@pytest.mark.asyncio
async def test_the_read_back_runs_in_its_own_task(fake_hass: FakeHass) -> None:
    """Cancelling the caller, as a restarted automation does, cannot reach it."""
    coordinator = _coordinator(front=[WARM], back=[GREEN])
    ran_in: list[asyncio.Task | None] = []

    async def _record_task() -> None:
        ran_in.append(asyncio.current_task())

    coordinator.async_request_refresh = _record_task
    entity = LIFXMirrorLight(coordinator, COMPONENT_FRONT)
    caller = asyncio.current_task()

    await entity.async_turn_on()
    await fake_hass.drain()

    assert ran_in
    assert ran_in[0] is not caller


def test_themes_are_offered_as_effects() -> None:
    """Each light lists the current LIFX themes by name, without retired ones."""
    entity = LIFXMirrorLight(_coordinator(front=[WARM], back=[GREEN]), COMPONENT_FRONT)

    assert entity.supported_features & LightEntityFeature.EFFECT
    assert "Evening" in entity.effect_list
    assert "Warm Ember" in entity.effect_list
    # "Fire" was replaced by "Warm Ember"; "Aurora" must not appear twice
    assert "Fire" not in entity.effect_list
    assert entity.effect_list.count("Aurora") == 1
    assert entity.effect_list == sorted(entity.effect_list, key=str.casefold)
    assert entity.effect is None


@pytest.mark.asyncio
async def test_choosing_an_effect_paints_the_theme_on_this_light(
    fake_hass: FakeHass,
) -> None:
    """A theme lands on this component only, with the requested transition."""
    coordinator = _coordinator(front=[WARM], back=[GREEN])
    entity = LIFXMirrorLight(coordinator, COMPONENT_BACK)

    await entity.async_turn_on(**{ATTR_EFFECT: "Evening", ATTR_TRANSITION: 2})
    await fake_hass.drain()

    apply = coordinator.device.apply_back_theme
    apply.assert_awaited_once()
    assert apply.await_args.args[0].palette_equals(ThemeLibrary.get("evening"))
    assert apply.await_args.kwargs == {"duration": 2.0, "power_on": True}
    coordinator.device.apply_front_theme.assert_not_awaited()
    coordinator.device.turn_back_on.assert_not_awaited()
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_effect_with_brightness_is_scaled_to_it(fake_hass: FakeHass) -> None:
    """A theme comes up at the requested brightness, not at its own."""
    coordinator = _coordinator(front=[WARM], back=[GREEN])
    coordinator.device.layout = SimpleNamespace(width=4, height=13)
    coordinator.device.back_positions = tuple(range(0, 50, 2))
    entity = LIFXMirrorLight(coordinator, COMPONENT_BACK)

    await entity.async_turn_on(**{ATTR_EFFECT: "Evening", ATTR_BRIGHTNESS: 51})
    await fake_hass.drain()

    sent = coordinator.device.turn_back_on.await_args.args[0]
    assert len(sent) == 25
    assert max(c.brightness for c in sent) == pytest.approx(0.2)
    coordinator.device.apply_back_theme.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unknown_effect_is_refused() -> None:
    """An effect outside the list is an error and nothing is sent."""
    coordinator = _coordinator(front=[WARM], back=[GREEN])
    entity = LIFXMirrorLight(coordinator, COMPONENT_FRONT)

    with pytest.raises(ServiceValidationError, match="Unknown effect"):
        await entity.async_turn_on(**{ATTR_EFFECT: "colorloop"})

    coordinator.device.apply_front_theme.assert_not_awaited()
    coordinator.device.turn_front_on.assert_not_awaited()
