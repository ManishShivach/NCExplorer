"""Cross-cutting utilities used throughout NCExplorer.

Currently a thin module — eager imports are fine because nothing here is heavy.
"""

from __future__ import annotations

from .tempfile_store import TempFileStore

__all__ = ["TempFileStore"]
