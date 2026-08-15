"""Persistence of the recently-opened file list.

``QSettings`` is the store, which puts two requirements on the caller:

* ``QApplication`` must have an organization *and* an application name before
  the first ``QSettings()`` is constructed — otherwise Qt writes to an unnamed
  location and the list silently fails to survive a restart. ``main.py`` sets
  both from ``__version__.APP_NAME``.
* The value round-trips through the platform's own settings backend, and the INI
  backend collapses a one-element string list back into a bare string. Anything
  reading the setting has to cope with both shapes; :meth:`RecentFilesStore.stored`
  is the only place that does.

Paths are stored absolute and de-duplicated case-insensitively (Windows), most
recent first.
"""

from __future__ import annotations

import logging
import os

from PyQt6.QtCore import QSettings

logger = logging.getLogger(__name__)

MAX_RECENT_FILES = 10
SETTINGS_KEY = "recentFiles"


class RecentFilesStore:
    """The most recently opened file paths, newest first."""

    def __init__(self, settings: QSettings | None = None):
        # An injectable QSettings keeps this usable without a QApplication.
        self._settings = settings if settings is not None else QSettings()

    def stored(self) -> list[str]:
        """Every remembered path, including ones that no longer exist."""
        raw = self._settings.value(SETTINGS_KEY, [])
        if raw is None:
            return []
        if isinstance(raw, str):
            # Single-entry list, flattened by the INI backend.
            return [raw] if raw else []
        return [str(item) for item in raw if item]

    def paths(self) -> list[str]:
        """Remembered paths that still exist on disk.

        Missing paths are filtered out of the *display* but left in the setting:
        a file on an unmounted volume or a network share is absent today and back
        tomorrow, and deleting it from the list on sight would be unhelpful.
        """
        return [path for path in self.stored() if os.path.exists(path)]

    def add(self, path: str) -> list[str]:
        """Record one path as the most recent. Returns the new list."""
        if not path:
            return self.stored()

        absolute = os.path.abspath(path)
        key = os.path.normcase(absolute)
        remaining = [p for p in self.stored() if os.path.normcase(p) != key]
        updated = [absolute] + remaining
        del updated[MAX_RECENT_FILES:]

        self._settings.setValue(SETTINGS_KEY, updated)
        logger.debug("Recent files now: %s", updated)
        return updated

    def clear(self) -> None:
        """Forget every remembered path."""
        self._settings.remove(SETTINGS_KEY)
        logger.info("Recent files list cleared")
