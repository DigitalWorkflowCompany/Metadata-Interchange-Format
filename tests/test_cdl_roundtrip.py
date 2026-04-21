"""CDL roundtrip + AMF cross-format comparison against a real production corpus.

Requires the DWC_CORPUS env var pointing at a production root (e.g.
/Volumes/DWC_Shuttle-04/WAR/260115_SD084). Skipped if the corpus is not
mounted — pytest should stay green on laptops without the shuttle.
"""
import os
from pathlib import Path

import pytest

from dwc_sidecar.cdl import (
    parse_cdl, extract_cdl_from_amf, serialize_cdl, cdl_values_equal,
)


def _corpus_root() -> Path | None:
    env = os.environ.get("DWC_CORPUS")
    if not env:
        return None
    root = Path(env)
    return root if root.exists() else None


def _cdl_files() -> list[Path]:
    root = _corpus_root()
    if root is None:
        return []
    cdl_dir = root / "Colour-Information/CDLs/CDL_Output"
    return sorted(cdl_dir.glob("*.cdl")) if cdl_dir.exists() else []


_CDL_FILES = _cdl_files()
_CDL_IDS   = [p.stem for p in _CDL_FILES]

pytestmark = pytest.mark.skipif(
    _corpus_root() is None or not _CDL_FILES,
    reason="DWC_CORPUS env var unset, root not mounted, or no CDLs under it",
)


@pytest.mark.parametrize("cdl_path", _CDL_FILES, ids=_CDL_IDS)
def test_cdl_serialization_roundtrip(cdl_path: Path, tmp_path: Path):
    original = parse_cdl(cdl_path)
    text     = serialize_cdl(original)
    tmp      = tmp_path / f"{cdl_path.stem}.roundtrip.cdl"
    tmp.write_text(text)
    reparsed = parse_cdl(tmp)
    assert cdl_values_equal(original, reparsed), (
        f"CDL drift after reparse: {original} vs {reparsed}"
    )


def test_standalone_cdl_and_amf_are_independent_artifacts():
    """Stage-9-style informational check: confirm the production corpus has
    at least one clip where the standalone CDL differs from the AMF-embedded
    lookTransform, which validates the design decision to carry both as
    separate artifacts rather than picking one as canonical."""
    root = _corpus_root()
    assert root is not None
    amf_dir = root / "Colour-Information/AMF"
    any_pair_checked = False
    any_divergence   = False
    for cdl in _cdl_files():
        amf = amf_dir / f"{cdl.stem}.amf"
        if not amf.exists():
            continue
        cdl_vals  = parse_cdl(cdl)
        amf_looks = extract_cdl_from_amf(amf)
        if not amf_looks:
            continue
        any_pair_checked = True
        for look in amf_looks:
            if not cdl_values_equal(cdl_vals, look):
                any_divergence = True
                break
        if any_divergence:
            break
    if not any_pair_checked:
        pytest.skip("no CDL/AMF pairs found to compare")
    assert any_divergence, (
        "every AMF lookTransform matches its standalone CDL — unusual for "
        "real productions; verify the corpus is what you think it is"
    )
