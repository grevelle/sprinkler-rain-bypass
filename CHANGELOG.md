# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [5.1.2] - 2026-07-04

### Changed

- Default daily check time is **midnight** (`00:00`) for irrigation programs that start around **1:00 AM**.

## [5.1.1] - 2026-07-04

### Changed

- **Install/configure wizard** now prompts for **`inches_per_cycle`** and **daily check time** (HH:MM); `./configure.sh` merges into existing `settings.toml` instead of resetting to the example file.

## [5.1.0] - 2026-07-04

### Added

- **`rain-bypass status`** — read-only text dashboard (weather, balance, relay, “would decide”); `--cached` skips the API.
- **`./configure.sh`** — fast reconfigure of API key and ZIP without a full `./install.sh`.

### Changed

- Default **`balance.inches_per_cycle`** is **0.3** (30 min/zone daily program).

## [5.0.0] - 2025-06-27

### Added

- **Seasonal balance** — daily ON/OFF from prorated monthly Kentucky bluegrass targets, `rain_mtd`, forecast, and `[balance].inches_per_cycle`.
- New module `rain_bypass.balance` and required `[balance]` settings section.

### Changed

- **Breaking:** replaced six-gate rain bypass with balance + safety (freeze, storm event). No backward compatibility with v4 `settings.toml`.
- **Breaking:** removed `inches_required`, `forecast_inches_max`, `near_term_*`, `rain_delay_days`, `freeze_skip`, `updates_per_day`, `past_days`; `forecast_days` moved to `[balance]`.
- `Watering` uses `event_lookback_days` (default 3) instead of `past_days`.
- Weather fetch uses daily rows only; `WeatherSnapshot` exposes `rain_mtd`.
- `State` / `Decision` track `balance_month` and `irrigation_inches_mtd`; removed `blocked_until`.
- Minimal `settings.example.toml`; installer reminds users to calibrate `inches_per_cycle`.

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
- Pi Zero W tuning, `scripts/ci.*`, pre-push hooks, unpinned deps policy, DRY systemd template.

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

[Unreleased]: https://github.com/grevelle/sprinkler-rain-bypass/compare/v5.1.2...HEAD
[5.1.2]: https://github.com/grevelle/sprinkler-rain-bypass/compare/v5.1.1...v5.1.2
[5.1.1]: https://github.com/grevelle/sprinkler-rain-bypass/compare/v5.1.0...v5.1.1
[5.1.0]: https://github.com/grevelle/sprinkler-rain-bypass/compare/v5.0.0...v5.1.0
[5.0.0]: https://github.com/grevelle/sprinkler-rain-bypass/compare/v4.0.0...v5.0.0
[4.0.0]: https://github.com/grevelle/sprinkler-rain-bypass/compare/v3.2.0...v4.0.0
[3.2.0]: https://github.com/grevelle/sprinkler-rain-bypass/compare/v3.1.0...v3.2.0
[3.1.0]: https://github.com/grevelle/sprinkler-rain-bypass/compare/v3.0.0...v3.1.0
[3.0.0]: https://github.com/grevelle/sprinkler-rain-bypass/releases/tag/v3.0.0
