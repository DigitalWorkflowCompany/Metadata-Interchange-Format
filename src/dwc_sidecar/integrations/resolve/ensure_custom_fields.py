"""Surface the one-time Project Settings setup required before
``apply_dwc_metadata.py`` can actually write fields.

Resolve's ``MediaPoolItem.SetMetadata(key, value)`` silently returns
``False`` for any field not pre-registered in the project's
Metadata & Scene settings — there's no API to create custom fields
(UNVERIFIED per plan §1.10 risks; re-checked against vendor READMEs
at ``resources/documentation/`` and no programmatic surface was found).

The pragmatic answer is: tell the DIT exactly what to do in the UI,
using the same eight field names the ALE emitter and Silverstack
integration produce. One manual setup, permanent effect.
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
        "aren't pre-registered in the project. Add these eight fields\n"
        "before running apply_dwc_metadata.py:\n"
        "\n"
        "  1. Open Project Settings (Cmd/Ctrl-,)\n"
        "  2. Select 'General Options'\n"
        "  3. Scroll to 'Metadata & Scene'\n"
        "  4. For each of the field names below, click '+', enter the\n"
        "     name exactly, choose type 'Text', and Save:\n"
        "\n"
        f"{bullets}\n"
        "\n"
        "  5. Re-save the project. Repeat for every project that will\n"
        "     receive DWC metadata — field definitions don't sync\n"
        "     across projects in Resolve.\n"
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
