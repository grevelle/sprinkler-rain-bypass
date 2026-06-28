# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Pi Zero W tuning: safe GPIO boot state, systemd memory cap, installer/SD-card pip flags, README platform notes.

### Changed

- Default `weather_timeout_seconds` increased to **45** for slow Pi Zero W Wi‑Fi.

## [4.0.0] - 2025-06-27

### Changed

- **Breaking:** removed monolithic `rain_bypass.app`; use layered modules (`weather`, `logic`, `controller`, `cli`, etc.) or package exports from `rain_bypass`.
- Replaced `requests` with **httpx**; tests use **respx**.
- Unified both entry points on **Typer** (`rain-bypass`, `rain-bypass-install`).
- Enabled **Pyright strict** on application code; CI matrix on Python **3.11** and **3.12**.

### Added

- `models`, `exceptions`, `windows`, `weather`, `logic`, `controller`, and `cli` modules.
- `WeatherError` for Visual Crossing failures.
- `.pre-commit-config.yaml` and Dependabot for pip and GitHub Actions.

## [3.2.0] - 2025-06

### Changed

- Replaced the bash installer wizard with a **Typer** CLI (`install_cli.py`); `install.sh` is now a thin bootstrap.
- Consolidated defaults in `settings.example.toml` via `settings_io`.
- Dropped irrigation **season** lockout; kept **sewer-only** hard block (Jan 16–Mar 15).

### Added

- Pyright, ShellCheck, and `py.typed` in CI.

## [3.1.0] - 2025

### Added

- Sewer baseline hard block during the city measurement window.
- Near-term hourly and freeze-skip gates.
- Past + forecast rain logic with event threshold and rain delay.

## [3.0.0] - 2025

### Changed

- Visual Crossing Timeline API replaces Open-Meteo.
- Pydantic v2 config, 100% test coverage enforced in CI.

[Unreleased]: https://github.com/grevelle/sprinkler-rain-bypass/compare/v4.0.0...HEAD
[4.0.0]: https://github.com/grevelle/sprinkler-rain-bypass/compare/v3.2.0...v4.0.0
[3.2.0]: https://github.com/grevelle/sprinkler-rain-bypass/compare/v3.1.0...v3.2.0
[3.1.0]: https://github.com/grevelle/sprinkler-rain-bypass/compare/v3.0.0...v3.1.0
[3.0.0]: https://github.com/grevelle/sprinkler-rain-bypass/releases/tag/v3.0.0
