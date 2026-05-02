from PyQt6.QtCore import QSize
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QToolBar, QToolButton, QMenu

from ..core.categories import OPERATOR_CATEGORIES, NCExplorerCategory

class NCExplorerToolbar(QToolBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self.setup_ui()

    def setup_ui(self):
        self.setMovable(True)
        self.setIconSize(QSize(24, 24))

        # Create category menus
        self.category_menus = {}
        for category in NCExplorerCategory:
            menu = QMenu(category.value, self)
            self.category_menus[category] = menu

            # Create a tool button for each category
            btn = QToolButton(self)
            btn.setText(category.value)
            btn.setMenu(menu)
            btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            self.addWidget(btn)

            # Add operators to the menu
            self.populate_category_menu(category)

    def populate_category_menu(self, category):
        """Add operators to the category menu"""
        menu = self.category_menus[category]
        menu.clear()

        operators = OPERATOR_CATEGORIES.get(category, [])

        for operator in sorted(operators):
            action = QAction(operator, self)
            action.triggered.connect(lambda checked, op=operator: self.operator_selected(op))
            menu.addAction(action)

    def operator_selected(self, operator):
        """Handle operator selection from menu"""
        if self.main_window:
            self.main_window.show_operator_parameters(operator)