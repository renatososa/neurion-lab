from typing import Optional

import pyqtgraph as pg
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor
from PyQt5.QtWidgets import QPushButton, QLayout

# Colores base para las curvas de cada canal (se repiten si hay más)
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
    pg.setConfigOption("background", "#282c34")  # Fondo oscuro tipo 'Atom One Dark'
    pg.setConfigOption("foreground", "#abb2bf")  # Texto y líneas en color claro


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


def make_nav_button(
    nav_layout: QLayout,
    text: str,
    checked: bool = False,
    extra_style: str = "",
    icon_path: Optional[str] = None,
    icon_color: Optional[str] = None,
    icon_size: int = 22,
    tooltip: Optional[str] = None,
) -> QPushButton:
    """Crea un botón de la navbar con estilo y lo añade al layout indicado."""
    btn = QPushButton(text)
    btn.setCheckable(True)
    btn.setAutoExclusive(True)
    btn.setChecked(checked)
    btn.setFocusPolicy(Qt.NoFocus)
    if icon_path:
        btn.setIcon(load_icon(icon_path, icon_color, icon_size))
        btn.setIconSize(QSize(icon_size, icon_size))
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setStyleSheet(
        """
        QPushButton {
            background-color: #1e252e;
            border: none;
            color: #9da5b4;
            min-width: 40px;
            min-height: 38px;
            padding: 6px 10px;
            border-radius: 0px;
            font-size: 18px;
        }
        QPushButton:first-child { border-top-left-radius: 8px; }
        QPushButton:last-child { border-top-right-radius: 8px; }
        QPushButton:hover {
            background-color: #2c323c;
        }
        QPushButton:checked {
            background-color: #1e252e;
            color: #ffffff;
            border-bottom: 3px solid #0078d4;
        }
        QPushButton:pressed {
            background-color: #0b65b1;
        }
        """
        + extra_style
    )
    nav_layout.addWidget(btn)
    return btn


def get_dark_stylesheet() -> str:
    """Devuelve la cadena de estilo CSS para el tema oscuro."""
    return """
        QMainWindow { background-color: #21252b; }
        QLabel, QGroupBox { color: #abb2bf; }
        h1 { font-size: 20px; color: #56b6c2; }
        h2 { font-size: 16px; color: #e06c75; } /* Título Consola */
        h3 { font-size: 14px; color: #e5c07b; }
        QPushButton {
            background-color: #56b6c2;
            color: #21252b;
            border: none;
            padding: 10px;
            font-weight: bold;
            border-radius: 5px;
        }
        QPushButton:hover { background-color: #61afef; }
        QPushButton:focus { outline: none; }
        QPushButton:disabled {
            background-color: #2b2f36;
            color: #5c6370;
            border: 1px solid #3e4451;
        }
        QWidget#PanelIzquierdo, QWidget#PanelDerechoSuperior, QWidget#PanelDerechoInferior {
            border: 1px solid #3e4451;
            border-radius: 5px;
            padding: 5px;
        }
        QGroupBox {
            margin-top: 5px;
            padding-top: 15px;
            border: 1px solid #3e4451;
            border-radius: 5px;
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
        h1 { font-size: 20px; color: #005a9e; }
        h2 { font-size: 16px; color: #d43f5e; }
        h3 { font-size: 14px; color: #b07d00; }
        QPushButton {
            background-color: #0078d4;
            color: #ffffff;
            border: none;
            padding: 10px;
            font-weight: bold;
            border-radius: 5px;
        }
        QPushButton:hover { background-color: #1185e3; }
        QPushButton:focus { outline: none; }
        QPushButton:disabled {
            background-color: #c8d1dc;
            color: #7a8699;
            border: 1px solid #b0bbc7;
        }
        QWidget#PanelIzquierdo, QWidget#PanelDerechoSuperior, QWidget#PanelDerechoInferior {
            border: 1px solid #d0d7e0;
            border-radius: 5px;
            padding: 5px;
        }
        QGroupBox {
            margin-top: 5px;
            padding-top: 15px;
            border: 1px solid #d0d7e0;
            border-radius: 5px;
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
    "make_nav_button",
    "get_dark_stylesheet",
    "get_light_stylesheet",
]
