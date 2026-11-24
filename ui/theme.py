"""
Look of the app: a dim ink-blue workspace (easy on the eyes during long transfers),
Bahnschrift (built into Windows 10/11) for headings and numbers, Segoe UI for text.
Colours: blue for actions, green for done / connected, red for problems, amber for attention.
"""
from PyQt5 import QtGui

DISPLAY = ["Bahnschrift", "Inter Display", "Segoe UI", "DejaVu Sans"]
TEXT = ["Segoe UI", "Inter", "DejaVu Sans"]

PALETTES = {
    "app": dict(bg="#0E1A24", panel="#15242F", raised="#1B2D3A", line="#26394A", text="#E6EEF3",
                 muted="#8BA2B2", faint="#5C7384", action="#5AB8FF", action_text="#06121B",
                 signal="#FFB547", open="#3DD68C", alarm="#FF5D5D", video="#081118"),
}


def font(size, weight=QtGui.QFont.Normal, display=False):
    f = QtGui.QFont()
    f.setFamilies(DISPLAY if display else TEXT)
    f.setPixelSize(int(size))
    f.setWeight(weight)
    return f


def qss(role):
    c = PALETTES[role]
    return f"""
    QWidget {{ background: {c['bg']}; color: {c['text']}; }}
    QLabel {{ background: transparent; }}
    QLabel[muted="true"] {{ color: {c['muted']}; }}
    QFrame#panel {{ background: {c['panel']}; border: 1px solid {c['line']}; border-radius: 12px; }}
    QFrame#rail {{ background: {c['panel']}; border-right: 1px solid {c['line']}; }}
    QPushButton {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['line']};
                   border-radius: 10px; padding: 10px 14px; }}
    QPushButton:hover {{ border-color: {c['action']}; }}
    QPushButton:focus {{ border: 2px solid {c['action']}; }}
    QPushButton:disabled {{ color: {c['faint']}; border-color: {c['line']}; }}
    QPushButton[kind="primary"] {{ background: {c['action']}; color: {c['action_text']}; border: none;
                                   font-weight: 600; }}
    QPushButton[kind="open"] {{ background: {c['open']}; color: #04140C; border: none; font-weight: 600; }}
    QPushButton[kind="danger"] {{ background: transparent; color: {c['alarm']}; border: 1px solid {c['alarm']}; }}
    QPushButton[kind="live"] {{ background: {c['signal']}; color: #1C1200; border: none; font-weight: 600; }}
    QPushButton[kind="primary"]:disabled, QPushButton[kind="open"]:disabled, QPushButton[kind="danger"]:disabled,
    QPushButton[kind="live"]:disabled {{ background: {c['raised']}; color: {c['faint']}; border: 1px solid {c['line']}; }}
    QPushButton[kind="rail"] {{ background: transparent; border: none; border-radius: 10px; padding: 8px;
                                color: {c['muted']}; text-align: left; }}
    QPushButton[kind="rail"]:checked {{ background: {c['raised']}; color: {c['text']}; }}
    QPushButton[kind="rail"]:hover {{ color: {c['text']}; }}
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
        background: {c['raised']}; border: 1px solid {c['line']}; border-radius: 10px; padding: 9px 12px;
        selection-background-color: {c['action']}; }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border: 2px solid {c['action']}; }}
    QListWidget {{ background: transparent; border: none; outline: none; }}
    QListWidget::item {{ border-radius: 10px; padding: 6px; }}
    QListWidget::item:selected {{ background: {c['raised']}; color: {c['text']}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {c['line']}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QToolTip {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['line']}; padding: 6px; }}
    QTableWidget {{ background: {c['raised']}; border: none; border-radius: 10px; gridline-color: {c['line']};
                    selection-background-color: {c['line']}; }}
    QCheckBox {{ background: transparent; }}
    QHeaderView::section {{ background: transparent; color: {c['muted']}; border: none; padding: 6px; }}
    """
