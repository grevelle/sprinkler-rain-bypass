# Sprinkler Rain Bypass

Raspberry Pi controller that sets a rain-bypass relay **ON or OFF** before each irrigation cycle using seasonal Kentucky bluegrass balance, month-to-date rain, and forecast — plus freeze and storm safety gates.

**Target hardware:** Raspberry Pi Zero W (also works on other Pi models with BCM GPIO).

Get a free API key at [Visual Crossing](https://www.visualcrossing.com/weather-api).

## Setup

On a **Raspberry Pi Zero W**, use the **latest Raspberry Pi OS** (32-bit is fine). Run the interactive installer — it checks for missing apt packages (venv, git, build tools on the Pi), applies defaults from `settings.example.toml`, resolves your **ZIP code** to coordinates (default **53029**), and only requires your Visual Crossing **API key** (press Enter everywhere else). Full command list: [Command reference](#command-reference).

```bash
git clone https://github.com/grevelle/sprinkler-rain-bypass.git
cd sprinkler-rain-bypass
chmod +x install.sh
./install.sh
```

Manual setup (or development off the Pi):

```bash
pip install -e ".[dev,gpio]"    # use [gpio] on the Pi
cp settings.example.toml settings.toml   # edit api_key (and zip_code if not 53029)
```

`settings.example.toml` is the canonical default config. Tests and `rain_bypass.settings_io` derive settings from it. Run `./install.sh` (or `rain-bypass-install`) for the interactive setup wizard.

**Change API key, ZIP, inches per cycle, or check time** — `./configure.sh` (see [Configure](#configure-api-key-zip-inchescycle-check-time)).

### Pi Zero W notes

| Topic | Recommendation |
| ----- | -------------- |
| OS | Latest **Raspberry Pi OS** (keep the image updated) |
| Prerequisites | `./install.sh` checks for **python3-venv**, **git**, and on the Pi **python3-dev** + **build-essential**; offers `sudo apt-get install` if anything is missing |
| First install | `./install.sh` on a Zero W can take **10–20 minutes** (slow CPU + SD card) |
| Wi‑Fi | Stable connection required for Visual Crossing; default API timeout is **45 s** |
| GPIO library | **`RPi.GPIO`** — correct for Zero W’s classic BCM2835 GPIO |
| Relay module | **3.3 V** logic (see Hardware below) |
| systemd | Installer sets `MemoryMax=256M` and `Nice=5` for the 512 MB Zero W |
| Maintenance | Optional **daily auto-update** at 04:00 — OS packages, `git pull`, pip, service restart (see [Automatic updates](#automatic-daily-updates)) |

On boot the relay **blocks watering** until the first check completes, then applies the decision from the daily check.

## Command reference

**On the Pi, `cd` into the repo first** — the venv and `settings.toml` live in `~/sprinkler-rain-bypass`, not your home directory:

```bash
cd ~/sprinkler-rain-bypass
```

Then use `.venv/bin/python -m rain_bypass …` or activate the venv so `rain-bypass` is on your PATH:

```bash
source .venv/bin/activate
rain-bypass status
```

Below, Pi examples use `cd ~/sprinkler-rain-bypass` where needed. Off-Pi dev commands assume you are already in the cloned repo.

### First-time setup

```bash
git clone https://github.com/grevelle/sprinkler-rain-bypass.git
cd sprinkler-rain-bypass
chmod +x install.sh configure.sh
./install.sh
```

Manual / dev setup (off the Pi or without the shell wrappers):

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[gpio]"          # Pi
pip install -e ".[dev,gpio]"                # dev machine
cp settings.example.toml settings.toml
.venv/bin/python -m rain_bypass.install_cli  # same wizard as ./install.sh
```

### Configure (API key, ZIP, inches/cycle, check time)

```bash
cd ~/sprinkler-rain-bypass
./configure.sh
# or:
.venv/bin/python -m rain_bypass.install_cli configure
.venv/bin/rain-bypass-install configure

# flags:
./configure.sh --skip-api-test              # skip Visual Crossing smoke test
./configure.sh --no-restart                 # write settings.toml only
```

Prompts update location (ZIP lookup), `inches_per_cycle`, and daily check time. **Other settings** (sewer window, GPIO, monthly balance overrides) are preserved.

Edit settings the wizard does not cover, then restart:

```bash
nano settings.toml
sudo systemctl restart rain-bypass
```

### Run the controller

```bash
cd ~/sprinkler-rain-bypass

# Continuous loop (what systemd runs):
.venv/bin/python -m rain_bypass
# or after: source .venv/bin/activate
rain-bypass

# One cycle — weather + decision + relay, then exit:
.venv/bin/python -m rain_bypass --once
rain-bypass --once

# Alternate config file:
.venv/bin/rain-bypass -c /path/to/settings.toml --once
```

Off-Pi development — set `gpio.mock = true` in `settings.toml`:

```bash
python -m rain_bypass --once
python -m rain_bypass
```

### Status dashboard

Read-only; never changes the relay or GPIO.

```bash
cd ~/sprinkler-rain-bypass
.venv/bin/python -m rain_bypass status

# Saved state only — no live API call:
.venv/bin/python -m rain_bypass status --cached

# Same after activating the venv:
source .venv/bin/activate
rain-bypass status --cached
```

### Logs

```bash
sudo journalctl -u rain-bypass -f              # follow live
sudo journalctl -u rain-bypass --since today
sudo journalctl -u rain-bypass --since "1 hour ago"
```

Set `log_level = "DEBUG"` in `settings.toml`, then `sudo systemctl restart rain-bypass`.

### systemd service

Installed by `./install.sh` as `rain-bypass`.

```bash
sudo systemctl status rain-bypass
sudo systemctl restart rain-bypass
sudo systemctl stop rain-bypass       # relay stays at last state
sudo systemctl start rain-bypass
sudo systemctl enable rain-bypass     # start on boot (install.sh does this)
```

Manual unit install (substitute paths first — see [systemd](#systemd) below):

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rain-bypass
```

### Update from GitHub (Pi)

```bash
cd ~/sprinkler-rain-bypass
git pull
.venv/bin/pip install -e '.[gpio]'
sudo systemctl restart rain-bypass
.venv/bin/python -m rain_bypass status    # optional sanity check
```

### Automatic daily updates

`./install.sh` on a Raspberry Pi can enable **two** complementary timers:

| Timer | When | What |
| ----- | ---- | ---- |
| `apt-daily-upgrade.timer` | ~06:00–07:00 (system default) | **OS packages** via `unattended-upgrades` (Debian/Raspbian standard); auto-reboot at **04:15** if needed |
| `rain-bypass-auto-update.timer` | **04:00** | **`git pull`**, **`pip install`**, restart `rain-bypass` |

`setup-autoupdate` installs `unattended-upgrades`, writes `/etc/apt/apt.conf.d/20auto-upgrades` and `51unattended-upgrades-rain-bypass`, and enables both timer sets.

Enable on an existing Pi:

```bash
cd ~/sprinkler-rain-bypass
git pull
.venv/bin/python -m rain_bypass.install_cli setup-autoupdate --yes
```

Check the timers:

```bash
sudo systemctl list-timers apt-daily-upgrade.timer rain-bypass-auto-update.timer
sudo journalctl -u rain-bypass-auto-update.service --since today
sudo journalctl -u apt-daily-upgrade.service --since today
```

Run app update once manually:

```bash
cd ~/sprinkler-rain-bypass
sudo ./scripts/auto-update.sh
```

Run OS update once manually:

```bash
sudo unattended-upgrade -v
```

Disable app auto-update:

```bash
sudo systemctl disable --now rain-bypass-auto-update.timer
```

Disable OS auto-update:

```bash
sudo rm /etc/apt/apt.conf.d/20auto-upgrades /etc/apt/apt.conf.d/51unattended-upgrades-rain-bypass
sudo systemctl disable --now apt-daily-upgrade.timer
```

### Development & CI

```bash
pip install -e ".[dev,gpio]"
pre-commit install                        # commit: Ruff + ShellCheck; push: Pyright + pytest

# Full CI locally (mirrors GitHub Actions):
./scripts/ci.sh                           # Linux / macOS / Pi
.\scripts\ci.ps1                          # Windows PowerShell

# Individual checks:
ruff check .
ruff check --fix .
ruff format .
ruff format --check .
shellcheck install.sh configure.sh scripts/auto-update.sh
python scripts/check_lf.py
pyright
pytest -q -m "not live" --cov=rain_bypass --cov-fail-under=100
pre-commit run --all-files
```

Live weather smoke test (needs real `settings.toml` with API key):

```bash
pytest -q -m live
python scripts/check_30day_rain.py        # print 30-day daily precip from Visual Crossing
```

## Usage

Once installed, the Pi runs as a **systemd service** (`rain-bypass`). You usually leave it alone — it wakes at the configured check time (default **midnight** local), fetches weather, decides ON or OFF for that day’s irrigation cycle, and sets the relay. Rain Bird (or your controller) runs its daily program as usual; the bypass relay acts like a rain sensor.

See [Command reference](#command-reference) for every shell command.

### Check status (text dashboard)

Any time you want to see what the system thinks, run `rain-bypass status` (or `status --cached` for offline).

Example output and how to read it:

```text
  Relay            BLOCK watering (relay closed / wet sensor)
  Rain MTD         0.26 in          ← rain this month at your ZIP
  Irrigation MTD   0.33 in          ← credited when last allowed watering
  Target to date   0.65 in          ← prorated July grass goal (day 4 of 31)
  Deficit          0.04 in          ← how far behind the lawn is
  Needs / cycle    >= 0.30 in       ← must reach this to allow watering
  Balance gate     block            ← deficit too small today
  Safety gate      pass             ← no freeze / storm block
  Would decide     BLOCK watering   ← what the next check would do (live fetch)
```

- **Relay** — current saved state driving the GPIO (green = allow, red = block).
- **Balance gate** — pass only when deficit ≥ `inches_per_cycle` (grass needs water).
- **Safety gate** — pass unless freeze or a heavy rain day in the lookback window.
- **Would decide** — live evaluation; does **not** change the relay (read-only).

`status` never runs a cycle or writes GPIO — safe to run anytime.

### View logs

Use `journalctl` — see [Logs](#logs) in the command reference. Set `log_level = "DEBUG"` in `settings.toml` for API `queryCost` and extra detail.

### Manual check (one cycle)

Runs weather + decision + relay once, then exits (same as the service’s daily tick). Use `rain-bypass --once` after changing settings to verify behavior without waiting for the next scheduled check.

### Change settings

| What | How |
| ---- | --- |
| API key, ZIP, inches/cycle, check time | `./configure.sh` (fast; restarts service) |
| Sewer window, GPIO, monthly balance, etc. | `nano settings.toml` then `sudo systemctl restart rain-bypass` |

After editing `settings.toml`, run `status` or `--once` to confirm the new logic.

## Config (`settings.toml`)


| Section    | Keys                                                                                                                                                                                                                                  |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `location` | `zip_code` (default **53029**), plus resolved `latitude`, `longitude`, `timezone` from install |
| `balance`  | `inches_per_cycle` (calibrate with catch cups); optional `forecast_days`, `[balance.monthly]` overrides |
| `watering` | `check_hour`, `check_minute`; optional `event_lookback_days`, `event_inches`, `freeze_temp_f` |
| `sewer`    | `start_month/day`, `end_month/day` — hard block window (annual sewer cap)                                                                                                                                                             |
| `weather`  | `api_key` (Visual Crossing Timeline API)                                                                                                                                                                                              |
| `gpio`     | `relay`, `watering_enabled_led`, `watering_disabled_led`, `mock`                                                                                                                                                                      |
| `runtime`  | `state_path`, `fail_mode`, `log_level`, `weather_timeout_seconds`                                                                                                                                                                     |


`fail_mode`: `disable_watering` (default) or `keep_last_state` when the weather API fails.

**Suggested starting values** (used by `./install.sh`): **Hartland, WI 53029** — **sewer lockout Jan 16–Mar 15**; **`balance.inches_per_cycle = 0.3`** (30 min/zone ≈ 0.3 in per daily run). Schedule Rain Bird for **one program every day**; this app sets the bypass before that cycle.

### Rain Bird setup

1. Set **one program** with your zones and run times.
2. Schedule the program **every day** at your preferred morning time.
3. Wire the bypass relay to the rain-sensor terminals (COM/NO as a dry contact).
4. Set the Pi check **before** your program (default **midnight** for a **1:00 AM** cycle).
5. **Calibrate `[balance].inches_per_cycle`** once with a catch-cup test on a full program run.

Relay **open** (green LED) = dry sensor = watering **allowed**. Relay **closed** (red LED) = wet sensor = cycle **skipped**.

### Sewer lockout

Many municipalities (including Hartland/Waukesha) set **annual sewer charges from water used January 16 through March 15** (shown on April utility bills). Any irrigation on the same water meter during that window increases your sewer bill for the **entire year**.

During the sewer lockout window:

- Watering is **hard blocked** — before weather checks or fail-mode logic.
- The relay stays in **block** mode; no API call is made.

### How watering is decided

Two layers run each morning check:

1. **Seasonal balance** — prorated monthly Kentucky bluegrass target minus month-to-date rain, credited irrigation, and forecast must reach at least **`inches_per_cycle`** before the switch turns **ON**.
2. **Safety** — block on freeze (today/tomorrow low below `freeze_temp_f`) or a heavy rain day in the lookback (`event_inches`).

```text
watering_required = balance_ok AND safety_ok AND NOT sewer
```

There is **no weekly schedule** — each day recomputes one ON/OFF for the next cycle only. One [Visual Crossing Timeline API](https://www.visualcrossing.com/resources/documentation/weather-api/timeline-weather-api/) request returns daily rows; `precip` is summed in **inches** (`unitGroup=us`).

**Check timing:** The loop sleeps until the next `check_hour`:`check_minute` in `location.timezone` (default **00:00** / midnight).

- Forecast rain reduces today’s balance before it falls (conservative). Tune `[balance].forecast_days` if the switch stays OFF too often.
- When the switch is **ON**, the model credits one `inches_per_cycle` to the month (it does not verify the panel actually ran).
- Set `log_level = "DEBUG"` to log `queryCost` each request.
- Match `location.timezone` to your coordinates; a mismatch with the API’s resolved timezone is logged as a warning.

### Upgrading from v4

Replace `settings.toml` from the new `settings.example.toml`. Removed keys include `inches_required`, `forecast_inches_max`, `near_term_*`, `rain_delay_days`, `updates_per_day`, and `past_days`. See [CHANGELOG.md](CHANGELOG.md).

## Code

Layered modules: `config`, `balance`, `settings_io`, `windows`, `weather` (httpx), `logic`, `controller`, `gpio`, `status`, `cli`, and `install_cli`. Typer powers both `rain-bypass` and `rain-bypass-install`. `settings.example.toml` is the single source for defaults. CI runs pre-commit, Pyright (strict), and pytest at 100% coverage on the latest Python 3.x.

## Development

Dependencies are **unpinned** — every install uses `pip install --upgrade` for the latest releases. See [Development & CI](#development--ci) in the command reference for install, hooks, and local CI commands.

`.gitattributes`, `.editorconfig`, and `.vscode/settings.json` keep **LF** line endings on all platforms (required for Pi and Linux CI). Install the **EditorConfig** extension if Cursor prompts you.

See [CHANGELOG.md](CHANGELOG.md) for release history.

## Hardware

### Parts

- **Raspberry Pi Zero W** (or any Pi with network access and BCM GPIO)
- GPIO breakout or breadboard
- 2× resistors (50–300 Ω), 1× 3.3 V relay module
- Green + red LED
- Irrigation controller with rain-bypass input

### Wiring diagram (default BCM pins)

Logic levels and pin numbers match `settings.example.toml`. Use **3.3 V** relay modules; many boards expose relay VCC on 5 V — check your module before wiring.

```mermaid
flowchart TB
  subgraph pi [Raspberry Pi]
    v33["3.3V"]
    gnd["GND"]
    gpio25["GPIO 25 — relay"]
    gpio4["GPIO 4 — green LED"]
    gpio27["GPIO 27 — red LED"]
  end

  subgraph relay [Relay module]
    rVCC["VCC"]
    rGND["GND"]
    rIN["IN"]
    rCOM["COM"]
    rNO["NO"]
  end

  subgraph leds [Status LEDs via 220 Ω]
    green["Green — watering allowed"]
    red["Red — watering blocked"]
  end

  subgraph controller [Irrigation controller]
    rainIn["Rain-bypass / sensor input"]
    rainCommon["Common"]
  end

  v33 --> rVCC
  gnd --> rGND
  gpio25 --> rIN
  rCOM --> rainCommon
  rNO --> rainIn

  gpio4 --> green
  gpio27 --> red
  green --> gnd
  red --> gnd
```

| GPIO signal | Pin | When HIGH / ON |
| ----------- | --- | -------------- |
| Relay IN | 25 | Watering **disabled** (relay energized — verify against your controller) |
| Green LED | 4 | Watering **allowed** |
| Red LED | 27 | Watering **blocked** |

Configure pins in `settings.toml` under `[gpio]`. Relay **COM/NO** (or your module’s dry-contact pair) goes to the controller’s rain-bypass terminals — same as a physical rain sensor would. Confirm whether your controller expects **normally open** or **normally closed** before leaving it unattended.

### Safety

Verify relay behavior with your controller before unattended use. Match relay rating to your bypass circuit.

## systemd

`./install.sh` installs the service with paths for your install directory. The unit template is `deploy/rain-bypass.service.in` (placeholders `@ROOT@`, `@PYTHON@`, `@SETTINGS@`, `@USER@`).

Manual install: substitute placeholders, then:

```bash
sed -e 's|@ROOT@|/opt/sprinkler-rain-bypass|g' \
    -e 's|@PYTHON@|/opt/sprinkler-rain-bypass/.venv/bin/python|g' \
    -e 's|@SETTINGS@|/opt/sprinkler-rain-bypass/settings.toml|g' \
    -e 's|@USER@|root|g' \
    deploy/rain-bypass.service.in | sudo tee /etc/systemd/system/rain-bypass.service
sudo systemctl daemon-reload
sudo systemctl enable --now rain-bypass
```

## License

MIT