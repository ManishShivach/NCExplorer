import sys
import traceback


def main():
    # Fast-path: --version / -V exits before Qt is touched.
    # Used by build.py's smoke-test to confirm the executable starts cleanly.
    if any(arg in ("--version", "-V") for arg in sys.argv[1:]):
        from ncexplorer_toolkit import APP_NAME, __version__
        print(f"{APP_NAME} {__version__}")
        return 0

    from PyQt6.QtCore import Qt  # noqa: F401  (kept for downstream consumers)
    from PyQt6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)

    try:
        # Import here to catch import errors
        from ncexplorer_toolkit import NCExplorerOperatorGUI

        print("Creating main window...")
        window = NCExplorerOperatorGUI()

        print("Showing main window...")
        window.show()

        print("Starting event loop...")
        exit_code = app.exec()
        print(f"Application finished with exit code: {exit_code}")

    except ImportError as e:
        print(f"Import Error: {e}")
        traceback.print_exc()
        QMessageBox.critical(None, "Import Error", f"Failed to import modules:\n{str(e)}")
        return 1

    except Exception as e:
        print(f"Startup Error: {e}")
        traceback.print_exc()
        QMessageBox.critical(None, "Startup Error", f"Failed to start application:\n{str(e)}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
