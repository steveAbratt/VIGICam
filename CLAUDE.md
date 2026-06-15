# VIGICam — Project Instructions for Claude

## Read First

At the start of any VIGICam session, read `vigicam-context.md` (gitignored, local only).
It contains camera IPs, credentials location, full API documentation, current integration
state, all bugs fixed, and the TODO list.

## Commit Ritual — follow every step, every time

### Step 1 — Create a branch (NEVER commit directly to main)

```bash
git checkout main && git pull origin main
git checkout -b fix/short-description    # or feat/ or docs/
```

- `fix/` — bug fixes
- `feat/` — new features or entities
- `docs/` — documentation only
- One logical change per branch; don't batch unrelated work

### Step 2 — Update CHANGELOG.md

Add an entry under `## [Unreleased]` describing what changed:
- `### Added` — new entities, features, platforms
- `### Fixed` — bug fixes (include the root cause, not just the symptom)
- `### Changed` — behaviour changes to existing entities
- `### Removed` — entities or features removed

### Step 3 — Bump the version in `custom_components/vigicam/manifest.json`

```
PATCH (x.x.1) — bug fixes, no new entities or config changes
MINOR (x.1.x) — new entities, new features, backwards-compatible
MAJOR (1.x.x) — breaking changes requiring users to re-add the integration
```

### Step 4 — Commit (include CHANGELOG.md + manifest.json every time)

```bash
git add CHANGELOG.md custom_components/vigicam/manifest.json <changed files>
git commit -m "Fix: ..."   # or Feat: / Docs: / Tweak:
```

### Step 5 — Open a PR, never push to main directly

```bash
git push origin <branch-name>
gh pr create --title "..." --body "..."
# Merge via GitHub PR — branch protection enforces this
```

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
