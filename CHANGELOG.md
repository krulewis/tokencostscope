# Changelog

## [0.1.5] - Unreleased

### Changed

- **Telemetry is now ON by default.** Previously required `--telemetry` flag
  or `TOKENCAST_TELEMETRY=1`. Now on unless explicitly disabled. Opt out:
  - Call the `disable_telemetry` tool (permanent)
  - Pass `--no-telemetry` CLI flag
  - Set `TOKENCAST_TELEMETRY=0`
- `--telemetry` flag is now a deprecated no-op (accepted for backward compat)
- First-run notice updated with explicit opt-out instructions
- README telemetry disclosure moved above the fold

### Added

- `disable_telemetry` MCP tool -- one call permanently disables telemetry
- Persistent opt-out file: `~/.tokencast/no-telemetry`

### Fixed

- (none)
