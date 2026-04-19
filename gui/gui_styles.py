from typing import Optional

import pyqtgraph as pg
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QPainterPath, QPen
from PyQt5.QtWidgets import QPushButton, QLayout

# Colores base para las curvas de cada canal (se repiten si hay mas)
CHANNEL_COLORS_BASE = [
    "#61afef",  # Azul (Canal 1)
    "#98c379",  # Verde (Canal 2)
    "#e06c75",  # Rojo Coral (Canal 3)
    "#c678dd",  # Morado (Canal 4)
    "#e5c07b",  # Amarillo (Canal 5)
    "#56b6c2",  # Cian (Canal 6)
    "#d19a66",  # Naranja (Canal 7)
    "#be5046",  # Rojo oscuro (Canal 8)
]


def apply_pyqtgraph_theme():
    """Configura pyqtgraph con el tema oscuro por defecto."""
    pg.setConfigOption("background", "#282c34")
    pg.setConfigOption("foreground", "#abb2bf")


def load_icon(path: str, color_hex: Optional[str] = None, size: int = 22) -> QIcon:
    """Carga un icono y opcionalmente lo tinta con un color dado."""
    pix = QPixmap(path)
    if pix.isNull():
        return QIcon()
    pix = pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    if color_hex:
        tinted = QPixmap(pix.size())
        tinted.fill(Qt.transparent)
        painter = QPainter(tinted)
        painter.drawPixmap(0, 0, pix)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QColor(color_hex))
        painter.end()
        return QIcon(tinted)
    return QIcon(pix)


