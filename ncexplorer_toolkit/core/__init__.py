"""Core engine: CDO subprocess integration and the operator schema registry.

This sub-package is intentionally light — no Qt, no Cartopy, no rasterio.
Importing it only pulls in the standard library, dataclasses, and the
auto-generated CDO operator catalog.
"""

from __future__ import annotations

from .categories import NCExplorerCategory
from .nc_integration import (
    NCExplorerError,
    NCExplorerIntegration,
    NCExplorerResult,
    create_NCExplorer_integration,
    create_native_NCExplorer,
    create_wsl_NCExplorer,
)

__all__ = [
    "NCExplorerCategory",
    "NCExplorerError",
    "NCExplorerIntegration",
    "NCExplorerResult",
    "create_NCExplorer_integration",
    "create_native_NCExplorer",
    "create_wsl_NCExplorer",
]
