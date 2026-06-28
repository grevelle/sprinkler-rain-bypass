# Sprinkler Rain Bypass

Raspberry Pi controller that checks recent rainfall (Visual Crossing) and drives a relay plus status LEDs for your irrigation rain-bypass input.

Get a free API key at [Visual Crossing](https://www.visualcrossing.com/weather-api).

## Setup

On a Raspberry Pi (or Linux), run the interactive installer — it creates a venv, prompts for all settings, validates your API key, and optionally installs systemd:

```bash
git clone https://github.com/grevelle/sprinkler-rain-bypass.git
cd sprinkler-rain-bypass
chmod +x install.sh
./install.sh
```

Manual setup (or development off the Pi):

```bash
pip install -e ".[dev]"    # use [gpio] on the Pi
cp settings.example.toml settings.toml   # edit coordinates, pins, API key
```

## Run

```bash
python -m rain_bypass --once   # single check
python -m rain_bypass          # loop
```

Use `gpio.mock = true` in settings to develop off the Pi.

## Config (`settings.toml`)


| Section    | Keys                                                                    |
| ---------- | ----------------------------------------------------------------------- |
| `location` | `latitude`, `longitude`, `timezone` (IANA name, e.g. `America/Chicago`) |
| `watering` | `inches_required`, `past_days`, `forecast_days`, `forecast_inches_max`, `event_inches`, `rain_delay_days`, `near_term_hours`, `near_term_inches_max`, `freeze_skip`, `freeze_temp_f`, `check_hour`, `check_minute`, `updates_per_day` |
| `season`   | `start_month/day`, `end_month/day` (must not overlap `[sewer]`)         |
| `sewer`    | `protect`, `start_month/day`, `end_month/day` — hard block window       |
| `weather`  | `api_key` (Visual Crossing Timeline API)                                |
| `gpio`     | `relay`, `watering_enabled_led`, `watering_disabled_led`, `mock`        |
| `runtime`  | `state_path`, `fail_mode`, `log_level`, `weather_timeout_seconds`       |


`fail_mode`: `disable_watering` (default) or `keep_last_state` when the weather API fails.

**Suggested starting values** (used by `./install.sh`): **Hartland, WI 53029** — irrigation season **May 7–Oct 7** only; **sewer baseline lockout Jan 16–Mar 15** (city sets annual sewer cap from winter water use on the April bill).

### Sewer baseline protection

Many municipalities (including Hartland/Waukesha) set **annual sewer charges from water used January 16 through March 15** (shown on April utility bills). Any irrigation on the same water meter during that window increases your sewer bill for the **entire year**.

When `sewer.protect = true` (default, always on via `./install.sh`):

- Watering is **hard blocked** during the sewer baseline window — before weather checks, season checks, or fail-mode logic.
- The relay stays in **block** mode; no API call is made.
- Config validation **rejects** a `[season]` range that overlaps the sewer window.

Keep `[season]` entirely outside Jan 16–Mar 15. Defaults (May 7–Oct 7) satisfy this.

### Weather behavior

The app uses **six gates** before allowing watering:

1. **Past window** — cumulative rain through today vs `inches_required` (Hydrawise).
2. **Rain event** — any single day in that window ≥ `event_inches` (UF/IFAS sensor; `0` = off).
3. **Forecast window** — sum from tomorrow through `forecast_days` vs `forecast_inches_max` (Rain Bird).
4. **Rain delay** — after a past-window block, stay off `rain_delay_days` (`blocked_until` in `state.json`).
5. **Near term** — hourly precip over the next `near_term_hours` vs `near_term_inches_max` (Rachio-style; `0` = off).
6. **Freeze skip** — block when today or tomorrow forecast low is below `freeze_temp_f` (protect heads/pipes).

Watering is allowed only when **all** gates pass and no active rain delay remains. One [Visual Crossing Timeline API](https://www.visualcrossing.com/resources/documentation/weather-api/timeline-weather-api/) request returns daily rows (and hourly rows when near-term is enabled). Daily `precip` is summed in **inches** (`unitGroup=us`).

**Check timing:** When `updates_per_day = 1`, the loop sleeps until the next `check_hour`:`check_minute` in `location.timezone` (default 4:30 AM — before most morning irrigation). With more than one check per day, checks are evenly spaced over 24 hours instead.

- **Today** in the past window may blend observed rain with forecast rain for the rest of the day (`source: comb` in the API). That can block watering before a storm arrives — usually what you want for a rain bypass.
- **API cost** is about one record per day returned plus one per hour when near-term is enabled (~`past_days + forecast_days + 24` per check). Set `log_level = "DEBUG"` to log `queryCost` each request.
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

`./install.sh` can install the service with correct paths for your install directory. To install manually, edit `deploy/rain-bypass.service` (paths and `.venv` python), then:

```bash
sudo cp deploy/rain-bypass.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rain-bypass
```

## License

MIT