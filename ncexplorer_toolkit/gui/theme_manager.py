from PyQt6.QtCore import QObject, pyqtSignal


class ThemeManager(QObject):
    theme_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.current_theme = 'light'
        self.themes = {
            'light': {
                'background': '#ffffff',
                'foreground': '#000000',
                'land': '#f5f5f5',
                'ocean': '#e6f3ff',
                'coastline': '#666666',
                'borders': '#999999'
            },
            'dark': {
                'background': '#1a1a1a',
                'foreground': '#ffffff',
                'land': '#2d2d2d',
                'ocean': '#1a1a1a',
                'coastline': '#cccccc',
                'borders': '#888888'
            }
        }

    def set_theme(self, theme_name):
        """Set the current theme"""
        if theme_name in self.themes:
            self.current_theme = theme_name
            self.theme_changed.emit(theme_name)

    def get_theme_colors(self, theme_name=None):
        """Get colors for a specified theme"""
        theme_name = theme_name or self.current_theme
        return self.themes.get(theme_name, self.themes['light'])
