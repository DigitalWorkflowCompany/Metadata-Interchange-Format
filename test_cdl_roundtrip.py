"""Two CDL tests against the real Colour-Information/ tree:
 A. serialization round-trip: parse → serialise → reparse → values equal
 B. cross-format: standalone CDL vs AMF-embedded lookTransforms for the same clip

Run:
  python3 test_cdl_roundtrip.py /Volumes/DWC_Shuttle-04/WAR/260115_SD084
"""
import sys
from pathlib import Path
from cdl import parse_cdl, extract_cdl_from_amf, serialize_cdl, cdl_values_equal


def round_trip(cdl_path: Path) -> tuple[bool, str]:
    original = parse_cdl(cdl_path)
    text = serialize_cdl(original)
    tmp = cdl_path.with_suffix(".roundtrip.cdl")
    tmp.write_text(text)
    try:
        reparsed = parse_cdl(tmp)
    finally:
        tmp.unlink()
    ok = cdl_values_equal(original, reparsed)
    return ok, ("match" if ok else f"drift after reparse — {original} vs {reparsed}")


def cross_format(cdl_path: Path, amf_path: Path) -> list[tuple[str, bool, str]]:
    cdl_vals = parse_cdl(cdl_path)
    amf_looks = extract_cdl_from_amf(amf_path)
    if not amf_looks:
        return [("no-amf-look", False, "AMF has no CDL-bearing lookTransforms")]
    results = []
    for i, look in enumerate(amf_looks):
        ok = cdl_values_equal(cdl_vals, look)
        label = f"look[{i}] '{look.get('description') or '?'}' applied={look['applied']}"
        if ok:
            results.append((label, True, "CDL == AMF lookTransform values"))
        else:
            diff = (f"CDL slope={cdl_vals['slope']} offset={cdl_vals['offset']} "
                    f"power={cdl_vals['power']} sat={cdl_vals['saturation']}  |  "
                    f"AMF slope={look['slope']} offset={look['offset']} "
                    f"power={look['power']} sat={look['saturation']}")
            results.append((label, False, diff))
    return results


def main():
    if len(sys.argv) < 2:
        print("usage: test_cdl_roundtrip.py <production-root>")
        return 2
    root    = Path(sys.argv[1])
    cdl_dir = root / "Colour-Information/CDLs/CDL_Output"
    amf_dir = root / "Colour-Information/AMF"

    cdl_files = sorted(cdl_dir.glob("*.cdl")) if cdl_dir.exists() else []
    if not cdl_files:
        print(f"no CDLs under {cdl_dir}")
        return 1

    print(f"A — serialization round-trip ({len(cdl_files)} file(s))")
    rt_pass = rt_fail = 0
    for p in cdl_files:
        ok, msg = round_trip(p)
        if ok:
            rt_pass += 1
        else:
            rt_fail += 1
            print(f"  FAIL {p.name}: {msg}")
    print(f"  {rt_pass} pass / {rt_fail} fail\n")

    print(f"B — cross-format (standalone CDL vs AMF-embedded lookTransforms)")
    cf_match = cf_diff = cf_skip = 0
    rows = []
    for cdl in cdl_files:
        amf = amf_dir / f"{cdl.stem}.amf"
        if not amf.exists():
            cf_skip += 1
            continue
        for label, ok, msg in cross_format(cdl, amf):
            if ok:
                cf_match += 1
                rows.append((cdl.stem, label, "✓", ""))
            else:
                cf_diff += 1
                rows.append((cdl.stem, label, "✗", msg))
    name_w = max((len(r[0]) for r in rows), default=0)
    for clip, label, mark, detail in rows[:20]:
        print(f"  {mark}  {clip:<{name_w}}  {label}")
    if len(rows) > 20:
        print(f"  … {len(rows) - 20} more")
    print(f"\n  {cf_match} match / {cf_diff} differ / {cf_skip} CDL without AMF")

    if cf_diff and cf_match == 0:
        print("\nNOTE: every AMF lookTransform differs from its standalone CDL.\n"
              "      Likely means the standalone CDL is the DIT on-set grade and\n"
              "      the AMF lookTransforms are independent post/dailies decisions.\n"
              "      Worth keeping BOTH as separate artifacts in the sidecar.")
    return 0 if rt_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
