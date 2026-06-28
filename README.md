# Sprinkler Rain Bypass

Use a Raspberry Pi to replace the rain-bypass sensor on your irrigation controller. The Pi checks recent precipitation, decides whether watering is needed, and drives a relay plus status LEDs.

Version 2 is a full rewrite of the original single-script project. It adds a modular Python package, clearer configuration, pluggable weather providers, tests, and safer failure handling.

## What's new in v2

- **Modular package** (`rain_bypass`) instead of one monolithic script
- **TOML configuration** with `settings.example.toml` and separate runtime state file
- **Open-Meteo support** (free, no API key) as the default weather provider
- **Visual Crossing** still supported for users who prefer it
- **Fixed lat/lon** — Google Geolocation removed (better for a stationary controller)
- **Mock GPIO mode** for development on a PC
- **Fail-safe options** when weather APIs are unavailable
- **pytest suite** with HTTP mocking
- **systemd unit** under `deploy/`

## How it works

1. Load `settings.toml`
2. Check whether today's date falls within the configured watering season
3. Fetch precipitation for the past *N* days at your configured coordinates
4. If rainfall is at or below the threshold, enable watering (relay off, green LED on)
5. Otherwise disable watering (relay on, red LED on)
6. Persist runtime state to `state.json` and sleep until the next check

## Quick start (Raspberry Pi)

```bash
git clone https://github.com/grevelle/sprinkler-rain-bypass.git
cd sprinkler-rain-bypass
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[gpio]"

cp settings.example.toml settings.toml
# Edit settings.toml: latitude, longitude, timezone, thresholds, GPIO pins

# Dry run once (uses mock GPIO if gpio.mock = true)
python -m rain_bypass --once

# Production run (gpio.mock = false on the Pi)
python -m rain_bypass
```

## Configuration

Copy `settings.example.toml` to `settings.toml` and edit:

| Section | Purpose |
|---------|---------|
| `[location]` | Fixed coordinates and timezone |
| `[watering]` | Rain threshold, lookback window, check frequency |
| `[season]` | Inclusive date range when watering may be allowed |
| `[weather]` | Provider selection and API settings |
| `[gpio]` | BCM pin numbers and mock mode |
| `[runtime]` | State file path, fail mode, log level |

### Weather providers

**Open-Meteo (default)** — no API key required.

```toml
[weather]
provider = "open_meteo"
```

**Visual Crossing** — requires an API key from [Visual Crossing](https://www.visualcrossing.com/weather-api).

```toml
[weather]
provider = "visual_crossing"
visual_crossing_api_key = "your-key-here"
```

### Fail modes

When the weather provider is unreachable:

- `disable_watering` (default) — conservative; blocks watering
- `keep_last_state` — reuses the previous decision from `state.json`

## Development (Windows/macOS/Linux)

```bash
pip install -e ".[dev]"
cp settings.example.toml settings.toml
# Set gpio.mock = true in settings.toml

pytest
python -m rain_bypass --once
```

## systemd (auto-start on boot)

```bash
sudo mkdir -p /opt/sprinkler-rain-bypass
sudo cp -r . /opt/sprinkler-rain-bypass/
sudo cp settings.toml /opt/sprinkler-rain-bypass/
sudo cp deploy/rain-bypass.service /etc/systemd/system/rain-bypass.service
sudo systemctl daemon-reload
sudo systemctl enable rain-bypass.service
sudo systemctl start rain-bypass.service
systemctl status rain-bypass.service
```

Adjust paths in the unit file if you install somewhere other than `/opt/sprinkler-rain-bypass`.

## Migrating from v1

The original project used `settings.ini`, Google Geolocation, and a single `rain-bypass.py` script.

1. Run the migration helper:

   ```bash
   python scripts/migrate_ini.py settings.ini -o settings.toml
   ```

2. Set `latitude` and `longitude` manually (replacing Google Geolocation)
3. Choose a weather provider (`open_meteo` recommended)
4. Install the package and use `python -m rain_bypass` instead of `rain-bypass.py`

### v1 → v2 setting map

| v1 (`settings.ini`) | v2 (`settings.toml`) |
|---------------------|----------------------|
| `weatherlookback` + `raindays` | `watering.past_days` (simplified to past N days ending today) |
| `inchesrequired` | `watering.inches_required` |
| `weatherupdatesperday` | `watering.updates_per_day` |
| `firstmonthtowater` / `firstdaytowater` | `season.start_month` / `season.start_day` |
| `lastmonthtowater` / `lastdaytowater` | `season.end_month` / `season.end_day` |
| `visualcrossingkey` | `weather.visual_crossing_api_key` |
| `googlekey` | removed — use fixed coordinates |
| `[ProgramModified]` | `state.json` (separate runtime file) |

## Hardware

See [docs/hardware.md](docs/hardware.md) for wiring, parts, and photos.

## Project layout

```
src/rain_bypass/     Application package
tests/               pytest suite
deploy/              systemd unit
docs/                Hardware notes
scripts/             Migration utilities
settings.example.toml
```

## License

MIT — see [LICENSE](LICENSE).

## Credits

Based on work by Scott Mangold ([Third Eye Vision](http://www.thirdeyevis.com/pi-page-3.php)). v2 modernization by Greg Revelle.
