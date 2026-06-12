"""MHL v1/v2 detection and parsing edge cases.

Windows DIT tools (ShotPut Pro) emit BOM-prefixed XML; a BOM is not
whitespace, so without utf-8-sig decoding the file used to fall through to
the YAML parser and silently mis-parse.
"""
import pytest

from dwc_sidecar.mhl import parse_mhl

V1_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hashlist version="1.1">
  <hash>
    <file>Camera/A001/A001_C042.ari</file>
    <size>1024</size>
    <xxhash64be>abcdef0123456789</xxhash64be>
  </hash>
</hashlist>
"""

V2_YAML = """\
Version: 2.0.0
Hashes:
  - File: Camera/A001/A001_C042.ari
    SHA256: 746285cf7774d723f46593f146168426eb65b2ac169e67764fd65dcbe40e75d4
"""


def test_v1_plain(tmp_path):
    p = tmp_path / "a.mhl"
    p.write_text(V1_XML)
    out = parse_mhl(p)
    assert out["Version"] == "1.1"
    assert out["Hashes"][0]["File"] == "Camera/A001/A001_C042.ari"
    assert out["Hashes"][0]["xxh64"] == "abcdef0123456789"   # xxhash64be → xxh64


def test_v1_with_utf8_bom(tmp_path):
    p = tmp_path / "bom.mhl"
    p.write_bytes(b"\xef\xbb\xbf" + V1_XML.encode())
    out = parse_mhl(p)
    assert out["Version"] == "1.1"
    assert out["Hashes"][0]["xxh64"] == "abcdef0123456789"


def test_v1_unexpected_root_raises(tmp_path):
    p = tmp_path / "weird.mhl"
    p.write_text('<?xml version="1.0"?><notamhl><hash/></notamhl>')
    with pytest.raises(ValueError, match="root element"):
        parse_mhl(p)


def test_v2_yaml(tmp_path):
    p = tmp_path / "b.ascmhl"
    p.write_text(V2_YAML)
    out = parse_mhl(p)
    assert out["Version"] == "2.0.0"
    assert "sha256" in out["Hashes"][0]   # SHA256 → sha256 (normalised)


def test_xml_without_declaration_is_still_xml(tmp_path):
    """Detection keys on '<', not on '<?xml' — an MHL written without the
    XML declaration must not reach the YAML parser."""
    p = tmp_path / "nodecl.mhl"
    p.write_text(V1_XML.split("\n", 1)[1])
    out = parse_mhl(p)
    assert out["Hashes"][0]["xxh64"] == "abcdef0123456789"
