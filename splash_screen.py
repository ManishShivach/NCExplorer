"""Standalone preview of the NCExplorer startup splash.

Run it to see exactly what a user sees while the application loads:

    python splash_screen.py             # play the startup sequence once
    python splash_screen.py --hold      # stop at 100% and stay up until closed
    python splash_screen.py --step 1.0  # seconds per step (default 0.45)

Click the splash to dismiss it early — QSplashScreen closes on mouse press.

The drawing itself lives in ``ncexplorer_toolkit/gui/splash.py``, which is what
``main.py`` shows and what the frozen application ships; this module only
wraps it.  That split is deliberate: a preview that draws its own copy of the
splash stops being a preview the first time the real one changes.

The manager API below (``create_splash_screen``, ``next_step``,
``close_splash``) exists for scripts that want to drive the splash themselves.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python splash_screen.py` from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6.QtCore import QTimer                            # noqa: E402
from PyQt6.QtWidgets import QApplication                   # noqa: E402

from ncexplorer_toolkit.__version__ import APP_NAME        # noqa: E402
from ncexplorer_toolkit.gui.splash import NCExplorerSplash  # noqa: E402
from ncexplorer_toolkit.resources.branding import app_icon  # noqa: E402

__all__ = [
    "NCExplorerSplash",
    "SplashScreenManager",
    "create_splash_screen",
    "show_splash_with_timer",
    "LOADING_STEPS",
]

#: The captions main.py drives the real splash with, in order, so the preview
#: shows the sequence a user actually gets.  Reproduced rather than imported:
#: the real ones are emitted from the middle of startup — some from inside the
#: main window's constructor — and there is no list to import.
LOADING_STEPS: list[tuple[str, int]] = [
    (f"Starting {APP_NAME}…", 0),
    ("Loading the operator catalogue…", 10),
    ("Loading the map libraries…", 20),
    ("Building the interface…", 38),
    ("Locating CDO…", 47),
    ("Building menus and toolbars…", 53),
    ("Drawing the map canvas…", 64),
    ("Adding the docks…", 86),
    ("Loading the analysis panels…", 94),
    ("Ready", 100),
]


class SplashScreenManager:
    """Owns a splash screen and walks it through the loading steps."""

    def __init__(self, steps: list[tuple[str, int]] | None = None) -> None:
        self.splash: NCExplorerSplash | None = None
        self.loading_steps = list(steps if steps is not None else LOADING_STEPS)
        self.current_step = 0

    def show_splash(self) -> NCExplorerSplash:
        """Create, show and paint the splash."""
        self.splash = NCExplorerSplash()
        self.splash.show()
        # Nothing has entered the event loop yet, so pump it once by hand or
        # the window is mapped but never painted.
        QApplication.processEvents()
        return self.splash

    def update_loading_step(self, step_index: int | None = None) -> bool:
        """Show one step; returns False once the sequence is exhausted."""
        if step_index is not None:
            self.current_step = step_index
        if self.splash is None or self.current_step >= len(self.loading_steps):
            return False

        message, progress = self.loading_steps[self.current_step]
        self.splash.set_progress(progress, message)
        self.current_step += 1
        return True

    def next_step(self) -> bool:
        return self.update_loading_step()

    def set_custom_message(self, message: str, progress: int | None = None) -> None:
        if self.splash is not None:
            self.splash.set_progress(
                self.splash.progress if progress is None else progress,
                message,
            )

    def close_splash(self, main_window=None) -> None:
        if self.splash is None:
            return
        if main_window is not None:
            # Waits for the window to be exposed, so there is no flash of
            # desktop between the two.
            self.splash.finish(main_window)
        else:
            self.splash.close()
        self.splash = None


def create_splash_screen() -> SplashScreenManager:
    """A manager with the standard loading steps, nothing shown yet."""
    return SplashScreenManager()


def show_splash_with_timer(app: QApplication, duration: int = 3000) -> SplashScreenManager:
    """Show the splash and close it after ``duration`` milliseconds."""
    manager = create_splash_screen()
    splash = manager.show_splash()
    QTimer.singleShot(duration, splash.close)
    return manager


def _preview(step_seconds: float, hold: bool) -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    manager = create_splash_screen()
    manager.show_splash()

    interval = max(1, int(step_seconds * 1000))
    timer = QTimer()

    def advance() -> None:
        if manager.next_step():
            return
        timer.stop()
        if hold:
            print("At 100% — close the splash (click it) to exit.")
            return
        # Let the finished bar be seen before it disappears.
        QTimer.singleShot(900, app.quit)

    timer.timeout.connect(advance)
    timer.start(interval)

    # A splash screen is not a normal top-level window, so closing it does not
    # emit lastWindowClosed and the preview would hang with nothing on screen.
    # Watch its visibility instead; the click-to-dismiss path lands here too.
    watchdog = QTimer()
    watchdog.timeout.connect(
        lambda: None if (manager.splash and manager.splash.isVisible()) else app.quit()
    )
    watchdog.start(200)

    return app.exec()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--step", type=float, default=0.45, metavar="SECONDS",
                        help="how long each loading step is shown (default 0.45)")
    parser.add_argument("--hold", action="store_true",
                        help="stay on screen at 100%% instead of closing")
    args = parser.parse_args()
    return _preview(args.step, args.hold)


if __name__ == "__main__":
    sys.exit(main())
