"""Surface the one-time Metadata-inspector setup required before
``apply_dwc_metadata.py`` can actually write fields.

Resolve's ``MediaPoolItem.SetMetadata(key, value)`` silently returns
``False`` for any field not pre-registered as a custom metadata field
in the project — there's no scripting API to create custom fields
(re-checked against the vendor READMEs at ``resources/documentation/``
and no programmatic surface was found).

The pragmatic answer is: tell the DIT exactly what to do in the UI.
Walk-through targets DaVinci Resolve 20.2+, which added the Metadata
inspector's three-dot → Create Custom Metadata dialog. Earlier
versions exposed this under Project Settings → General Options →
Metadata & Scene; that path is gone in 20.2 and later.
"""
from __future__ import annotations

import sys
from typing import Iterable


DWC_FIELDS: tuple[str, ...] = (
    "DWC_Signed", "DWC_Kid", "DWC_Events", "DWC_Locks",
    "DWC_LockedBy", "DWC_LastVerified", "DWC_SidecarPath", "DWC_ChainHead",
)


def format_setup_message(fields: Iterable[str] = DWC_FIELDS) -> str:
    """Return the copy-pasteable setup instructions block."""
    bullets = "\n".join(f"      • {f}" for f in fields)
    return (
        "One-time Resolve setup for DWC metadata\n"
        "=======================================\n"
        "\n"
        "Resolve's SetMetadata API silently drops writes to fields that\n"
        "aren't pre-registered as custom metadata. Add these eight fields\n"
        "before running apply_dwc_metadata.py. Steps target DaVinci\n"
        "Resolve 20.2+ (the three-dot Metadata menu). On older Resolve,\n"
        "the same fields live under Project Settings > General Options\n"
        "> Metadata & Scene.\n"
        "\n"
        "  1. Go to the Media (or Edit) page\n"
        "  2. Select any clip in the Media Pool so the Metadata inspector\n"
        "     populates\n"
        "  3. Click the three-dot options menu at the top of the Metadata\n"
        "     tab and choose 'Create Custom Metadata'\n"
        "  4. For each of the field names below, enter the name exactly,\n"
        "     pick field type 'Text Input', and tick 'Show in all\n"
        "     projects' so the field persists across future projects:\n"
        "\n"
        f"{bullets}\n"
        "\n"
        "  5. To review, edit, or reorder later, three-dot menu >\n"
        "     Manage Custom Metadata.\n"
        "  6. To view only the DWC fields on a clip, set the Metadata\n"
        "     tab's group-filter dropdown (top-right) to 'Custom'.\n"
    )


def main(argv: list[str] | None = None) -> int:
    _ = argv  # argparse-less: single-purpose tool
    print(format_setup_message(DWC_FIELDS))
    print(
        "If the Resolve scripting API adds a create-field surface in a\n"
        "future release, update this helper to call it directly."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
