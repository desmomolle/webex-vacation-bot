# Changelog

Alle nennenswerten Änderungen an diesem Projekt.
Format nach [Keep a Changelog](https://keepachangelog.com),
Gruppierung nach [Conventional Commits](https://www.conventionalcommits.org).
## [unreleased]

### Bug Fixes
- Unclosed {% if step == "summary" %} in setup.html caused 500 on /setup (ca285e7)

### CI/CD
- Auto-changelog via git-cliff reusable workflow (d8a3e84)
- Inline changelog workflow (public repo can't call private reusable) (3ba7f98)
- Stricter cliff.toml — drop non-conventional commits via catch-all skip (8fb795f)

### Documentation
- Add logs & diagnostics section with platform-specific examples (623954e)
- Restructure README — quick start first, fix all inconsistencies (993c12a)

### Features
- Browser setup wizard, configurable internal domain (4391154)
- Enable/disable toggle in WebUI, fix status key, README cleanup (d1cc36b)
- DEMO_MODE — click through the whole app without real credentials (f1c8fa8)
- Settings page to edit config after setup (1e56489)

### Security
- Fernet encryption, setup password, CSRF protection (0de1d62)


