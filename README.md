# Sprinkler Rain Bypass

Raspberry Pi rain-bypass controller: check recent precipitation, drive a relay, show status on red/green LEDs.

## Install

```bash
git clone https://github.com/grevelle/sprinkler-rain-bypass.git
cd sprinkler-rain-bypass
pip install -e ".[gpio]"   # omit [gpio] on dev machines
cp settings.example.toml settings.toml
```

Edit `settings.toml` with your coordinates, thresholds, and GPIO pins.

## Run

```bash
python -m rain_bypass --once   # test one cycle
python -m rain_bypass          # continuous loop
```

Set `gpio.mock = true` to develop without a Pi.

## Config

| Section | Purpose |
|---------|---------|
| `location` | Fixed lat/lon and timezone |
| `watering` | Rain threshold, lookback days, checks per day |
| `season` | Date range when watering may run |
| `weather` | `open_meteo` (default) or `visual_crossing` |
| `gpio` | BCM pins and mock mode |
| `runtime` | State file, fail mode, log level |

On weather API failure, `fail_mode = "disable_watering"` blocks watering; `"keep_last_state"` reuses the prior decision.

## Layout

```
src/rain_bypass/
  config.py   settings (Pydantic)
  weather.py    precipitation providers
  gpio.py       relay + LED control
  app.py        decision loop + state
  __main__.py   CLI
docs/hardware.md
deploy/rain-bypass.service
```

## systemd

```bash
sudo cp deploy/rain-bypass.service /etc/systemd/system/
sudo systemctl enable --now rain-bypass
```

Adjust `WorkingDirectory` and `ExecStart` paths in the unit file for your install location.

## Migrate from v1

```bash
python scripts/migrate_ini.py
```

Then set coordinates manually and switch to `open_meteo` if desired.

## Hardware

See [docs/hardware.md](docs/hardware.md).

## License

MIT
