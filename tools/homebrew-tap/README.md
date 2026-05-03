# Homebrew formula for `dwc-sidecar` CLI

This directory holds the canonical source of `Formula/dwc-sidecar.rb`. It is
not the live tap — the live tap lives at:

    https://github.com/DigitalWorkflowCompany/homebrew-tap

End users install via:

    brew install digitalworkflowcompany/tap/dwc-sidecar

Plan §6 (Track B). Goal: a single `brew install` line for the CLI, parallel to
the existing `brew install --cask dwc-sidecar-status` for the menu-bar app.

## How publishing works

1. Tag a release: `git tag v0.4.0 && git push --tags`.
2. `.github/workflows/release-cli.yml` builds an sdist + wheel and attaches
   them to the GitHub release.
3. `.github/workflows/homebrew-tap-bump.yml` triggers on `release: published`,
   computes the sdist's sha256, and runs `brew bump-formula-pr` against the
   tap repo with the new URL + sha256.
4. `brew test-bot` on the tap repo verifies the bump installs cleanly before
   the PR can merge.

The formula in this directory is the **template**. The tap repo's copy is the
live one; PRs from the bump workflow target that repo, not this one.

## First-time tap bootstrap (one-time, manual)

Steps to stand up the tap repo for the first publish:

1. Create the repo: `gh repo create DigitalWorkflowCompany/homebrew-tap
   --public --description "Homebrew tap for DWC tools"`.
2. Copy `Formula/dwc-sidecar.rb` from this directory into the repo's
   `Formula/` directory.
3. Make a release of `dwc-sidecar` from the main repo (publishes the sdist).
4. Locally, run `brew tap DigitalWorkflowCompany/tap` then
   `brew update-python-resources Formula/dwc-sidecar.rb` — this rewrites every
   `resource` block with current PyPI URLs and sha256s, replacing the
   `REPLACE_WITH_PYPI_SHA256` placeholders.
5. Compute the sdist sha256:
   `curl -L <sdist-url> | shasum -a 256` and update the formula's `sha256`
   field.
6. Test: `brew install --build-from-source ./Formula/dwc-sidecar.rb` then
   `brew test ./Formula/dwc-sidecar.rb`.
7. Commit + push to the tap repo.

After that, the bump workflow handles every subsequent release.

## Why a tap and not homebrew-core

Homebrew core requires 50+ stargazers, "notability", and a stable release
history. We are below that threshold. The tap path is faster, fully under our
control, and visually equivalent to end users (one `brew install` line).
Migration to homebrew-core is a Phase 03+ candidate if/when adoption justifies
it.

## Why not `brew install --cask`?

The existing menu-bar app ships as a cask (`dwc-sidecar-status`) because it's
a notarised `.app` bundle. The CLI is a Python package — formulae are the
right Homebrew shape for that. The two coexist under the same tap.
