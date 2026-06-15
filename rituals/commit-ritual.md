# Commit Ritual

Follow every step, every time, in order.

---

### Step 1 — Create a branch (NEVER commit directly to main)

```bash
git checkout main && git pull origin main
git checkout -b fix/short-description    # or feat/ or docs/
```

- `fix/` — bug fixes
- `feat/` — new features or entities
- `docs/` — documentation only
- One logical change per branch; don't batch unrelated work

---

### Step 2 — Update CHANGELOG.md

Add an entry under `## [Unreleased]` describing what changed:
- `### Added` — new entities, features, platforms
- `### Fixed` — bug fixes (include the root cause, not just the symptom)
- `### Changed` — behaviour changes to existing entities
- `### Removed` — entities or features removed

---

### Step 3 — Bump the version in `custom_components/vigicam/manifest.json`

```
PATCH (x.x.1) — bug fixes, no new entities or config changes
MINOR (x.1.x) — new entities, new features, backwards-compatible
MAJOR (1.x.x) — breaking changes requiring users to re-add the integration
```

---

### Step 4 — Commit (include CHANGELOG.md + manifest.json every time)

```bash
git add CHANGELOG.md custom_components/vigicam/manifest.json <changed files>
git commit -m "Fix: ..."   # or Feat: / Docs: / Tweak:
```

---

### Step 5 — Open a PR, never push to main directly

```bash
git push origin <branch-name>
gh pr create --title "..." --body "..."
# Merge via GitHub PR — branch protection enforces this
```
