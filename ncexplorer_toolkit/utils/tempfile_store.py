import os
import signal
import tempfile
import threading
from pathlib import Path
from typing import List, Optional

class TempFileStore:
    """Manage temporary files created by the wrapper with graceful cleanup."""

    def __init__(self, base_dir: Optional[str] = None, tag: str = "cdo_temp") -> None:
        self.base_dir = Path(base_dir or tempfile.gettempdir())
        self._tag = tag
        self._files: List[Path] = []

        # Check if we're running in the main thread
        self._is_main_thread = threading.current_thread() is threading.main_thread()

        # register signal handlers if we're in the main thread | # register SIGINT / SIGTERM cleanup only once
        if self._is_main_thread and signal.getsignal(signal.SIGTERM) != signal.SIG_IGN:
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, self._cleanup)  # type: ignore[arg-type]

    # public helpers ------------------------------------------------------
    def new(self, *, suffix: str = ".nc") -> str:
        fd, path = tempfile.mkstemp(prefix=f"{self._tag}_", suffix=suffix, dir=self.base_dir)
        os.close(fd)
        self._files.append(Path(path))
        return path

    def cleanup(self) -> None:
        for fp in self._files:
            try:
                fp.unlink(missing_ok=True)
            except OSError:
                pass
        self._files.clear()

    # internal ------------------------------------------------------------
    def _cleanup(self, *_: object) -> None:  # pragma: no cover
        self.cleanup()
        raise SystemExit(130)

    def __del__(self) -> None:
        self.cleanup()