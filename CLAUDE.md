# VIGICam — Project Instructions for Claude

## Read First

At the start of any VIGICam session, read `vigicam-context.md` (gitignored, local only).
It contains camera IPs, credentials location, full API documentation, current integration
state, all bugs fixed, and the TODO list.

## Committing changes

When the user intends to commit changes to git, read `rituals/commit-ritual.md` and follow it.

---

## Deployment

```bash
# 1. Edit files in custom_components/vigicam/
# 2. Follow rituals/commit-ritual.md (branch → changelog → version → PR)
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
