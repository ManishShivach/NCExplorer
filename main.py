# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
import logging
import sys
import traceback

logger = logging.getLogger("ncexplorer.main")


def _set_windows_app_id(author: str, name: str, version: str) -> None:
    """Give Windows an explicit AppUserModelID so the taskbar shows our icon.

    Without this, a Python-hosted process is grouped under python.exe and the
    taskbar button shows the Python icon no matter what QWindow::setIcon says.
    Must run before the first window is created. No-op off Windows.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"{author}.{name}.{version}"
        )
    except Exception:                                      # noqa: BLE001
        logger.debug("Could not set the Windows AppUserModelID", exc_info=True)


def main():
    # Fast-path: --version / -V exits before Qt is touched.
    # Used by build.py's smoke-test to confirm the executable starts cleanly.
    if any(arg in ("--version", "-V") for arg in sys.argv[1:]):
        from ncexplorer_toolkit import APP_NAME, __version__
        print(f"{APP_NAME} {__version__}")
        return 0

    # Also before Qt, and for a stronger reason than --version's. This is
    # invoked by the Windows installer and by its RunOnce resume step, which
    # can run before a desktop session is fully up — starting Qt there would
    # turn a working repair into a crash. See core/nc_repair.py.
    if "--repair-cdo" in sys.argv[1:]:
        from ncexplorer_toolkit.core.nc_repair import main as repair_main
        return repair_main()

    from PyQt6.QtCore import Qt  # noqa: F401  (kept for downstream consumers)
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from ncexplorer_toolkit.__version__ import APP_AUTHOR, APP_NAME, __version__
    from ncexplorer_toolkit.utils.logging_setup import configure_logging

    # Handlers first, so anything logged during import or window construction is
    # captured. NCEXPLORER_DEBUG=1 raises the console to DEBUG.
    log_file = configure_logging()

    _set_windows_app_id(APP_AUTHOR, APP_NAME, __version__)

    app = QApplication(sys.argv)
    # QSettings() reads these; without both, the recent-files list is written to
    # an unnamed location and does not survive a restart.
    app.setOrganizationName(APP_AUTHOR)
    app.setApplicationName(APP_NAME)

    # Before any window exists: Qt gives every window the application icon
    # unless the window sets its own, so this one call covers the title bar, the
    # macOS dock, the Windows taskbar and the alt-tab switcher.
    from ncexplorer_toolkit.resources.branding import app_icon
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    logger.info("%s starting; log file: %s", APP_NAME, log_file or "console only")

    splash = None
    if "--no-splash" not in sys.argv[1:]:
        try:
            from ncexplorer_toolkit.gui.splash import show_splash
            splash = show_splash()
        except Exception:                                  # noqa: BLE001
            # A splash that cannot be drawn must never be the reason the
            # application does not start.
            logger.warning("Splash screen unavailable", exc_info=True)

    def progress(value: int, message: str) -> None:
        if splash is not None:
            splash.set_progress(value, message)

    # Constructing the main window is the longest single step — the canvas, the
    # docks and CDO discovery all happen inside it — so it reports its own
    # progress rather than leaving the bar parked. Its 0-1 fraction maps onto
    # the last WINDOW_SHARE of the bar.
    WINDOW_START, WINDOW_SHARE = 45, 55

    def window_progress(fraction: float, message: str) -> None:
        progress(WINDOW_START + int(fraction * WINDOW_SHARE), message)

    try:
        # Imported here — and in stages — for two reasons: to catch import
        # errors, and because these *are* the slow part of startup, so the
        # splash has something true to report while they run.
        progress(10, "Loading the operator catalogue…")
        import ncexplorer_toolkit                          # noqa: F401

        progress(20, "Loading the map libraries…")
        try:
            from ncexplorer_toolkit.geocanvas import canvas  # noqa: F401
        except ImportError:
            # Not fatal here: the main window imports the same module and will
            # raise with the real diagnostic a few lines below.
            logger.debug("Pre-loading geocanvas failed", exc_info=True)

        progress(38, "Building the interface…")
        from ncexplorer_toolkit import NCExplorerOperatorGUI

        logger.info("Creating main window")
        window = NCExplorerOperatorGUI(progress=window_progress)

        logger.info("Showing main window")
        progress(100, "Ready")
        window.show()
        if splash is not None:
            # finish() waits for the window to be exposed, so the splash is
            # never replaced by a grey rectangle mid-paint.
            splash.finish(window)
            splash = None

        logger.info("Starting event loop")
        exit_code = app.exec()
        logger.info("Application finished with exit code: %s", exit_code)

    except ImportError as e:
        if splash is not None:
            splash.close()
        logger.error("Import error: %s", e, exc_info=True)
        traceback.print_exc()
        QMessageBox.critical(None, "Import Error", f"Failed to import modules:\n{str(e)}")
        return 1

    except Exception as e:
        if splash is not None:
            splash.close()
        logger.error("Startup error: %s", e, exc_info=True)
        traceback.print_exc()
        QMessageBox.critical(None, "Startup Error", f"Failed to start application:\n{str(e)}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
