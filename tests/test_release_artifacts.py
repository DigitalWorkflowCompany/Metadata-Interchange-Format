"""Smoke-tests for the release artifacts produced by ``python -m build``.

Plan §6 (Track B). The Homebrew formula at
``tools/homebrew-tap/Formula/dwc-sidecar.rb`` consumes the sdist whose
URL is derived from the package name + version. If the sdist filename
ever drifts from ``dwc_sidecar-X.Y.Z.tar.gz`` (e.g. an underscore-vs-
hyphen rename, a build-system change), the formula will 404 silently
on the next release and Homebrew installs will break.

Runs in CI on every release tag (release-cli.yml) before the artifacts
are attached to the GitHub release.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_version() -> str:
    """Read the package version from pyproject.toml so this test stays
    in sync with whatever the next ``python -m build`` will produce.
    """
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        meta = tomllib.load(f)
    return meta["project"]["version"]


def _build_artifacts(tmp_path: Path) -> list[Path]:
    """Run ``python -m build`` against the repo into ``tmp_path`` and
    return the produced artifact paths. Skip the test if ``build`` is
    not installed locally — this test is primarily a CI guard.
    """
    try:
        import build  # noqa: F401
    except ImportError:
        pytest.skip("python -m build not available; install with `pip install build`")

    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(tmp_path), str(REPO_ROOT)],
        check=True,
        capture_output=True,
    )
    return sorted(tmp_path.iterdir())


def test_sdist_filename_matches_homebrew_formula_expectation(tmp_path):
    """The sdist must be named ``dwc_sidecar-X.Y.Z.tar.gz``.

    The Homebrew formula's ``url`` is constructed from this exact pattern.
    A package-name change (hyphen vs underscore, etc.) would break the
    formula's URL on the next bump.
    """
    artifacts = _build_artifacts(tmp_path)
    version = _read_version()
    expected_sdist = f"dwc_sidecar-{version}.tar.gz"
    sdists = [p for p in artifacts if p.name.endswith(".tar.gz")]
    assert len(sdists) == 1, f"expected exactly one sdist, got {sdists}"
    assert sdists[0].name == expected_sdist, (
        f"sdist name {sdists[0].name!r} != expected {expected_sdist!r}; "
        f"the Homebrew formula at tools/homebrew-tap/Formula/dwc-sidecar.rb "
        f"derives its URL from this pattern — update both together"
    )


def test_wheel_filename_is_pure_python(tmp_path):
    """The wheel must be ``dwc_sidecar-X.Y.Z-py3-none-any.whl`` (pure
    Python, no compiled extensions). A platform-specific wheel (e.g.
    ``-cp312-cp312-macosx_*``) would mean we accidentally introduced a
    C extension and the formula's resource pinning becomes platform-
    sensitive.
    """
    artifacts = _build_artifacts(tmp_path)
    version = _read_version()
    expected_wheel = f"dwc_sidecar-{version}-py3-none-any.whl"
    wheels = [p for p in artifacts if p.name.endswith(".whl")]
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    assert wheels[0].name == expected_wheel, (
        f"wheel name {wheels[0].name!r} != expected {expected_wheel!r} — "
        f"a non-pure-Python wheel would force per-platform builds"
    )


def test_version_string_is_well_formed():
    """Version must be PEP 440-compatible — Homebrew formula resource
    URLs and tag patterns assume a vX.Y.Z[rcN] shape.
    """
    version = _read_version()
    assert re.fullmatch(r"\d+\.\d+\.\d+(rc\d+)?", version), (
        f"pyproject.toml version {version!r} doesn't match X.Y.Z[rcN] — "
        f"the release-cli.yml tag pattern won't trigger on it"
    )
