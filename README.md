# LIFX Mirror

A Home Assistant custom integration that gives a [LIFX Mirror](https://www.lifx.com/products/mirror)
two independent light entities, one for the **front** (surface) light and one for the **back**
(halo) light.

The core LIFX integration shows the Mirror as a single light. This integration adds the two
components on top of it, so each can be switched, dimmed and colored on its own.

## Requirements

- Home Assistant **2026.10.0 or later**. That release moved core LIFX to the `lifx-async`
  library, which is what understands the Mirror's two components. Older releases need an
  older version of this integration.
- Your Mirror already set up in the core [LIFX](https://www.home-assistant.io/integrations/lifx)
  integration.

## Installation

### HACS

1. In HACS, open the menu and choose **Custom repositories**.
2. Add this repository's URL with the type **Integration**.
3. Download **LIFX Mirror** and restart Home Assistant.

### Manual

Copy `custom_components/lifx_mirror` into your Home Assistant `config/custom_components`
folder and restart Home Assistant.

### Setup

Go to **Settings → Devices & services → Add integration**, choose **LIFX Mirror** and confirm.
The integration finds Mirrors from the core LIFX integration, so there is nothing to enter.

## Entities

Each Mirror gets two lights:

| Entity | Controls |
|---|---|
| `Frontlight` | The 25 zones facing the room |
| `Backlight` | The 25 zones facing the wall |

Both support brightness, color temperature (1500–9000 K), hue/saturation and transitions.

- Turning a light on with no options brings back the colors it last showed.
- Choosing a color or brightness sets every zone of that light to one color.
- Turning one light off while the other is on only darkens that light. Turning off the last
  lit light powers the Mirror off.

## Action: `lifx_mirror.set_state`

Sets both lights in one call. Brightness `0` turns a light off, and if both are `0` the Mirror
is powered off.

```yaml
action: lifx_mirror.set_state
data:
  device_id: 0123456789abcdef0123456789abcdef
  frontlight_brightness: 0
  backlight_hue: 120
  backlight_saturation: 100
  backlight_brightness: 20
  transition: 2
```

| Field | Unit | Default |
|---|---|---|
| `frontlight_hue`, `backlight_hue` | degrees, 0–360 | 0 |
| `frontlight_saturation`, `backlight_saturation` | percent | 0 |
| `frontlight_brightness`, `backlight_brightness` | percent | 100 |
| `frontlight_kelvin`, `backlight_kelvin` | kelvin, 1500–9000 | 3500 |
| `transition` | seconds | 0 |

`device_id` accepts a list, and may be either this integration's device or the Mirror's
core LIFX device.

## WiFi and Thread

The integration reads and sets only colors and power, nothing specific to the radio, so it does
not care whether the Mirror is on WiFi or Thread. Finding and reaching the Mirror is core LIFX's
job. If core reloads the Mirror, for example because its address changed, these lights
reconnect within about five minutes.

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The tests use plain fakes and do not need a Mirror or a running Home Assistant.
