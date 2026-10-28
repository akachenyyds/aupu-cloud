# Project working agreements

- This repository contains a third-party AUPU Home Assistant integration, not a Home Assistant configuration directory.
- Keep account data, phone numbers, OTPs, MQTT credentials, real device identifiers, captures and vendor application code out of the repository and its Git history.
- Tests use synthetic fixtures and mocks. Run `python scripts/verify_project.py` and `python scripts/run_tests.py` after relevant changes.
- Preserve TLS verification, ownership checks, correlated replies and the rule against automatically repeating uncertain control commands.
- Record framework tests, real cloud reads, LAN verification, physical control, HA installation/reload and long-term authorization as separate evidence.
- Do not deploy to an existing HA or operate physical devices unless the user explicitly authorizes that scope.
- Refresh `audit/SHA256SUMS.json` after changing release files; that manifest excludes itself. Keep historical review archives intact.
