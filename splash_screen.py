"""
Splash screen for GIS Toolkit application startup
Provides visual feedback during application loading
"""

import sys
from PyQt6.QtWidgets import QSplashScreen
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap, QFont, QPainter, QColor, QLinearGradient, QBrush


class GISSplashScreen(QSplashScreen):
    """
    Professional splash screen for GIS Toolkit
    Shows loading progress and status messages
    """

    def __init__(self):
        # Create a custom pixmap for the splash screen
        pixmap = self.create_splash_pixmap()
        super().__init__(pixmap)

        # Configure window properties
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.SplashScreen
        )

        # Initialize progress tracking
        self.progress_value = 0
        self.max_progress = 100

        # Show an initial message
        self.show_message("Initializing GIS Toolkit...", 0)

    @staticmethod
    def create_splash_pixmap():
        """Create a professional-looking splash screen background"""
        width, height = 500, 300
        pixmap = QPixmap(width, height)

        # Create a painter for custom drawing
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Create a gradient background
        gradient = QLinearGradient(0, 0, 0, height)
        gradient.setColorAt(0, QColor(25, 35, 45))  # Dark blue-gray
        gradient.setColorAt(0.5, QColor(45, 65, 85))  # Medium blue
        gradient.setColorAt(1, QColor(25, 35, 45))  # Dark blue-gray

        painter.fillRect(0, 0, width, height, QBrush(gradient))

        # Add title text
        painter.setPen(QColor(255, 255, 255))
        title_font = QFont("Arial", 24, QFont.Weight.Bold)
        painter.setFont(title_font)
        painter.drawText(50, 80, "GIS Toolkit")

        # Add subtitle
        subtitle_font = QFont("Arial", 12)
        painter.setFont(subtitle_font)
        painter.setPen(QColor(200, 200, 200))
        painter.drawText(50, 110, "Climate Data Operators GUI")

        # Add version info
        version_font = QFont("Arial", 10)
        painter.setFont(version_font)
        painter.setPen(QColor(180, 180, 180))
        painter.drawText(50, 140, "Version 1.0")

        # Add decorative elements
        painter.setPen(QColor(70, 130, 180))
        painter.drawLine(50, 160, 450, 160)

        painter.end()
        return pixmap

    def show_message(self, message, progress=None):
        """
        Show a loading message with optional progress

        Args:
            message: Status message to display
            progress: Progress value (0-100)
        """
        if progress is not None:
            self.progress_value = progress
            progress_text = f" ({progress}%)"
        else:
            progress_text = ""

        # Show a message with progress
        display_message = f"{message}{progress_text}"

        super().showMessage(
            display_message,
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
            QColor(255, 255, 255)
        )

        # Force immediate update
        self.repaint()

    def update_progress(self, value, message=""):
        """Update progress bar and message"""
        self.progress_value = min(value, self.max_progress)
        if message:
            self.show_message(message, self.progress_value)
        else:
            self.show_message("Loading...", self.progress_value)

    def paintEvent(self, event):
        """Custom paint event to add a progress bar"""
        super().paintEvent(event)

        # Draw the progress bar
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Progress bar dimensions
        bar_width = 400
        bar_height = 8
        bar_x = 50
        bar_y = 250

        # Draw a progress bar background
        painter.setBrush(QBrush(QColor(100, 100, 100)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(bar_x, bar_y, bar_width, bar_height, 4, 4)

        # Draw progress bar fill
        if self.progress_value > 0:
            fill_width = int((self.progress_value / self.max_progress) * bar_width)
            painter.setBrush(QBrush(QColor(70, 130, 180)))
            painter.drawRoundedRect(bar_x, bar_y, fill_width, bar_height, 4, 4)

        painter.end()


class SplashScreenManager:
    """
    Manages splash screen lifecycle and progress updates
    """

    def __init__(self):
        self.splash = None
        self.loading_steps = [
            ("Loading Qt framework...", 10),
            ("Initializing GIS integration...", 25),
            ("Setting up GeoCanvas...", 45),
            ("Preparing map canvas...", 65),
            ("Creating user interface...", 80),
            ("Finalizing startup...", 95),
            ("Ready!", 100)
        ]
        self.current_step = 0

    def show_splash(self):
        """Show the splash screen"""
        self.splash = GISSplashScreen()
        self.splash.show()
        return self.splash

    def update_loading_step(self, step_index=None):
        """Update to the next loading step or specific step"""
        if step_index is not None:
            self.current_step = step_index

        if self.splash and self.current_step < len(self.loading_steps):
            message, progress = self.loading_steps[self.current_step]
            self.splash.update_progress(progress, message)
            self.current_step += 1

    def next_step(self):
        """Move to the next loading step"""
        self.update_loading_step()

    def set_custom_message(self, message, progress=None):
        """Set a custom loading message"""
        if self.splash:
            self.splash.show_message(message, progress)

    def close_splash(self, main_window=None):
        """Close the splash screen"""
        if self.splash:
            if main_window:
                self.splash.finish(main_window)
            else:
                self.splash.close()
            self.splash = None


# Convenience functions for easy integration
def create_splash_screen():
    """Create and return a splash screen manager"""
    return SplashScreenManager()


def show_splash_with_timer(app, duration=3000):
    """
    Show a splash screen for a specific duration

    Args:
        app: QApplication instance
        duration: Duration in milliseconds
    """
    splash_manager = create_splash_screen()
    splash = splash_manager.show_splash()

    # Auto-close after duration
    QTimer.singleShot(duration, splash.close)

    return splash_manager


# Example usage for testing
if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)

    # Create a splash screen manager
    splash_manager = create_splash_screen()
    splash = splash_manager.show_splash()


    # Simulate loading steps
    def simulate_loading():
        import time
        for i in range(7):
            time.sleep(0.5)  # Simulate work
            splash_manager.next_step()
            app.processEvents()  # Keep UI responsive

        # Close splash after demo
        QTimer.singleShot(1000, splash.close)


    # Start simulation
    QTimer.singleShot(500, simulate_loading)

    sys.exit(app.exec())
