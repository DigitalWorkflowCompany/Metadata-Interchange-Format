"""ASC CDL + AMF-embedded CDL extraction.

Returns a unified dict:
  { "id": str|None, "description": str|None,
    "slope":  (r,g,b), "offset": (r,g,b), "power": (r,g,b),
    "saturation": float }

extract_cdl_from_amf() returns a list of such dicts (an AMF can carry multiple
lookTransforms, each with its own SOP/Sat and an 'applied' flag).
"""
from pathlib import Path
import xml.etree.ElementTree as ET


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _triple(text: str):
    parts = text.split()
    if len(parts) != 3:
        raise ValueError(f"expected 3 floats, got: {text!r}")
    return tuple(float(p) for p in parts)


def _find(elem, local_name: str):
    for e in elem.iter():
        if _strip_ns(e.tag) == local_name:
            return e
    return None


def _find_any(elem, *names):
    """First descendant whose local name matches any of `names`."""
    for e in elem.iter():
        if _strip_ns(e.tag) in names:
            return e
    return None


def _extract_sop_sat(node) -> dict:
    sop = _find_any(node, "SOPNode")
    sat = _find_any(node, "SATNode", "SatNode")
    if sop is None:
        raise ValueError("no SOPNode found")
    def _t(leaf): return leaf.text if leaf is not None and leaf.text else ""
    slope  = _triple(_t(_find_any(sop, "Slope",  "slope")))
    offset = _triple(_t(_find_any(sop, "Offset", "offset")))
    power  = _triple(_t(_find_any(sop, "Power",  "power")))
    saturation = 1.0
    if sat is not None:
        s = _find_any(sat, "Saturation", "saturation")
        if s is not None and s.text:
            saturation = float(s.text.strip())
    return {"slope": slope, "offset": offset, "power": power,
            "saturation": saturation}


def parse_cdl(path: Path) -> dict:
    """Parse a standalone ASC CDL/CCC/CC file."""
    root = ET.parse(path).getroot()
    cc_parent = _find(root, "ColorCorrection") or root
    desc_el   = _find(cc_parent, "Description")
    result = {
        "id":          cc_parent.attrib.get("id"),
        "description": (desc_el.text.strip() if (desc_el is not None and desc_el.text) else None),
    }
    result.update(_extract_sop_sat(cc_parent))
    return result


def extract_cdl_from_amf(path: Path) -> list[dict]:
    """Return every <lookTransform> in an AMF that contains a SOPNode, with its
    'applied' flag and description."""
    root = ET.parse(path).getroot()
    out = []
    for e in root.iter():
        if _strip_ns(e.tag) != "lookTransform":
            continue
        sop = _find(e, "SOPNode")
        if sop is None:
            continue
        applied_attr = e.attrib.get("applied", "true").lower()
        desc_el = _find(e, "description")
        try:
            cdl_vals = _extract_sop_sat(e)
        except ValueError:
            continue
        out.append({
            "applied":     applied_attr == "true",
            "description": (desc_el.text.strip() if (desc_el is not None and desc_el.text) else None),
            "id":          None,
            **cdl_vals,
        })
    return out


def serialize_cdl(cdl: dict) -> str:
    """Serialise back to canonical ASC CDL XML (for round-trip tests)."""
    def fmt(t): return f"{t[0]:.6f} {t[1]:.6f} {t[2]:.6f}"
    id_attr  = f' id="{cdl["id"]}"' if cdl.get("id") else ""
    desc_el  = f"            <Description>{cdl['description']}</Description>\n" if cdl.get("description") else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ColorDecisionList xmlns="urn:ASC:CDL:v1.2">\n'
        '    <ColorDecision>\n'
        f'        <ColorCorrection{id_attr}>\n'
        f"{desc_el}"
        '            <SOPNode>\n'
        f'                <Slope>{fmt(cdl["slope"])}</Slope>\n'
        f'                <Offset>{fmt(cdl["offset"])}</Offset>\n'
        f'                <Power>{fmt(cdl["power"])}</Power>\n'
        '            </SOPNode>\n'
        '            <SATNode>\n'
        f'                <Saturation>{cdl["saturation"]:.6f}</Saturation>\n'
        '            </SATNode>\n'
        '        </ColorCorrection>\n'
        '    </ColorDecision>\n'
        '</ColorDecisionList>\n'
    )


def cdl_values_equal(a: dict, b: dict, tol: float = 1e-5) -> bool:
    """SOP+Sat value equality within a small float tolerance."""
    def close(x, y): return all(abs(i - j) <= tol for i, j in zip(x, y))
    return (close(a["slope"], b["slope"]) and
            close(a["offset"], b["offset"]) and
            close(a["power"], b["power"]) and
            abs(a["saturation"] - b["saturation"]) <= tol)
