from PyQt6.QtGui import QValidator

class QIntValidator(QValidator):
    """Simple integer validator"""
    def validate(self, text, pos):
        if text == "" or text.isdigit():
            return (QValidator.State.Acceptable, text, pos)
        return (QValidator.State.Invalid, text, pos)

class QDoubleValidator(QValidator):
    """Simple float validator"""
    def validate(self, text, pos):
        if text == "":
            return (QValidator.State.Acceptable, text, pos)
        try:
            float(text)
            return (QValidator.State.Acceptable, text, pos)
        except ValueError:
            return (QValidator.State.Invalid, text, pos)

# Add a new validator for double values with an auto option
class QDoubleAutoValidator(QValidator):
    """Float validator that allows 'Auto' (0) value"""
    def validate(self, text, pos):
        if text == "" or text == "0":
            return (QValidator.State.Acceptable, text, pos)
        try:
            float(text)
            return (QValidator.State.Acceptable, text, pos)
        except ValueError:
            return (QValidator.State.Invalid, text, pos)