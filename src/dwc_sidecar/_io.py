"""Small filesystem helpers shared across emitters and appenders.

Kept separate so ``watch``, ``append``, ``lock``, ``transfer`` and ``bundle``
all write sidecars the same crash-safe way without importing each other.
"""
import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write via temp file + os.replace so a crash mid-write never leaves a
    truncated sidecar or state file (same pattern as ale_emitter.update_ale)."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
