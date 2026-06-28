# Sprinkler Rain Bypass v2

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
