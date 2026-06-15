# VIGICam — Project Instructions for Claude

## Read First

At the start of any VIGICam session, read `vigicam-context.md` (gitignored, local only).
It contains camera IPs, credentials location, full API documentation, current integration
state, all bugs fixed, and the TODO list.

## Commit Ritual — ALWAYS follow this before every commit

**Every commit must include these three things:**

### 1. Update CHANGELOG.md
Add an entry under `## [Unreleased]` describing what changed, using these labels:
- `### Added` — new entities, features, platforms
- `### Fixed` — bug fixes (include the root cause, not just the symptom)
- `### Changed` — behaviour changes to existing entities
- `### Removed` — entities or features removed

### 2. Bump the version in `custom_components/vigicam/manifest.json`
```
PATCH (x.x.1) — bug fixes, no new entities, no config changes
MINOR (x.1.x) — new entities, new features, backwards-compatible
MAJOR (1.x.x) — breaking changes requiring users to re-add the integration
```

### 3. Include both files in the commit
Stage `CHANGELOG.md` and `manifest.json` alongside whatever else changed.

---

## Deployment

```bash
# 1. Edit files in custom_components/vigicam/
# 2. Follow commit ritual above, then:
git add -A && git commit -m "..." && git push origin main

# 3. In HA: HACS → VIGI & InSight Cameras → Update → Restart HA
# 4. Check logs: Settings → System → Logs → search "vigicam"
```

## Key Gotchas

- `vigicam-context.md` is gitignored — never commit it (contains personal IPs/MACs)
- `.credentials.json` is gitignored — never commit it (contains passwords)
- HA runs Python 3.14 — **never use `ssl.create_default_context()`** in async code;
  use `async_get_clientsession(hass)` or `async_create_clientsession(hass, ...)` instead
- VIGI auth: stok is at the **top level** of the login response (not under `result`)
- Storage API: use `harddisk_manage` with `table: hd_info`, not `sd_card`
- Audio API: use `audio_config`, not `audio`
- Spotlight: set via `image.switch.night_vision_mode`, not `whiteLamp`

## GitHub

Repository: https://github.com/steveAbratt/VIGICam
