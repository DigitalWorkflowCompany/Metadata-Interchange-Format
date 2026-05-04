---
created: 2026-05-04T09:08:39.709Z
title: Bump GitHub Actions for Node.js 24 deprecation
area: tooling
files:
  - .github/workflows/macos-statusbar.yml
  - .github/workflows/release-cli.yml
  - .github/workflows/web-validator.yml
  - .github/workflows/homebrew-tap-bump.yml
  - .github/workflows/hosted-schema-drift.yml
---

## Problem

GitHub Actions deprecation warning surfaced on the 2026-05-04 macos-statusbar
run (run id 25310566147):

> Node.js 20 actions are deprecated. The following actions are running on
> Node.js 20 and may not work as expected: actions/checkout@v4,
> actions/setup-python@v5, actions/upload-artifact@v4. Actions will be
> forced to run with Node.js 24 by default starting June 2nd, 2026. Node.js
> 20 will be removed from the runner on September 16th, 2026.

Affected workflows in this repo (every Node.js-based action call):

- `.github/workflows/macos-statusbar.yml` — actions/checkout@v4,
  actions/setup-python@v5, actions/upload-artifact@v4
- `.github/workflows/release-cli.yml` — actions/checkout@v4,
  actions/setup-python@v5, actions/upload-artifact@v4
- `.github/workflows/web-validator.yml` — actions/checkout@v4,
  actions/setup-python@v5, actions/upload-artifact@v4,
  actions/download-artifact@v4
- `.github/workflows/homebrew-tap-bump.yml` — none (pure shell + brew)
- `.github/workflows/hosted-schema-drift.yml` — actions/checkout@v4
  (verify; may also use setup-python)

Reference:
<https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/>

## Solution

Two viable paths; pick one before 2026-06-02:

1. **Bump action major versions** (preferred — long-term clean): check each
   action's repo for a release that runs on Node.js 24 and update the `@vN`
   pin. As of writing, the v4/v5 majors above will gain a Node.js 24
   build; new majors (e.g. checkout@v5) may also be available — verify
   per-action.
2. **Opt-in flag** (short-term hedge): set
   `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true` at workflow or job env level.
   Forces the existing pins to run on Node.js 24 immediately. Use this if
   bumping majors introduces breaking changes that need a separate fix.

Validation: re-run each workflow after the change and confirm the
deprecation warning is gone. If something breaks, the temporary escape
hatch (until 2026-09-16) is `ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION=true`
to keep Node.js 20 — do not rely on this past September 2026.

Hard deadline: **2026-09-16** when Node.js 20 is removed from runners.
Soft deadline: **2026-06-02** when Node.js 24 becomes the default and the
deprecation warning escalates.
