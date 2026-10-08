"""Tests for the LIFX Mirror helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
)
from homeassistant.exceptions import ServiceValidationError

pytest.importorskip("lifx")

from lifx import HSBK, MirrorLight

from custom_components.lifx_mirror.util import (
    find_mirror_coordinators,
    hsbk_changes,
    merge_hsbk,
    normalize_serial,
)


def _mirror() -> MirrorLight:
    """Return an object that is a MirrorLight without connecting to a device."""
    return object.__new__(MirrorLight)


def _hass(*entries: SimpleNamespace) -> SimpleNamespace:
    """Return a stand-in hass whose loaded LIFX entries are the given ones."""
    return SimpleNamespace(
        config_entries=SimpleNamespace(async_loaded_entries=lambda _domain: entries)
    )


def test_normalize_serial() -> None:
    """Serials and MACs compare equal whatever their separators and case."""
    assert normalize_serial("D0:73:D5:00:00:01") == "d073d5000001"
    assert normalize_serial("d073d5000001") == "d073d5000001"


def test_find_mirror_coordinators_keeps_only_mirrors() -> None:
    """Other LIFX devices and entries that did not finish loading are skipped."""
    mirror = SimpleNamespace(device=_mirror())
    other = SimpleNamespace(device=SimpleNamespace(product=176))
    not_ready = SimpleNamespace()

    found = find_mirror_coordinators(
        _hass(
            SimpleNamespace(runtime_data=mirror),
            SimpleNamespace(runtime_data=other),
            SimpleNamespace(runtime_data=None),
            not_ready,
        )
    )

    assert found == [mirror]


def test_hsbk_changes_empty() -> None:
    """A plain turn_on asks for no color change."""
    assert hsbk_changes() == {
        "hue": None,
        "saturation": None,
        "brightness": None,
        "kelvin": None,
    }


def test_hsbk_changes_hs_color_uses_neutral_kelvin() -> None:
    """Hue and saturation arrive as degrees and percent."""
    changes = hsbk_changes(**{ATTR_HS_COLOR: (120.0, 50.0)})

    assert changes == {
        "hue": 120.0,
        "saturation": 0.5,
        "brightness": None,
        "kelvin": 3500,
    }


def test_hsbk_changes_color_temp_desaturates() -> None:
    """A color temperature request drops any saturation."""
    changes = hsbk_changes(**{ATTR_COLOR_TEMP_KELVIN: 2700})

    assert changes["kelvin"] == 2700
    assert changes["saturation"] == 0.0


def test_hsbk_changes_brightness_scales_to_unit_range() -> None:
    """Brightness arrives as 0-255 and leaves as a fraction."""
    assert hsbk_changes(**{ATTR_BRIGHTNESS: 255})["brightness"] == 1.0
    assert hsbk_changes(**{ATTR_BRIGHTNESS: 0})["brightness"] == 0.0


def test_merge_hsbk_keeps_what_is_not_asked() -> None:
    """Only the requested components of the base color change."""
    base = HSBK(hue=10, saturation=0.4, brightness=0.6, kelvin=3000)

    merged = merge_hsbk(base, hsbk_changes(**{ATTR_BRIGHTNESS: 51}))

    assert merged == HSBK(hue=10, saturation=0.4, brightness=0.2, kelvin=3000)


def test_merge_hsbk_lights_a_dark_component() -> None:
    """A color asked of a dark component comes on at full brightness."""
    dark = HSBK(hue=0, saturation=0, brightness=0, kelvin=3500)

    merged = merge_hsbk(dark, hsbk_changes(**{ATTR_COLOR_TEMP_KELVIN: 2700}))

    assert merged.brightness == 1.0
    assert merged.kelvin == 2700


def test_merge_hsbk_rejects_unsupported_kelvin() -> None:
    """A value the device cannot take is reported to the caller."""
    base = HSBK(hue=0, saturation=0, brightness=1, kelvin=3500)

    with pytest.raises(ServiceValidationError):
        merge_hsbk(base, hsbk_changes(**{ATTR_COLOR_TEMP_KELVIN: 100}))
