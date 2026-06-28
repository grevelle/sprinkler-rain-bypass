# Sprinkler Rain Bypass

## 2.1.0

- Collapse package to five modules: `config`, `weather`, `gpio`, `app`, `__main__`
- Pydantic validation replaces hand-written config parsing
- Python 3.11+; drop `tomli` and nested dataclass boilerplate
- Merge decision loop, state, and runner into `app.run()`

## 2.0.0

- Complete rewrite as installable Python package (`rain_bypass`)
- TOML configuration with `settings.example.toml`
- Open-Meteo weather provider (free, no API key)
- Visual Crossing provider retained
- Removed Google Geolocation; use fixed coordinates
- Separate JSON runtime state file
- Mock GPIO mode for development
- Configurable fail-safe behavior on API errors
- pytest suite and systemd unit under `deploy/`
- Restored original hardware photos under `docs/images/`
- Migration script for v1 `settings.ini`

## 1.x

- Original single-script implementation using Weather Underground / Dark Sky, then Visual Crossing and Google Geolocation
