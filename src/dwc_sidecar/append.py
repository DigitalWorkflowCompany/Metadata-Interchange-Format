"""Append-to-chain machinery for an *existing* sidecar.

The emitters (`bootstrap`, `mhl_walker`, `batch`, `watch`) all create
single-event documents; `sign_example` rewrites whole chains from scratch.
Nothing appended a fresh signed event to a chain a previous tool wrote — which
is exactly what lock co-signing (Phase 2) and transfer counter-signing
(Phase 3) need.

Design rule carried over from v0.2: **whoever appends last re-signs the head.**
And we *verify before appending*: `append_event` runs the structural
chain-integrity + binding stages over the target asset and refuses to extend a
broken chain. Without that gate the CLI would happily launder a tampered
sidecar by appending a fresh, correctly-signed tip on top of a broken prefix.
"""
import base64
import json
from datetime import datetime, timezone
from pathlib import Path

from ._io import atomic_write_text
from .canonical import canonical_bytes, event_hash, make_head, SIDECAR_NS
from .validate import (
    _iter_assets, _asset_payloads, _asset_label,
    validate_chain_integrity, validate_binding,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_sidecar_asset(path, *, clip_uuid: str | None = None):
    """Load a sidecar and return (doc, asset) for the asset to be appended to.

    Single-asset docs return that asset. Multi-asset docs (a reel) require
    ``clip_uuid`` to disambiguate — appending to the wrong asset of a reel is a
    silent provenance error, so we refuse rather than guess."""
    doc = json.loads(Path(path).read_text())
    assets = [a for a in _iter_assets(doc)
              if "dwc.sidecar.events" in _asset_payloads(a)]
    if not assets:
        raise ValueError(f"{path}: no asset carries a dwc.sidecar.events payload")

    if clip_uuid is not None:
        wanted = clip_uuid.removeprefix("urn:uuid:")
        matched = [a for a in assets if _asset_has_clip_uuid(a, wanted)]
        if not matched:
            raise ValueError(f"{path}: no asset with clip-uuid {clip_uuid!r} "
                             f"(available: {[_asset_label(a) for a in assets]})")
        if len(matched) > 1:
            raise ValueError(f"{path}: clip-uuid {clip_uuid!r} matches multiple assets")
        return doc, matched[0]

    if len(assets) > 1:
        raise ValueError(
            f"{path}: {len(assets)} assets carry events; pass --clip-uuid to choose "
            f"({', '.join(_asset_label(a) for a in assets)})")
    return doc, assets[0]


def _asset_has_clip_uuid(asset: dict, uuid: str) -> bool:
    for i in asset.get("identifier") or []:
        if (isinstance(i, dict)
                and i.get("identifierScope") == "dwc:clip-uuid"
                and i.get("identifierValue") == uuid):
            return True
    return False


def _events_entry(asset: dict) -> dict:
    entry = _asset_payloads(asset).get("dwc.sidecar.events")
    if entry is None:
        raise ValueError("asset has no dwc.sidecar.events payload")
    return entry


def verify_asset_chain(asset: dict) -> None:
    """Raise ValueError if the asset's chain or binding is already broken.
    Scoped to a one-asset doc so an unrelated broken sibling can't block (and a
    broken target can't be laundered)."""
    mini = {"Asset": [asset]}
    for fn in (validate_chain_integrity, validate_binding):
        result = fn(mini)
        if result["errors"]:
            bad = [ln for ln in result["lines"] if "FAIL" in ln or "not" in ln]
            raise ValueError(
                f"refusing to append: asset {_asset_label(asset)} fails "
                f"{result['title']} ({result['errors']} error(s)):\n  "
                + "\n  ".join(bad[:5]))


def append_event(asset: dict, body: dict, signer, *, verify: bool = True) -> dict:
    """Append one signed event to ``asset``'s chain and re-sign the head.

    ``body`` carries the event's semantic fields (actor, tool, action, target,
    and optionally ``artifacts``/``transfer``); seq, ts, prevHash, hash and sig
    are assigned here. Mutates ``asset`` in place and returns the new event."""
    if verify:
        verify_asset_chain(asset)

    entry  = _events_entry(asset)
    events = entry.setdefault("value", [])
    last   = events[-1] if events else None
    seq      = (last.get("seq", 0) + 1) if last else 1
    prev_hash = last.get("hash") if last else None

    ev: dict = {"seq": seq, "ts": body.get("ts") or _now_iso()}
    for k in ("actor", "tool", "action", "target", "transfer", "artifacts"):
        if k in body and body[k] is not None:
            ev[k] = body[k]
    ev["prevHash"] = prev_hash
    ev["hash"] = event_hash(ev)
    ev["sig"] = {"alg": "ed25519", "kid": signer.kid,
                 "value": base64.b64encode(signer.sign(canonical_bytes(ev))).decode()}
    events.append(ev)

    _rewrite_head(asset, ev, signer)
    return ev


def _rewrite_head(asset: dict, last_event: dict, signer) -> None:
    head = make_head(last_event, signer)
    cd = (((asset.get("assetFC") or {}).get("functionalProperties") or {})
          .get("customData"))
    if cd is None:
        raise ValueError("asset has no functionalProperties.customData to hold the head")
    for e in cd:
        if isinstance(e, dict) and e.get("domain") == "dwc.sidecar.head":
            e["value"] = head
            return
    cd.append({"domain": "dwc.sidecar.head",
               "namespace": SIDECAR_NS,
               "schema":    f"{SIDECAR_NS}/head.schema.json",
               "value": head})


def save_sidecar(path, doc) -> None:
    atomic_write_text(Path(path), json.dumps(doc, indent=2) + "\n")
