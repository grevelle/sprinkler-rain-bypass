# Sprinkler Rain Bypass

Raspberry Pi controller that checks recent rainfall (Open-Meteo) and drives a relay plus status LEDs for your irrigation rain-bypass input.

## Setup

```bash
git clone https://github.com/grevelle/sprinkler-rain-bypass.git
cd sprinkler-rain-bypass
pip install -e ".[gpio]"
cp settings.example.toml settings.toml   # edit coordinates, pins, thresholds
```

## Run

```bash
python -m rain_bypass --once   # single check
python -m rain_bypass          # loop
```

Use `gpio.mock = true` in settings to develop off the Pi.

## Config (`settings.toml`)

| Section | Keys |
|---------|------|
| `location` | `latitude`, `longitude`, `timezone` |
| `watering` | `inches_required`, `past_days`, `updates_per_day` |
| `season` | `start_month/day`, `end_month/day` |
| `gpio` | `relay`, `watering_enabled_led`, `watering_disabled_led`, `mock` |
| `runtime` | `state_path`, `fail_mode`, `log_level`, `weather_timeout_seconds` |

`fail_mode`: `disable_watering` (default) or `keep_last_state` when the weather API fails.

## Code

Four modules: `config` (settings), `app` (weather + logic), `gpio`, `__main__` (CLI). 100% test coverage enforced in CI.

## Hardware

[docs/hardware.md](docs/hardware.md)

## systemd

```bash
sudo cp deploy/rain-bypass.service /etc/systemd/system/
sudo systemctl enable --now rain-bypass
```

## License

MIT
