# Sprinkler Rain Bypass

Raspberry Pi controller that checks recent rainfall (Visual Crossing) and drives a relay plus status LEDs for your irrigation rain-bypass input.

Get a free API key at [Visual Crossing](https://www.visualcrossing.com/weather-api).

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


| Section    | Keys                                                              |
| ---------- | ----------------------------------------------------------------- |
| `location` | `latitude`, `longitude`, `timezone` (IANA name, e.g. `America/Chicago`) |
| `watering` | `inches_required`, `past_days`, `updates_per_day`                 |
| `season`   | `start_month/day`, `end_month/day`                                |
| `weather`  | `api_key` (Visual Crossing Timeline API)                          |
| `gpio`     | `relay`, `watering_enabled_led`, `watering_disabled_led`, `mock`  |
| `runtime`  | `state_path`, `fail_mode`, `log_level`, `weather_timeout_seconds` |


`fail_mode`: `disable_watering` (default) or `keep_last_state` when the weather API fails.

### Weather behavior

The app calls the [Visual Crossing Timeline API](https://www.visualcrossing.com/resources/documentation/weather-api/timeline-weather-api/) with an inclusive local-date range from `past_days` ago through today (computed from `location.timezone`). Daily `precip` is summed in **inches** (`unitGroup=us`).

- **Today** may blend observed rain with forecast rain for the rest of the day (`source: comb` in the API). That can block watering before a storm arrives — usually what you want for a rain bypass.
- **API cost** is about one record per day returned (~`past_days` per check). Set `log_level = "DEBUG"` to log `queryCost` each request.
- Match `location.timezone` to your coordinates; a mismatch with the API’s resolved timezone is logged as a warning.

## Code

Three modules: `config` (settings), `app` (weather, logic, CLI), `gpio`. 100% test coverage enforced in CI.

## Hardware

### Parts

- Raspberry Pi with network access
- GPIO breakout or breadboard
- 2× resistors (50–300 Ω), 1× 3.3 V relay module
- Green + red LED
- Irrigation controller with rain-bypass input

### Wiring (default BCM pins)


| Signal    | Pin | Behavior                 |
| --------- | --- | ------------------------ |
| Relay     | 25  | HIGH = watering disabled |
| Green LED | 4   | ON when watering allowed |
| Red LED   | 27  | ON when watering blocked |


Configure pins in `settings.toml` under `[gpio]`.

### Photos

Front view

Side view

### Safety

Verify relay behavior with your controller before unattended use. Match relay rating to your bypass circuit.

## systemd

```bash
sudo cp deploy/rain-bypass.service /etc/systemd/system/
sudo systemctl enable --now rain-bypass
```

## License

MIT