def make_visibility_icon(is_visible: bool, color_hex: str, size: int = 18) -> QIcon:
    """Crea un icono simple de ojo para mostrar/ocultar contrasenas."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)

    color = QColor(color_hex)
    pen = QPen(color)
    pen.setWidth(2)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    margin = 2.5
    center_x = size / 2.0
    center_y = size / 2.0
    top_y = 4.0
    bottom_y = size - 4.0
    left_x = margin
    right_x = size - margin

    eye_path = QPainterPath()
    eye_path.moveTo(left_x, center_y)
    eye_path.quadTo(center_x, top_y, right_x, center_y)
    eye_path.quadTo(center_x, bottom_y, left_x, center_y)
    painter.drawPath(eye_path)

    if is_visible:
        painter.setBrush(color)
        painter.drawEllipse(center_x - 2.2, center_y - 2.2, 4.4, 4.4)
    else:
        painter.drawLine(int(left_x + 1), int(bottom_y - 1), int(right_x - 1), int(top_y + 1))

    painter.end()
    return QIcon(pix)


def make_nav_button(
    nav_layout: QLayout,
    text: str,
    checked: bool = False,
    extra_style: str = "",
    icon_path: Optional[str] = None,
    icon_color: Optional[str] = None,
    icon_size: int = 20,
    tooltip: Optional[str] = None,
) -> QPushButton:
    """Crea un boton de navegacion con texto visible."""
    btn = QPushButton(text)
    btn.setCheckable(True)
    btn.setAutoExclusive(True)
    btn.setChecked(checked)
    btn.setFocusPolicy(Qt.NoFocus)
    if icon_path:
        btn.setIcon(load_icon(path=icon_path, color_hex=icon_color, size=icon_size))
        btn.setIconSize(QSize(icon_size, icon_size))
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setStyleSheet(get_nav_button_stylesheet(True) + extra_style)
    nav_layout.addWidget(btn)
    return btn


def get_nav_button_stylesheet(is_dark: bool) -> str:
    if is_dark:
        return """
        QPushButton {
            background-color: #1e252e;
            border: none;
            color: #9da5b4;
            min-width: 90px;
            min-height: 42px;
            padding: 8px 12px;
            border-radius: 0px;
            font-size: 13px;
            font-weight: 600;
            text-align: center;
        }
        QPushButton:first-child { border-top-left-radius: 10px; }
        QPushButton:last-child { border-top-right-radius: 10px; }
        QPushButton:hover { background-color: #2c323c; }
        QPushButton:checked {
            background-color: #1e252e;
            color: #ffffff;
            border-bottom: 3px solid #0078d4;
        }
        QPushButton:pressed { background-color: #0b65b1; }
        """
    return """
        QPushButton {
            background-color: #ffffff;
            border: none;
            color: #475569;
            min-width: 90px;
            min-height: 42px;
            padding: 8px 12px;
            border-radius: 0px;
            font-size: 13px;
            font-weight: 600;
            text-align: center;
        }
        QPushButton:first-child { border-top-left-radius: 10px; }
        QPushButton:last-child { border-top-right-radius: 10px; }
        QPushButton:hover { background-color: #eef2f7; }
        QPushButton:checked {
            background-color: #ffffff;
            color: #111827;
            border-bottom: 3px solid #0078d4;
        }
        QPushButton:pressed { background-color: #dbeafe; }
        """


def get_dark_stylesheet() -> str:
    """Devuelve la cadena de estilo CSS para el tema oscuro."""
    return """
        QMainWindow { background-color: #21252b; }
        QLabel, QGroupBox { color: #abb2bf; }
        QLabel[role="sectionTitle"] { color: #f3f4f6; font-size: 18px; font-weight: 700; }
        QLabel[role="sectionHint"] { color: #9ca3af; font-size: 12px; }
        QLabel[role="statusTitle"] { color: #9ca3af; font-size: 11px; font-weight: 600; }
        QLabel[role="statusValue"] { color: #f9fafb; font-size: 15px; font-weight: 700; }
        QLabel[role="consoleTitle"] { color: #e5e7eb; font-size: 15px; font-weight: 700; }
        QPushButton {
            background-color: #2a3039;
            color: #e5e7eb;
            border: 1px solid #3b4350;
            padding: 10px 12px;
            font-weight: 600;
            border-radius: 8px;
        }
        QPushButton:hover { background-color: #343b46; }
        QPushButton:focus { outline: none; }
        QPushButton[variant="primary"] {
            background-color: #56b6c2;
            color: #111827;
            border: none;
        }
        QPushButton[variant="primary"]:hover { background-color: #6dc7d2; }
        QPushButton[variant="secondary"] {
            background-color: #1f2937;
            color: #e5e7eb;
            border: 1px solid #475569;
        }
        QPushButton[variant="secondary"]:hover { background-color: #273244; }
        QPushButton[variant="danger"] {
            background-color: #ef4444;
            color: #ffffff;
            border: none;
        }
        QPushButton[variant="danger"]:hover { background-color: #f25b5b; }
        QPushButton[variant="ghost"] {
            background-color: transparent;
            color: #cbd5e1;
            border: 1px solid #3b4350;
        }
        QPushButton[variant="ghost"]:hover { background-color: #2a3039; }
        QPushButton[variant="danger"][recording="true"] {
            background-color: #b91c1c;
            color: #ffffff;
        }
        QPushButton:disabled {
            background-color: #2b2f36;
            color: #5c6370;
            border: 1px solid #3e4451;
        }
        QLineEdit, QComboBox, QPlainTextEdit {
            background-color: #1b2027;
            color: #e5e7eb;
            border: 1px solid #3e4451;
            border-radius: 7px;
            padding: 6px 8px;
        }
        QComboBox::drop-down {
            border: none;
            width: 24px;
        }
        QComboBox QAbstractItemView {
            background-color: #1f2937;
            color: #e5e7eb;
            selection-background-color: #56b6c2;
            selection-color: #111827;
        }
        QWidget#PanelIzquierdo, QWidget#PanelDerechoSuperior, QWidget#PanelDerechoInferior {
            border: 1px solid #3e4451;
            border-radius: 12px;
            padding: 6px;
        }
        QWidget#NavBar {
            border-bottom: 1px solid #2b3038;
        }
        QWidget#StatusBar, QWidget#AcquisitionBar {
            background-color: #1a1f27;
            border: 1px solid #2f3744;
            border-radius: 12px;
        }
        QWidget#StatusCard {
            background-color: #222833;
            border: 1px solid #313a48;
            border-radius: 10px;
        }
        QWidget#StatusCard[state="success"] {
            background-color: #163826;
            border: 1px solid #22c55e;
        }
        QWidget#StatusCard[state="warning"] {
            background-color: #3b2f15;
            border: 1px solid #f59e0b;
        }
        QWidget#StatusCard[state="danger"] {
            background-color: #3b1f24;
            border: 1px solid #ef4444;
        }
        QGroupBox {
            margin-top: 5px;
            padding-top: 15px;
            border: 1px solid #3e4451;
            border-radius: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 5px;
        }
    """


def get_light_stylesheet() -> str:
    """Devuelve la cadena de estilo CSS para el tema claro."""
    return """
        QMainWindow { background-color: #f0f2f5; }
        QLabel, QGroupBox { color: #2c313a; }
        QLabel[role="sectionTitle"] { color: #111827; font-size: 18px; font-weight: 700; }
        QLabel[role="sectionHint"] { color: #4b5563; font-size: 12px; }
        QLabel[role="statusTitle"] { color: #6b7280; font-size: 11px; font-weight: 600; }
        QLabel[role="statusValue"] { color: #111827; font-size: 15px; font-weight: 700; }
        QLabel[role="consoleTitle"] { color: #111827; font-size: 15px; font-weight: 700; }
        QPushButton {
            background-color: #ffffff;
            color: #1f2937;
            border: 1px solid #c7d0dc;
            padding: 10px 12px;
            font-weight: 600;
            border-radius: 8px;
        }
        QPushButton:hover { background-color: #f7fafc; }
        QPushButton:focus { outline: none; }
        QPushButton[variant="primary"] {
            background-color: #0078d4;
            color: #ffffff;
            border: none;
        }
        QPushButton[variant="primary"]:hover { background-color: #1185e3; }
        QPushButton[variant="secondary"] {
            background-color: #e8eef7;
            color: #0f172a;
            border: 1px solid #bfd0e3;
        }
        QPushButton[variant="secondary"]:hover { background-color: #dde8f4; }
        QPushButton[variant="danger"] {
            background-color: #d43f5e;
            color: #ffffff;
            border: none;
        }
        QPushButton[variant="danger"]:hover { background-color: #e24f6f; }
        QPushButton[variant="ghost"] {
            background-color: transparent;
            color: #334155;
            border: 1px solid #c7d0dc;
        }
        QPushButton[variant="ghost"]:hover { background-color: #eef2f7; }
        QPushButton[variant="danger"][recording="true"] {
            background-color: #b4233f;
            color: #ffffff;
        }
        QPushButton:disabled {
            background-color: #c8d1dc;
            color: #7a8699;
            border: 1px solid #b0bbc7;
        }
        QLineEdit, QComboBox, QPlainTextEdit {
            background-color: #ffffff;
            color: #111827;
            border: 1px solid #c7d0dc;
            border-radius: 7px;
            padding: 6px 8px;
        }
        QComboBox::drop-down {
            border: none;
            width: 24px;
        }
        QComboBox QAbstractItemView {
            background-color: #ffffff;
            color: #111827;
            selection-background-color: #0078d4;
            selection-color: #ffffff;
            border: 1px solid #c7d0dc;
        }
        QWidget#PanelIzquierdo, QWidget#PanelDerechoSuperior, QWidget#PanelDerechoInferior {
            border: 1px solid #d0d7e0;
            border-radius: 12px;
            padding: 6px;
        }
        QWidget#NavBar {
            border-bottom: 1px solid #d6dde7;
        }
        QWidget#StatusBar, QWidget#AcquisitionBar {
            background-color: #ffffff;
            border: 1px solid #d6dde7;
            border-radius: 12px;
        }
        QWidget#StatusCard {
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 10px;
        }
        QWidget#StatusCard[state="success"] {
            background-color: #dcfce7;
            border: 1px solid #22c55e;
        }
        QWidget#StatusCard[state="warning"] {
            background-color: #fef3c7;
            border: 1px solid #f59e0b;
        }
        QWidget#StatusCard[state="danger"] {
            background-color: #fee2e2;
            border: 1px solid #ef4444;
        }
        QGroupBox {
            margin-top: 5px;
            padding-top: 15px;
            border: 1px solid #d0d7e0;
            border-radius: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 5px;
        }
    """


__all__ = [
    "CHANNEL_COLORS_BASE",
    "apply_pyqtgraph_theme",
    "load_icon",
    "make_visibility_icon",
    "make_nav_button",
    "get_nav_button_stylesheet",
    "get_dark_stylesheet",
    "get_light_stylesheet",
]
