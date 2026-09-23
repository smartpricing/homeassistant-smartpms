# Security policy

## Reporting a vulnerability

Please do **not** open a public issue for security problems.

Report them privately through GitHub: **Security → Report a vulnerability**
on this repository (private vulnerability reporting), or ask the maintainers
listed in `custom_components/smartpms/manifest.json` (`codeowners`) for a
private channel. Include the integration version, your Home Assistant version
and the steps to reproduce.

## Supported versions

Only the latest release installed through HACS receives fixes.

## Sharing logs safely

Home Assistant stores the SmartPMS e-mail, password and API key of this
integration in `.storage/core.config_entries` (as it does for every
integration). Never share that file. The diagnostics download redacts the
credentials. Since the security fixes of epic SPCWR-141 the integration logs
neither credentials nor tokens, even with debug logging enabled; if you use an
older version, check debug logs for `token` before posting them.

Use a dedicated SmartPMS user with the least privileges that can read your
properties for this integration.
