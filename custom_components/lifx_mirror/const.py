"""Constants for LIFX Mirror."""

from datetime import timedelta
from logging import Logger, getLogger

_LOGGER: Logger = getLogger(__package__)

DOMAIN = "lifx_mirror"
NAME = "LIFX Mirror"
LIFX_DOMAIN = "lifx"

ATTR_FRONTLIGHT_HUE = "frontlight_hue"
ATTR_FRONTLIGHT_SATURATION = "frontlight_saturation"
ATTR_FRONTLIGHT_BRIGHTNESS = "frontlight_brightness"
ATTR_FRONTLIGHT_KELVIN = "frontlight_kelvin"

ATTR_BACKLIGHT_HUE = "backlight_hue"
ATTR_BACKLIGHT_SATURATION = "backlight_saturation"
ATTR_BACKLIGHT_BRIGHTNESS = "backlight_brightness"
ATTR_BACKLIGHT_KELVIN = "backlight_kelvin"

COMPONENT_FRONT = "front"
COMPONENT_BACK = "back"

DEFAULT_KELVIN = 3500

# Seconds a Mirror needs to apply a command before a read-back shows the result
STATE_SETTLE_DELAY = 0.3

DISCOVERY_INTERVAL = timedelta(minutes=5)

SERVICE_LIFX_MIRROR_SET_STATE = "set_state"
