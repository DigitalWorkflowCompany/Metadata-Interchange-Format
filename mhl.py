"""ASC MHL v1 (XML) and v2 (YAML) reader.

Both versions are parsed into a common dict shape:
  { "Version": "1.1" | "2.0.0", "Hashes": [ { "File": str, "<alg>": "<hex>" , ... }, ... ] }

Hash alg keys are normalised to lowercase (sha256, md5, sha1, xxh64, xxh3, blake3, c4).
MHL v1 uses 'xxhash64be' / 'xxhash64' — both are mapped to 'xxh64'.
"""
from pathlib import Path
import xml.etree.ElementTree as ET
import yaml  # type: ignore[import-not-found]

_V1_ALG_MAP = {
    "md5":        "md5",
    "sha1":       "sha1",
    "sha256":     "sha256",
    "sha512":     "sha512",
    "xxhash64":   "xxh64",
    "xxhash64be": "xxh64",
    "xxhash":     "xxh64",
    "xxhash3":    "xxh3",
    "blake3":     "blake3",
    "c4":         "c4",
}


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_mhl_v1(text: str) -> dict:
    root = ET.fromstring(text)
    out = {"Version": root.attrib.get("version", "1"), "Hashes": []}
    for child in root:
        if _strip_ns(child.tag) != "hash":
            continue
        entry: dict = {}
        for leaf in child:
            tag = _strip_ns(leaf.tag).lower()
            val = (leaf.text or "").strip()
            if tag == "file":
                entry["File"] = val
            elif tag == "size":
                entry["Size"] = int(val) if val.isdigit() else val
            elif tag == "lastmodificationdate":
                entry["LastModificationDate"] = val
            elif tag in _V1_ALG_MAP:
                entry[_V1_ALG_MAP[tag]] = val
        out["Hashes"].append(entry)
    return out


def parse_mhl_v2(text: str) -> dict:
    doc = yaml.safe_load(text) or {}
    # Already matches our internal shape except hash alg keys may be mixed case
    for h in doc.get("Hashes") or []:
        for k in list(h.keys()):
            if k in _V1_ALG_MAP:
                h[_V1_ALG_MAP[k]] = h.pop(k)
    return doc


def parse_mhl(path: Path) -> dict:
    text = Path(path).read_text()
    lead = text.lstrip()
    if lead.startswith("<?xml") or lead.startswith("<hashlist"):
        return parse_mhl_v1(text)
    return parse_mhl_v2(text)
