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
- Choosing a hue, saturation or color temperature recolors every zone; choosing a brightness
  rescales the zones so the brightest takes it and the rest keep their share, so a gradient
  dims without flattening.
- Each light's effect list holds the LIFX themes (Evening, Autumn, Galaxy and so on).
  Choosing one paints that light with the theme's palette and leaves the other light alone.
  A theme is a one-off set of colors, not a running animation, so the light reports no
  current effect afterwards. A brightness sent with an effect scales the theme so its
  brightest zone takes that brightness; other options, such as a color, are ignored. The
  core LIFX light's effects (colorloop, pulse and the rest) are not offered here.
- Each light has a `zones` attribute listing its 25 zone colors (not stored in the recorder).
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

## Action: `lifx_mirror.set_zones`

Sets the individual zones of one light. Give exactly one of `colors`, `gradient`, `segments`
or `theme`.

```yaml
action: lifx_mirror.set_zones
data:
  device_id: 0123456789abcdef0123456789abcdef
  component: front
  gradient:
    - {hue: 30, saturation: 100, brightness: 10}
    - {hue: 0, saturation: 0, brightness: 60, kelvin: 3000}
  transition: 2
```

- `colors`: exactly 25 entries, in zone order (each light is a closed ring around the
  Mirror's edge).
- `gradient`: two or more colors spread evenly along the Mirror's length. Hues blend the
  short way round. On a Mirror mounted horizontally with its buttons on the right, the first
  color is at the left end and the last at the right.
- `segments`: stretches of the length, each with a `start` and `end` in percent (0 is the end
  opposite the buttons, 100 the end with them) and a color. Zones outside every segment go
  dark, and later segments win where they overlap.

  ```yaml
  segments:
    - {start: 0, end: 33, hue: 30, saturation: 100, brightness: 20}
    - {start: 67, end: 100, kelvin: 2700, brightness: 60}
  ```
- `theme`: a LIFX theme, by the name the light's effect list shows (`Warm Ember`) or its
  key (`warm_ember`). Case doesn't matter.

Each color takes `hue` (0–360), `saturation` (%), `brightness` (%) and `kelvin`, with the
same defaults as `set_state`.

## Actions: `lifx_mirror.snapshot` and `lifx_mirror.restore`

`snapshot` remembers every zone of both lights and whether each is on. `restore` puts them
back, gradients included, which a scene can't do. Use them around a temporary change:

```yaml
sequence:
  - action: lifx_mirror.snapshot
    data:
      device_id: 0123456789abcdef0123456789abcdef
  - action: lifx_mirror.set_state
    data:
      device_id: 0123456789abcdef0123456789abcdef
      frontlight_hue: 0
      frontlight_saturation: 100
      backlight_hue: 0
      backlight_saturation: 100
  - delay: 3
  - action: lifx_mirror.restore
    data:
      device_id: 0123456789abcdef0123456789abcdef
      transition: 1
```

- A light that was off goes back off, and still comes back in its earlier colors the next
  time it's turned on. If both were off, the Mirror is powered off.
- Each Mirror keeps one snapshot, replaced by the next `snapshot` call. `restore` can be
  called more than once.
- Snapshots live in memory and are lost when Home Assistant restarts.

## WiFi and Thread

The integration reads and sets only colors and power, nothing specific to the radio, so it does
not care whether the Mirror is on WiFi or Thread. Finding and reaching the Mirror is core LIFX's
job. If core reloads the Mirror, for example because its address changed, these lights
reconnect within about five minutes.

## License

[MIT](LICENSE).

## Acknowledgements

- Started from [hass-lifx-ceiling](https://github.com/Djelibeybi/hass-lifx-ceiling) by
  Avi Miller (MIT), which provided the project layout and tooling configuration.
- The handling of color requests follows the approach of Home Assistant's core
  [LIFX integration](https://github.com/home-assistant/core/tree/dev/homeassistant/components/lifx)
  (Apache-2.0).
- The Mirror itself is driven by [lifx-async](https://github.com/Djelibeybi/lifx-async)
  (UPL-1.0), which Home Assistant installs for its LIFX integration.

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The tests use plain fakes and do not need a Mirror or a running Home Assistant. GitHub
Actions runs the same checks on every pull request and on each push to `main`.
