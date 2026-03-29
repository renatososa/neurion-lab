import sys
import time
import socket
import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QHBoxLayout, QVBoxLayout, QGridLayout,
    QSizePolicy, QPushButton, QLabel,
    QCheckBox, QGroupBox, QPlainTextEdit,
    QLineEdit, QStackedWidget, QComboBox, QStyle,
    QSlider, QSplitter
)
from PyQt5.QtCore import QTimer, Qt, QDateTime, QSize
from PyQt5.QtGui import QFont, QIcon, QPixmap, QPainter, QColor
from typing import Optional
import pyqtgraph as pg
from gui_constants import (
    PC_UDP_PORT,
    ADS_VREF,
    ADS_GAIN_DEFAULT,
    PACK_BASE_UV,
    WINDOW_MIN_SEC,
    WINDOW_MAX_SEC,
    PLOT_UPDATE_MS,
    NUM_DEVICES,
    CHANNELS_PER_DEVICE,
    NET_BLOCK_SAMPLES_MAX,
    UDP_RECV_BUFSIZE,
)
from gui_styles import (
    CHANNEL_COLORS_BASE,
    apply_pyqtgraph_theme,
    load_icon,
    make_nav_button,
    get_dark_stylesheet,
    get_light_stylesheet,
)
from gui_comm import (
    parse_dump_config,
    deduce_dims_from_payload,
    build_steps_from_gains,
    create_udp_socket,
    start_discovery_socket,
    send_udp_command as gc_send_udp_command,
    send_udp_bytes,
    close_socket,
)
from gui_console import ConsoleManager


# Configurar pyqtgraph con tema por defecto
apply_pyqtgraph_theme()

# pyserial es opcional; si no está disponible, se avisa en la consola
try:
    import serial
    import serial.tools.list_ports
except Exception:
    serial = None

# --- 1. CONFIGURACIÓN DEL TEMA OSCURO Y ESTILO ---

class SignalPlotterGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Visualizador de Senales en Vivo con Consola")
        self.setGeometry(100, 100, 1400, 800) 
        
        # Parámetros de la señal
        self.window_seconds = 10
        self.data_len = 500
        self.samples_per_second = self.data_len / self.window_seconds
        self.data_x = np.linspace(0, self.window_seconds, self.data_len)
        self.num_channels = NUM_DEVICES * CHANNELS_PER_DEVICE
        self.channel_names = [
            f'D{d+1}-CH{c+1}'
            for d in range(NUM_DEVICES)
            for c in range(CHANNELS_PER_DEVICE)
        ]
        self.channel_colors = [
            CHANNEL_COLORS_BASE[i % len(CHANNEL_COLORS_BASE)]
            for i in range(self.num_channels)
        ]
        self.channel_data = np.zeros((self.num_channels, self.data_len))
        self.ptr = 0
        self.device_enabled = [False] * NUM_DEVICES
        self.channel_curves = {}
        self.device_plots = []
        self.device_curves = []
        self.is_running = True # Estado de la simulación
        self.serial_conn = None  # Conexión serial persistente
        self.discovery_socket = None
        self.discovery_timer = QTimer()
        self.discovery_timer.setInterval(200)
        self.discovery_timer.timeout.connect(self.poll_discovery)
        self.discovery_deadline = None
        self.awaiting_config = False
        self.config_deadline = None
        self.device_ip = None
        self.data_socket = None
        self.data_timer = QTimer()
        self.data_timer.setInterval(50)
        self.data_timer.timeout.connect(self.poll_data_packets)
        self.live_data_received = False
        self.window_label = None
        self.last_packet_idx = None
        self.lost_packets = 0
        self.available_devices = 0
        self.battery_label = None
        self._last_batt_update = 0.0
        self.config_device_combo = None
        self.config_rows = []
        self.config_apply_all = {}
        self.config_autoload_done = False
        self.is_dark_theme = True
        self.btn_log_toggle = None
        # Ganancias conocidas desde el dispositivo (no las de la UI)
        self.gains_from_device = [
            [ADS_GAIN_DEFAULT] * CHANNELS_PER_DEVICE for _ in range(NUM_DEVICES)
        ]
        self.console_mgr: Optional[ConsoleManager] = None
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Contenedor principal con splitter horizontal: izquierda (control) y derecha (plots+consola)
        main_layout = QHBoxLayout(central_widget)
        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setHandleWidth(1)
        self.main_splitter.setStyleSheet("QSplitter::handle { background: transparent; }")
        main_layout.addWidget(self.main_splitter)
        
        # --- 2. CONFIGURACIÓN DEL PANEL IZQUIERDO (Panel de Control) ---
        self.panel_izquierdo = QWidget()
        self.panel_izquierdo.setObjectName("PanelIzquierdo")
        self.panel_izquierdo.setMinimumWidth(260)
        self.panel_izquierdo.setMaximumWidth(420)
        self.main_splitter.addWidget(self.panel_izquierdo)
        
        layout_izquierdo = QVBoxLayout(self.panel_izquierdo)

        # Navbar horizontal estilo VS Code (solo iconos)
        nav_widget = QWidget()
        nav_layout = QHBoxLayout(nav_widget)
        nav_layout.setContentsMargins(0, 0, 0, 6)
        nav_layout.setSpacing(0)
        nav_widget.setStyleSheet(
            """
            QWidget {
                border-bottom: 1px solid #2b3038;
            }
            """
        )

        self.nav_conect = make_nav_button(nav_layout, "", True, icon_path="wifi.svg", icon_color="#abb2bf", tooltip="Conectividad")
        self.nav_config  = make_nav_button(nav_layout, "Cfg", False, tooltip="Configuracion")
        self.nav_record   = make_nav_button(nav_layout, "", False, icon_path="record.svg", icon_size=28, icon_color="#abb2bf", tooltip="Grabacion")  # Mantiene colores originales


        layout_izquierdo.addWidget(nav_widget)

        # Contenidos según pestaña
        self.nav_stack = QStackedWidget()
        layout_izquierdo.addWidget(self.nav_stack, 1)

        # Indicador simple de batería
        self.battery_label = QLabel("Batería: -- %")
        self.battery_label.setStyleSheet("color: #abb2bf; font-weight: bold;")
        layout_izquierdo.addWidget(self.battery_label)

        # Pestaña Conectividad: seleccionar puerto y enviar SSID/PASS por serial
        conect_page = QWidget()
        conect_layout = QVBoxLayout(conect_page)

        # Botón de discovery (escucha broadcast del dispositivo)
        self.btn_discover = QPushButton("Buscar dispositivo")
        self.btn_discover.setFocusPolicy(Qt.NoFocus)
        self.btn_discover.setToolTip("Escuchar en el puerto UDP para detectar la IP del dispositivo")
        self.btn_discover.clicked.connect(self.start_discovery_listen)
        conect_layout.addWidget(self.btn_discover)

        # Bloque Serial
        serial_group = QGroupBox("Conexión Serial")
        serial_group.setStyleSheet("QGroupBox { color: #61afef; }")
        serial_grid = QGridLayout(serial_group)
        serial_grid.setHorizontalSpacing(10)
        serial_grid.setVerticalSpacing(8)

        self.serial_available = serial is not None

        serial_grid.addWidget(QLabel("Puerto COM"), 0, 0)
        self.combo_ports = QComboBox()
        self.btn_refresh_ports = QPushButton()
        self.btn_refresh_ports.setFocusPolicy(Qt.NoFocus)
        refresh_h = self.combo_ports.sizeHint().height()
        self.btn_refresh_ports.setFixedSize(QSize(refresh_h, refresh_h))
        icon_size = max(12, refresh_h - 6)
        base_icon = self.style().standardIcon(QStyle.SP_BrowserReload)
        base_pix = base_icon.pixmap(QSize(icon_size, icon_size))
        tinted = QPixmap(base_pix.size())
        tinted.fill(Qt.transparent)
        painter = QPainter(tinted)
        painter.drawPixmap(0, 0, base_pix)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QColor("#abb2bf"))
        painter.end()
        self.btn_refresh_ports.setIcon(QIcon(tinted))
        self.btn_refresh_ports.setIconSize(QSize(icon_size, icon_size))
        self.btn_refresh_ports.setToolTip("Actualizar lista de puertos")
        self.btn_refresh_ports.clicked.connect(self.refresh_serial_ports)
        port_row = QHBoxLayout()
        port_row.addWidget(self.combo_ports, 2)
        port_row.addWidget(self.btn_refresh_ports, 1)
        serial_grid.addLayout(port_row, 0, 1)

        serial_grid.addWidget(QLabel("Baudrate"), 1, 0)
        self.combo_baud = QComboBox()
        for br in ("115200", "230400", "460800", "921600"):
            self.combo_baud.addItem(br)
        self.combo_baud.setCurrentText("230400")
        serial_grid.addWidget(self.combo_baud, 1, 1)

        self.btn_connect_serial = QPushButton("Conectar")
        self.btn_connect_serial.setFocusPolicy(Qt.NoFocus)
        self.btn_connect_serial.setToolTip("Abrir/cerrar puerto serial")
        self.btn_connect_serial.clicked.connect(self.toggle_serial_connection)
        serial_grid.addWidget(self.btn_connect_serial, 2, 0, 1, 2)

        conect_layout.addWidget(serial_group)

        # Bloque WiFi
        wifi_group = QGroupBox("Credenciales WiFi")
        wifi_group.setStyleSheet("QGroupBox { color: #98c379; }")
        wifi_grid = QGridLayout(wifi_group)
        wifi_grid.setHorizontalSpacing(10)
        wifi_grid.setVerticalSpacing(8)

        wifi_grid.addWidget(QLabel("SSID WiFi"), 0, 0)
        self.input_ssid = QLineEdit()
        self.input_ssid.setPlaceholderText("Nombre de la red WiFi")
        wifi_grid.addWidget(self.input_ssid, 0, 1)

        wifi_grid.addWidget(QLabel("Password WiFi"), 1, 0)
        self.input_password = QLineEdit()
        self.input_password.setPlaceholderText("Contraseña")
        self.input_password.setEchoMode(QLineEdit.Password)
        wifi_grid.addWidget(self.input_password, 1, 1)

        self.btn_send_wifi = QPushButton("Enviar SSID/PASS por Serial")
        self.btn_send_wifi.setFocusPolicy(Qt.NoFocus)
        self.btn_send_wifi.clicked.connect(self.send_wifi_credentials)
        wifi_grid.addWidget(self.btn_send_wifi, 2, 0, 1, 2)

        self.btn_ap_mode = QPushButton("Modo AP")
        self.btn_ap_mode.setFocusPolicy(Qt.NoFocus)
        self.btn_ap_mode.clicked.connect(self.send_ap_mode)
        wifi_grid.addWidget(self.btn_ap_mode, 3, 0, 1, 2)

        self.label_serial_status = QLabel("")
        self.label_serial_status.setStyleSheet("color: #98c379;")
        wifi_grid.addWidget(self.label_serial_status, 4, 0, 1, 2)

        conect_layout.addWidget(wifi_group)
        conect_layout.addStretch(1)

        # Pestaña Configuración
        config_page = QWidget()
        config_layout = QVBoxLayout(config_page)
        config_layout.setContentsMargins(6, 6, 6, 6)
        config_layout.setSpacing(8)
        config_layout.setAlignment(Qt.AlignTop)

        config_group = QGroupBox("Configuración ADS")
        config_group.setStyleSheet("QGroupBox { color: #e06c75; }")
        config_group_layout = QVBoxLayout(config_group)
        config_group_layout.setContentsMargins(10, 8, 10, 8)
        config_group_layout.setSpacing(10)

        device_row = QHBoxLayout()
        device_row.setSpacing(6)
        device_row.addWidget(QLabel("Dispositivo:"))
        self.config_device_combo = QComboBox()
        for d in range(NUM_DEVICES):
            self.config_device_combo.addItem(f"ADS {d+1}", d)
        device_row.addWidget(self.config_device_combo)
        device_row.addStretch(1)
        config_group_layout.addLayout(device_row)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        # Hacer columnas equiespaciadas
        for col_idx in range(6):
            grid.setColumnStretch(col_idx, 1)
        headers = ["Canal", "ON/OFF", "Gain", "Test", "Bias+", "Bias-"]
        for col, h in enumerate(headers):
            lbl = QLabel(h)
            align = Qt.AlignCenter if h in ("ON/OFF", "Test") else Qt.AlignLeft
            grid.addWidget(lbl, 0, col, alignment=align)

        # Fila de aplicar a todos
        grid.addWidget(QLabel("Todos"), 1, 0)
        cb_all_on = QCheckBox()
        cb_all_on.setToolTip("Aplicar ON/OFF a todos los canales")
        cb_all_on.setStyleSheet(
            "QCheckBox::indicator { width: 16px; height: 16px; }"
            "QCheckBox::indicator:checked { background-color: #61afef; border: 1px solid #61afef; }"
            "QCheckBox::indicator:unchecked { border: 1px solid #3e4451; }"
        )
        cb_all_on.stateChanged.connect(self.apply_all_on_changed)
        grid.addWidget(cb_all_on, 1, 1, alignment=Qt.AlignCenter)

        combo_all_gain = QComboBox()
        gain_options = [1, 2, 4, 6, 8, 12, 24]
        for g in gain_options:
            combo_all_gain.addItem(str(g), g)
        combo_all_gain.setCurrentIndex(len(gain_options) - 1)
        combo_all_gain.setToolTip("Aplicar ganancia a todos los canales")
        combo_all_gain.currentIndexChanged.connect(self.apply_all_gain_changed)
        grid.addWidget(combo_all_gain, 1, 2)

        cb_all_test = QCheckBox()
        cb_all_test.setToolTip("Aplicar Test a todos los canales")
        cb_all_test.setStyleSheet(
            "QCheckBox::indicator { width: 16px; height: 16px; }"
            "QCheckBox::indicator:checked { background-color: #c678dd; border: 1px solid #c678dd; }"
            "QCheckBox::indicator:unchecked { border: 1px solid #3e4451; }"
        )
        cb_all_test.stateChanged.connect(self.apply_all_test_changed)
        grid.addWidget(cb_all_test, 1, 3, alignment=Qt.AlignCenter)

        cb_all_bias_p = QCheckBox()
        cb_all_bias_p.setToolTip("Seleccionar todos los canales en Bias+")
        cb_all_bias_p.setStyleSheet(
            "QCheckBox::indicator { width: 16px; height: 16px; }"
            "QCheckBox::indicator:checked { background-color: #98c379; border: 1px solid #98c379; }"
            "QCheckBox::indicator:unchecked { border: 1px solid #3e4451; }"
        )
        cb_all_bias_p.stateChanged.connect(self.apply_all_bias_p_changed)
        grid.addWidget(cb_all_bias_p, 1, 4, alignment=Qt.AlignCenter)

        cb_all_bias_n = QCheckBox()
        cb_all_bias_n.setToolTip("Seleccionar todos los canales en Bias-")
        cb_all_bias_n.setStyleSheet(
            "QCheckBox::indicator { width: 16px; height: 16px; }"
            "QCheckBox::indicator:checked { background-color: #e5c07b; border: 1px solid #e5c07b; }"
            "QCheckBox::indicator:unchecked { border: 1px solid #3e4451; }"
        )
        cb_all_bias_n.stateChanged.connect(self.apply_all_bias_n_changed)
        grid.addWidget(cb_all_bias_n, 1, 5, alignment=Qt.AlignCenter)

        self.config_apply_all = {
            "on": cb_all_on,
            "gain": combo_all_gain,
            "test": cb_all_test,
            "bias_p": cb_all_bias_p,
            "bias_n": cb_all_bias_n,
        }

        self.config_rows = []
        for ch in range(CHANNELS_PER_DEVICE):
            row_idx = ch + 2
            grid.addWidget(QLabel(f"CH{ch+1}"), row_idx, 0)

            channel_color = self.channel_colors[ch]
            cb_on = QCheckBox()
            cb_on.setChecked(True)
            cb_on.setStyleSheet(
                f"QCheckBox::indicator {{ width: 16px; height: 16px; }}"
                f"QCheckBox::indicator:checked {{ background-color: {channel_color}; border: 1px solid {channel_color}; }}"
                f"QCheckBox::indicator:unchecked {{ border: 1px solid #3e4451; }}"
            )
            grid.addWidget(cb_on, row_idx, 1, alignment=Qt.AlignCenter)

            combo_gain = QComboBox()
            for g in gain_options:
                combo_gain.addItem(str(g), g)
            combo_gain.setCurrentIndex(len(gain_options) - 1)
            grid.addWidget(combo_gain, row_idx, 2)

            cb_test = QCheckBox()
            cb_test.setChecked(False)
            cb_test.setStyleSheet(
                f"QCheckBox::indicator {{ width: 16px; height: 16px; }}"
                f"QCheckBox::indicator:checked {{ background-color: {channel_color}; border: 1px solid {channel_color}; }}"
                f"QCheckBox::indicator:unchecked {{ border: 1px solid #3e4451; }}"
            )
            grid.addWidget(cb_test, row_idx, 3, alignment=Qt.AlignCenter)

            cb_bias_p = QCheckBox()
            cb_bias_p.setChecked(False)
            cb_bias_p.setStyleSheet(
                "QCheckBox::indicator { width: 16px; height: 16px; }"
                "QCheckBox::indicator:checked { background-color: #98c379; border: 1px solid #98c379; }"
                "QCheckBox::indicator:unchecked { border: 1px solid #3e4451; }"
            )
            grid.addWidget(cb_bias_p, row_idx, 4, alignment=Qt.AlignCenter)

            cb_bias_n = QCheckBox()
            cb_bias_n.setChecked(False)
            cb_bias_n.setStyleSheet(
                "QCheckBox::indicator { width: 16px; height: 16px; }"
                "QCheckBox::indicator:checked { background-color: #e5c07b; border: 1px solid #e5c07b; }"
                "QCheckBox::indicator:unchecked { border: 1px solid #3e4451; }"
            )
            grid.addWidget(cb_bias_n, row_idx, 5, alignment=Qt.AlignCenter)

            self.config_rows.append({
                "on": cb_on,
                "gain": combo_gain,
                "test": cb_test,
                "bias_p": cb_bias_p,
                "bias_n": cb_bias_n,
            })

        config_group_layout.addLayout(grid)
        config_layout.addWidget(config_group)

        actions_group = QGroupBox("Acciones")
        actions_group.setStyleSheet("QGroupBox { color: #56b6c2; }")
        actions_layout = QHBoxLayout(actions_group)
        actions_layout.setContentsMargins(10, 8, 10, 8)
        actions_layout.setSpacing(12)
        # Botones con expansión para quedar equiespaciados
        self.btn_cfg_read = QPushButton("Leer")
        self.btn_cfg_send = QPushButton("Enviar")
        self.btn_theme_toggle = QPushButton("Tema claro")
        self.btn_theme_toggle.setCheckable(True)
        self.btn_theme_toggle.setChecked(False)
        for btn in (self.btn_cfg_read, self.btn_cfg_send, self.btn_theme_toggle):
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.btn_cfg_read.clicked.connect(self.config_read_from_device)
        self.btn_cfg_send.clicked.connect(self.config_send_to_device)
        self.btn_theme_toggle.clicked.connect(self.toggle_theme)
        actions_layout.addStretch(1)
        actions_layout.addWidget(self.btn_cfg_read)
        actions_layout.addWidget(self.btn_cfg_send)
        actions_layout.addWidget(self.btn_theme_toggle)
        actions_layout.addStretch(1)
        actions_layout.addStretch(1)
        config_layout.addWidget(actions_group)
        config_layout.addStretch(1)

        # Pestaña Record (contenido actual)
        record_page = QWidget()
        record_layout = QVBoxLayout(record_page)

        control_group = QGroupBox("Control de Adquisición")
        control_group.setStyleSheet("QGroupBox { color: #56b6c2; }")
        control_layout = QGridLayout(control_group)

        self.btn_start = QPushButton("▶ Iniciar")
        self.btn_pause = QPushButton("▮▮ Pausar")
        self.btn_log_toggle = QPushButton("Grabar")
        self.btn_log_toggle.setCheckable(True)
        self.btn_start.setToolTip("Iniciar adquisición")
        self.btn_pause.setToolTip("Pausar adquisición")
        self.btn_log_toggle.setToolTip("Inicia o detiene el guardado en CSV")
        self.btn_start.setAutoDefault(False)
        self.btn_start.setDefault(False)
        self.btn_pause.setAutoDefault(False)
        self.btn_pause.setDefault(False)
        self.btn_log_toggle.setAutoDefault(False)
        self.btn_log_toggle.setDefault(False)
        self.btn_start.setFocusPolicy(Qt.NoFocus)
        self.btn_pause.setFocusPolicy(Qt.NoFocus)
        self.btn_log_toggle.setFocusPolicy(Qt.NoFocus)
        # Estado inicial: en pausa, con iniciar habilitado
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)

        self.btn_start.clicked.connect(self.start_signal)
        self.btn_pause.clicked.connect(self.pause_signal)
        self.btn_log_toggle.toggled.connect(self.toggle_logging)

        control_layout.addWidget(self.btn_start, 0, 0)
        control_layout.addWidget(self.btn_pause, 0, 1)
        control_layout.addWidget(self.btn_log_toggle, 0, 2)

        record_layout.addWidget(control_group)

        channel_group = QGroupBox("Selección de Canales (por dispositivo)")
        channel_group.setStyleSheet("QGroupBox { color: #c678dd; }")
        channel_layout = QVBoxLayout(channel_group)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)

        grid.addWidget(QLabel("Canal"), 0, 0)
        self.device_checks = []
        for d in range(NUM_DEVICES):
            cb_dev = QCheckBox(f"{d+1}")
            cb_dev.setStyleSheet("color: #abb2bf; font-weight: 700;")
            cb_dev.setChecked(False)
            cb_dev.setProperty("device_id", d)
            cb_dev.stateChanged.connect(self.toggle_device)
            self.device_checks.append(cb_dev)
            grid.addWidget(cb_dev, 0, d + 1)

        self.channel_checks = {}
        for ch in range(CHANNELS_PER_DEVICE):
            grid.addWidget(QLabel(f"CH{ch+1}"), ch + 1, 0)
            for d in range(NUM_DEVICES):
                idx = d * CHANNELS_PER_DEVICE + ch
                cb = QCheckBox()
                cb.setChecked(False)
                cb.setProperty("device_id", d)
                cb.setProperty("channel_id", ch)
                cb.setProperty("global_index", idx)
                cb.stateChanged.connect(self.toggle_channel)
                cb.setStyleSheet(
                    f"QCheckBox::indicator {{ width: 16px; height: 16px; }}"
                    f"QCheckBox::indicator:checked {{ background-color: {self.channel_colors[idx]}; border: 1px solid {self.channel_colors[idx]}; }}"
                    f"QCheckBox::indicator:unchecked {{ border: 1px solid #3e4451; }}"
                )
                self.channel_checks[(d, ch)] = cb
                grid.addWidget(cb, ch + 1, d + 1)

        channel_layout.addLayout(grid)
        record_layout.addWidget(channel_group)

        # Slicer de ventana temporal
        window_group = QGroupBox("Ventana de gráfica (s)")
        window_group.setStyleSheet("QGroupBox { color: #e5c07b; }")
        window_layout = QVBoxLayout(window_group)
        self.window_label = QLabel(f"{int(self.window_seconds)} s")
        self.window_slider = QSlider(Qt.Horizontal)
        self.window_slider.setMinimum(WINDOW_MIN_SEC)
        self.window_slider.setMaximum(WINDOW_MAX_SEC)
        self.window_slider.setValue(int(self.window_seconds))
        self.window_slider.setTickInterval(1)
        self.window_slider.setTickPosition(QSlider.TicksBelow)
        self.window_slider.valueChanged.connect(self.handle_window_change)
        window_layout.addWidget(self.window_label)
        window_layout.addWidget(self.window_slider)
        record_layout.addWidget(window_group)
        record_layout.addStretch(1)

        self.nav_stack.addWidget(conect_page)  # 0
        self.nav_stack.addWidget(config_page)  # 1
        self.nav_stack.addWidget(record_page)  # 2
        # Mostrar la pestaña de Conectividad al iniciar
        self.nav_stack.setCurrentIndex(0)

        self.nav_conect.toggled.connect(lambda checked: checked and self.nav_stack.setCurrentIndex(0))
        self.nav_config.toggled.connect(lambda checked: checked and self.nav_stack.setCurrentIndex(1))
        self.nav_record.toggled.connect(lambda checked: checked and self.nav_stack.setCurrentIndex(2))

        # Poblar la lista de puertos disponibles al iniciar
        self.refresh_serial_ports()
        
        # --- 3. CONFIGURACIÓN DEL PANEL DERECHO (Plot y Consola) ---
        # Panel derecho alojado en el splitter principal
        self.panel_derecho = QWidget()
        self.main_splitter.addWidget(self.panel_derecho)
        layout_derecho = QVBoxLayout(self.panel_derecho)

        # Splitter vertical interno: plots arriba, consola abajo (ajustable por el usuario)
        self.right_splitter = QSplitter(Qt.Vertical)
        self.right_splitter.setHandleWidth(1)
        self.right_splitter.setStyleSheet("QSplitter::handle { background: transparent; }")
        layout_derecho.addWidget(self.right_splitter)

        # 3a. SUBPANEL DERECHO SUPERIOR (plots por dispositivo)
        self.plots_container = QWidget()
        self.plots_container.setObjectName("PanelDerechoSuperior")
        self.plots_layout = QGridLayout(self.plots_container)
        self.plots_layout.setSpacing(10)
        self.plots_layout.setColumnStretch(0, 1)  # una columna que ocupa todo el ancho
        self.right_splitter.addWidget(self.plots_container)
        self.create_plots()
        self.update_plot_layout()
        # Deshabilitar plots y checks hasta recibir snapshot con ADS inicializados
        self.apply_device_availability(0)

        # 3b. SUBPANEL DERECHO INFERIOR: CONSOLA DE COMANDOS
        self.console_container = QWidget()
        self.console_container.setObjectName("PanelDerechoInferior")
        console_layout = QVBoxLayout(self.console_container)
        console_layout.setContentsMargins(5, 5, 5, 5) # Margen interno
        
        console_layout.addWidget(QLabel("<h2>Consola de Comandos</h2>"))
        
        # Área de Historial/Salida
        self.console_output = QPlainTextEdit()
        self.console_output.setReadOnly(True)
        self.console_output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.console_output.setStyleSheet("background-color: #1e1e1e; color: #98c379; border: 1px solid #3e4451; font-family: 'Consolas', 'Courier New';")
        console_layout.addWidget(self.console_output)
        
        # Entrada de Comando
        self.console_input = QLineEdit()
        self.console_input.setPlaceholderText("Escriba su comando aqui y presione Enter...")
        self.console_input.setStyleSheet("background-color: #282c34; color: #abb2bf; border: 1px solid #56b6c2; padding: 5px; border-radius: 3px;")
        
        # Conexión: Cuando se presiona Enter, se llama a handle_command
        self.console_input.returnPressed.connect(self.handle_command) 
        
        console_layout.addWidget(self.console_input)
        self.right_splitter.addWidget(self.console_container)
        self.right_splitter.setStretchFactor(0, 3)
        self.right_splitter.setStretchFactor(1, 1)

        # Ajustar proporciones iniciales del splitter principal (izquierda/derecha)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)

        # --- 4. CONFIGURACIÓN DEL QTimer ---
        self.timer = QTimer()
        self.timer.setInterval(PLOT_UPDATE_MS) 
        self.timer.timeout.connect(self.update_plot)
        # No iniciar automáticamente; esperar a que se presione Iniciar
        
        # Inicializar gestor de consola y logging (usa los widgets ya creados)
        self.console_mgr = ConsoleManager(self.console_output, self.console_input, self.btn_log_toggle)
        self.append_to_console("Sistema: Consola iniciada. Escriba 'help' para ver comandos.")
        self.log_message("Sistema: Inicializacion de la GUI completa.")
        self.apply_theme(True)

        # --- Deteccion de puertos ---

    def _is_likely_esp_port(self, port_info) -> bool:
        """Heuristica sencilla por VID/PID/descripcion para identificar el ESP32."""
        try:
            vid = port_info.vid
            pid = port_info.pid
            manuf = (port_info.manufacturer or "").lower()
            desc = (port_info.description or "").lower()
            hwid = (port_info.hwid or "").lower()
        except Exception:
            return False

        known_vid_pid = {
            (0x303a, None),      # Espressif
            (0x10c4, 0xea60),    # CP210x
            (0x1a86, 0x7523),    # CH340/CH341
            (0x1a86, 0x55d4),    # CH9102
            (0x0403, 0x6001),    # FTDI FT232
            (0x2341, None),      # Algunos clones/variantes
        }
        for v, p in known_vid_pid:
            if vid == v and (p is None or pid == p):
                return True

        needles = ("esp32", "usb jtag", "cp210", "silicon labs", "ch340", "ch341", "wch", "ftdi", "usb serial")
        return any(n in manuf for n in needles) or any(n in desc for n in needles) or any(n in hwid for n in needles)

    # --- Acciones masivas en Configuracion ---

    def apply_all_on_changed(self, state):
        """Aplica ON/OFF a todos los canales en la tabla de configuracion."""
        enabled = state == Qt.Checked
        for row in self.config_rows:
            cb = row["on"]
            cb.blockSignals(True)
            cb.setChecked(enabled)
            cb.blockSignals(False)

    def apply_all_gain_changed(self, index: int):
        """Aplica la ganancia seleccionada a todos los canales."""
        combo_all = self.config_apply_all.get("gain")
        if not combo_all:
            return
        gain = combo_all.itemData(index)
        for row in self.config_rows:
            combo = row["gain"]
            combo.blockSignals(True)
            idx = combo.findData(gain)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def apply_all_test_changed(self, state):
        """Aplica el flag de test a todos los canales."""
        enabled = state == Qt.Checked
        for row in self.config_rows:
            cb = row["test"]
            cb.blockSignals(True)
            cb.setChecked(enabled)
            cb.blockSignals(False)

    def apply_all_bias_p_changed(self, state):
        """Aplica selección Bias+ a todos los canales."""
        enabled = state == Qt.Checked
        for row in self.config_rows:
            cb = row["bias_p"]
            cb.blockSignals(True)
            cb.setChecked(enabled)
            cb.blockSignals(False)

    def apply_all_bias_n_changed(self, state):
        """Aplica selección Bias- a todos los canales."""
        enabled = state == Qt.Checked
        for row in self.config_rows:
            cb = row["bias_n"]
            cb.blockSignals(True)
            cb.setChecked(enabled)
            cb.blockSignals(False)

    def _device_gains_snapshot(self, devices: int) -> list:
        """Devuelve la ganancia por canal segAon Aoltima lectura desde el dispositivo."""
        gains = []
        for d in range(max(1, devices)):
            if d < len(self.gains_from_device):
                gains.extend(self.gains_from_device[d][:CHANNELS_PER_DEVICE])
            else:
                gains.extend([ADS_GAIN_DEFAULT] * CHANNELS_PER_DEVICE)
        return gains

    # --- Temas (oscuro/claro) ---

    def apply_theme(self, is_dark: bool):
        """Aplica el tema oscuro o claro y actualiza el estado del toggle."""
        self.is_dark_theme = bool(is_dark)
        stylesheet = get_dark_stylesheet() if self.is_dark_theme else get_light_stylesheet()
        self.setStyleSheet(stylesheet)
        if self.btn_theme_toggle:
            self.btn_theme_toggle.blockSignals(True)
            self.btn_theme_toggle.setChecked(not self.is_dark_theme)
            self.btn_theme_toggle.setText("Tema claro" if self.is_dark_theme else "Tema oscuro")
            self.btn_theme_toggle.blockSignals(False)

    def toggle_theme(self):
        """Alterna entre tema oscuro y claro."""
        self.apply_theme(not self.is_dark_theme)

    # --- Métodos de Consola ---

    def log_message(self, message):
        """Añade un mensaje al historial de la consola (con timestamp)."""
        if self.console_mgr:
            self.console_mgr.log_message(message)

    def append_to_console(self, text, is_command=False):
        """Añade texto formateado al historial de la consola."""
        if self.console_mgr:
            self.console_mgr.append_to_console(text, is_command=is_command)


    def send_udp_command(self, command: str, timeout: float = 1.0, bufsize: int = 1024, log_response: bool = True):
        """Envía un comando por UDP usando los helpers compartidos y devuelve la respuesta (si llega)."""
        if not command:
            return None
        if not self.device_ip:
            self.append_to_console("IP del dispositivo desconocida. Usa 'Buscar dispositivo' antes de enviar comandos UDP.")
            return None
        try:
            resp = gc_send_udp_command(self.device_ip, command, PC_UDP_PORT, timeout, bufsize)
        except Exception as exc:
            self.append_to_console(f"No se pudo enviar comando UDP: {exc}")
            return None
        if resp is None and log_response:
            self.append_to_console("Sin respuesta UDP (timeout).")
            return None
        if log_response and resp:
            self.append_to_console(f"Respuesta UDP: {resp}")
        return resp

    def keyPressEvent(self, event):
        """Navegación de historial con flechas arriba/abajo en la consola."""
        if self.console_mgr and self.console_mgr.handle_history_key(event):
            return
        super().keyPressEvent(event)

    def handle_command(self):
        """Procesa el comando ingresado por el usuario en la consola."""
        raw_command = self.console_input.text().strip()
        if not raw_command:
            return

        self.append_to_console(raw_command, is_command=True)
        self.console_input.clear()
        # Guardar en historial
        if self.console_mgr:
            self.console_mgr.record_command(raw_command)

        # Enviar por UDP al dispositivo (si tenemos IP detectada)
        self.send_udp_command(raw_command)

        command = raw_command.lower()

        # Simulación de comandos
        if command == "help":
            response = (
                "Comandos de la GUI (locales):\n"
                " - help: Muestra esta ayuda.\n"
                " - status: Muestra estado de la simulación/plots.\n"
                " - start/pause: Controla la simulación de señal local.\n"
                " - clear: Limpia la consola.\n"
                " - ch_data [canal]: Muestra un dato del canal (e.g., ch_data 3).\n"
                " - logstart [archivo]: Comienza a guardar muestras UDP en CSV.\n"
                " - logstop: Detiene el guardado de muestras.\n"
                "\n"
                "Comandos enviados por UDP al dispositivo (mismos que Serial):\n"
                " - START / STOP / CONNECTIVITY / DISCOVERY_REPLY <ipPC>\n"
                " - p/i/c/t/x (cambian estado: PC/IDLE/CAL/TEST/ERROR)\n"
                " - CH <dev> <ch> <gain> <pd> <test>\n"
                " - BIAS <dev> <sensP> <sensN>\n"
                " - BIASDRV <dev> <en> [refInt] (1=driver ON, refInt=1 usa ref interna en CONFIG3)\n"
                "   o BIASDRV <dev> 0xHH (escribe CONFIG3 raw en hex)\n"
                " - TEST <dev> <enable> <amp> <freq>\n"
                " - SAVE / LOAD / DUMP / APMode\n"
                " - T <plotEnable> <dev> <channel|all>\n"
                " - HPF <0/1> (filtro pasa-altos 0.2 Hz)\n"
                " - LPF <0/1> (filtro pasa-bajos 500 Hz)\n"
                " - SET_WIFI <ssid> <password>"
            )
            self.append_to_console(response)
            
        elif command == "status":
            state = "ACTIVA" if self.is_running else "PAUSADA"
            response = f"Estado: {state}. Punto de muestra: {self.ptr}. Canales activos: {sum(c.isVisible() for c in self.channel_curves.values())}/{self.num_channels}."
            self.append_to_console(response)
            
        elif command == "start":
            self.start_signal()
            self.append_to_console("Simulación iniciada/reanudad.")
            
        elif command == "pause":
            self.pause_signal()
            self.append_to_console("Simulación pausada.")

        elif command == "clear":
            if self.console_mgr:
                self.console_mgr.clear_console()
            self.append_to_console("Consola limpia.")
            
        elif command.startswith("ch_data"):
            try:
                # Extraer el número del canal
                parts = command.split()
                channel_index = int(parts[1]) - 1 
                if 0 <= channel_index < self.num_channels:
                    # Mostrar el último valor del canal
                    last_value = self.channel_data[channel_index, -1]
                    response = f"Último valor del Canal {channel_index + 1}: {last_value:.4f}"
                else:
                    response = f"Error: Canal {channel_index + 1} inválido. Use 1 a {self.num_channels}."
                self.append_to_console(response)
            except (IndexError, ValueError):
                self.append_to_console("Error de sintaxis. Uso: ch_data [número_canal].")

        elif command.startswith("logstart"):
            parts = raw_command.split(maxsplit=1)
            path = parts[1] if len(parts) > 1 else None
            self.start_logging(path)

        elif command == "logstop":
            self.stop_logging()

        else:
            # Comandos no manejados localmente igual se envían por UDP (respuesta vendrá del dispositivo).
            self.append_to_console("Comando enviado al dispositivo (ver respuesta UDP).")


    def parse_dump_config(self, dump_text: str):
        """Parsea la salida de DUMP en un dict {dev: {ch: reg}}."""
        return parse_dump_config(dump_text)
    
    def load_config_into_ui(self, dev_idx: int, channel_regs: dict):
        """Actualiza la pesta?a Config con los registros CHnSET recibidos."""
        gain_map = {0: 1, 1: 2, 2: 4, 3: 6, 4: 8, 5: 12, 6: 24}
        for ch, row in enumerate(self.config_rows):
            reg = channel_regs.get(ch)
            if reg is None:
                continue
            pd = (reg >> 7) & 0x01
            gain_code = (reg >> 4) & 0x07
            mux = reg & 0x07
            is_test = mux in (0x02, 0x05)
            gain_val = gain_map.get(gain_code, gain_map[6])
    
            cb_on = row["on"]
            combo_gain = row["gain"]
            cb_test = row["test"]
            cb_on.blockSignals(True)
            combo_gain.blockSignals(True)
            cb_test.blockSignals(True)
            cb_on.setChecked(pd == 0)
            idx = combo_gain.findData(gain_val)
            if idx >= 0:
                combo_gain.setCurrentIndex(idx)
            cb_test.setChecked(is_test)
            cb_on.blockSignals(False)
            combo_gain.blockSignals(False)
            cb_test.blockSignals(False)

        self.append_to_console(f"Configuraci?n cargada para ADS {dev_idx}.")

    def _update_device_gains_from_dump(self, cfg_dump: dict):
        """Actualiza el cache de ganancias segun lo ultimo leido del dispositivo."""
        gain_map = {0: 1, 1: 2, 2: 4, 3: 6, 4: 8, 5: 12, 6: 24}
        for dev_idx, ch_map in cfg_dump.items():
            if dev_idx is None or dev_idx >= NUM_DEVICES:
                continue
            for ch, reg in ch_map.items():
                if ch is None or ch >= CHANNELS_PER_DEVICE:
                    continue
                gain_code = (reg >> 4) & 0x07
                gain_val = gain_map.get(gain_code, ADS_GAIN_DEFAULT)
                self.gains_from_device[dev_idx][ch] = gain_val

    def config_read_from_device(self):
        """Env?a DUMP y llena la tabla de Configuraci?n."""
        if not self.device_ip:
            self.append_to_console("No hay IP del dispositivo. Ejecuta 'Buscar dispositivo'.")
            return
        resp = self.send_udp_command("DUMP", timeout=1.5, bufsize=4096, log_response=False)
        if not resp:
            self.append_to_console("Sin respuesta al comando DUMP.")
            return
        cfg = self.parse_dump_config(resp)
        if not cfg:
            self.append_to_console("No se pudieron interpretar los CHnSET del DUMP.")
            return
        # Actualizar cache de ganancias con lo leido
        self._update_device_gains_from_dump(cfg)
        dev = self.config_device_combo.currentData()
        if dev is None:
            dev = 0
        if dev not in cfg:
            dev = sorted(cfg.keys())[0]
            idx_sel = self.config_device_combo.findData(dev)
            if idx_sel >= 0:
                self.config_device_combo.setCurrentIndex(idx_sel)
        self.load_config_into_ui(dev, cfg.get(dev, {}))
    
    def config_send_to_device(self):
        """Envía BIAS (P/N) y comandos CH por canal según la pestaña Config."""
        if not self.device_ip:
            self.append_to_console("No hay IP del dispositivo. Ejecuta 'Buscar dispositivo'.")
            return
        dev = self.config_device_combo.currentData()
        if dev is None:
            dev = 0
        # BIAS: construir máscaras P/N a partir de checkboxes (bit=1 canal incluido). Si ninguna, se desactiva.
        bias_mask_p = 0
        bias_mask_n = 0
        for ch_idx, row in enumerate(self.config_rows):
            if row["bias_p"].isChecked():
                bias_mask_p |= (1 << ch_idx)
            if row["bias_n"].isChecked():
                bias_mask_n |= (1 << ch_idx)
        bias_cmd = f"BIAS {dev} {bias_mask_p} {bias_mask_n}"
        bias_resp = self.send_udp_command(bias_cmd, timeout=1.5, bufsize=512, log_response=False)
        if bias_resp:
            self.append_to_console(f"BIAS -> {bias_resp}")
        else:
            self.append_to_console("BIAS enviado (sin respuesta)")

        sent = 0
        for ch_idx, row in enumerate(self.config_rows):
            on = row["on"].isChecked()
            gain = row["gain"].currentData()
            if gain is None:
                try:
                    gain = int(row["gain"].currentText())
                except Exception:
                    gain = 24
            test_flag = 1 if row["test"].isChecked() else 0
            pd_flag = 0 if on else 1
            cmd = f"CH {dev} {ch_idx} {gain} {pd_flag} {test_flag}"
            resp = self.send_udp_command(cmd, timeout=1.5, bufsize=512, log_response=False)
            sent += 1
            if resp:
                self.append_to_console(f"CH{ch_idx + 1} (idx {ch_idx}) -> {resp}")
        self.append_to_console(f"Configuraci?n enviada a ADS {dev} ({sent} comandos CH).")
        # Re-leer para confirmar y actualizar las ganancias usadas en el escalado
        try:
            self.config_read_from_device()
        except Exception as exc:
            self.append_to_console(f"No se pudo refrescar configuraci?n tras enviar CH: {exc}")
    
    # --- Métodos de Conectividad Serial ---

    def refresh_serial_ports(self):
        """Lista puertos COM disponibles y habilita/deshabilita controles."""
        if not self.serial_available:
            self.combo_ports.clear()
            self.combo_ports.addItem("pyserial no disponible")
            self.combo_ports.setEnabled(False)
            self.btn_send_wifi.setEnabled(False)
            self.btn_connect_serial.setEnabled(False)
            self.label_serial_status.setText("Instala pyserial para usar la conexión serial.")
            return

        try:
            ports = list(serial.tools.list_ports.comports())
        except Exception as exc:
            self.combo_ports.clear()
            self.combo_ports.addItem("Error listando puertos")
            self.combo_ports.setEnabled(False)
            self.btn_send_wifi.setEnabled(False)
            self.btn_connect_serial.setEnabled(False)
            self.label_serial_status.setText(f"Error: {exc}")
            return

        self.combo_ports.clear()
        if not ports:
            self.combo_ports.addItem("Sin puertos detectados")
            self.combo_ports.setEnabled(False)
            self.btn_send_wifi.setEnabled(False)
            self.btn_connect_serial.setEnabled(False)
            self.label_serial_status.setText("Conecta el dispositivo y pulsa Actualizar.")
            return

        likely_idx = None
        for p in ports:
            is_esp = self._is_likely_esp_port(p)
            label = f"{p.device} ({p.description})"
            if is_esp:
                label += "  [ESP32?]"
                if likely_idx is None:
                    likely_idx = self.combo_ports.count()
            self.combo_ports.addItem(label, p.device)
        self.combo_ports.setEnabled(True)
        self.btn_send_wifi.setEnabled(True)
        self.btn_connect_serial.setEnabled(True)
        if likely_idx is not None:
            self.combo_ports.setCurrentIndex(likely_idx)
            self.label_serial_status.setText("Puerto sugerido: posible ESP32")
        else:
            self.label_serial_status.setText("")

    def toggle_serial_connection(self):
        """Abre o cierra la conexión serial persistente."""
        if not self.serial_available:
            self.append_to_console("pyserial no está instalado; no se puede abrir el puerto.")
            return

        # Si ya está abierta, cerrar
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
                self.append_to_console("Puerto serial cerrado.")
            except Exception as exc:
                self.append_to_console(f"Error al cerrar puerto: {exc}")
            self.serial_conn = None
            self.btn_connect_serial.setText("Conectar")
            self.combo_ports.setEnabled(True)
            self.combo_baud.setEnabled(True)
            self.btn_refresh_ports.setEnabled(True)
            self.label_serial_status.setText("Desconectado")
            return

        # Abrir conexión
        port = self.combo_ports.currentData()
        if not port or " " in str(port):
            port = self.combo_ports.currentText().split()[0]
        try:
            baud = int(self.combo_baud.currentText())
        except ValueError:
            baud = 230400

        if not port or port.lower().startswith("sin puerto"):
            self.append_to_console("Selecciona un puerto COM válido.")
            return

        try:
            ser = serial.Serial(
                port,
                baudrate=baud,
                timeout=0.5,
                write_timeout=1,
                rtscts=False,
                dsrdtr=False,
            )
            try:
                ser.dtr = False
                ser.rts = False
            except Exception:
                pass
            ser.reset_input_buffer()
            self.serial_conn = ser
            self.btn_connect_serial.setText("Desconectar")
            self.combo_ports.setEnabled(False)
            self.combo_baud.setEnabled(False)
            self.btn_refresh_ports.setEnabled(False)
            self.label_serial_status.setText(f"Conectado a {port} @ {baud}")
            self.append_to_console(f"Conectado a {port} @ {baud}")
        except Exception as exc:
            self.serial_conn = None
            self.append_to_console(f"Error al conectar: {exc}")
            self.label_serial_status.setText(f"Error: {exc}")

    def send_wifi_credentials(self):
        """Envía SSID y password por el puerto serial seleccionado."""
        if not self.serial_available:
            self.append_to_console("pyserial no está instalado; no se puede abrir el puerto.")
            return
        if not self.serial_conn or not self.serial_conn.is_open:
            self.append_to_console("Conecta primero el puerto serial.")
            return

        ssid = self.input_ssid.text().strip()
        pwd = self.input_password.text().strip()

        if not ssid or not pwd:
            self.append_to_console("Completa SSID y password antes de enviar.")
            return

        try:
            ser = self.serial_conn
            ser.reset_input_buffer()
            payload = f"SET_WIFI {ssid} {pwd}\n".encode("utf-8", errors="ignore")
            ser.write(payload)
            ser.flush()

            resp_lines = []
            deadline = time.monotonic() + 10.0  # margen de espera para respuesta
            while time.monotonic() < deadline:
                line = ser.readline()
                if not line:
                    continue
                txt = line.decode("utf-8", errors="ignore").strip()
                if txt:
                    resp_lines.append(txt)
            resp = " | ".join(resp_lines).strip()
            if resp:
                self.append_to_console(f"Respuesta del dispositivo: {resp}")
                self.label_serial_status.setText(resp)
            else:
                self.append_to_console(f"Enviado WiFi: {ssid} (sin respuesta)")
                self.label_serial_status.setText("Enviado (sin respuesta)")
        except Exception as exc:
            self.append_to_console(f"Error serial: {exc}")
            self.label_serial_status.setText(f"Error: {exc}")

    def send_ap_mode(self):
        """Envía comando para modo AP por el puerto serial."""
        if not self.serial_available:
            self.append_to_console("pyserial no está instalado; no se puede abrir el puerto.")
            return
        if not self.serial_conn or not self.serial_conn.is_open:
            self.append_to_console("Conecta primero el puerto serial.")
            return
        try:
            ser = self.serial_conn
            ser.reset_input_buffer()
            ser.write(b"APMode\n")
            ser.flush()

            resp_lines = []
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                line = ser.readline()
                if not line:
                    continue
                txt = line.decode("utf-8", errors="ignore").strip()
                if txt:
                    resp_lines.append(txt)
            resp = " | ".join(resp_lines).strip()
            if resp:
                self.append_to_console(f"Respuesta del dispositivo: {resp}")
                self.label_serial_status.setText(resp)
            else:
                self.append_to_console("Comando APMode enviado (sin respuesta)")
                self.label_serial_status.setText("APMode enviado (sin respuesta)")
        except Exception as exc:
            self.append_to_console(f"Error serial: {exc}")
            self.label_serial_status.setText(f"Error: {exc}")

    # --- Lectura de datos UDP (streaming) ---
    def start_data_listener(self):
        """Abre socket UDP para recibir paquetes de datos."""
        if self.data_socket:
            return
        # Evitar conflicto de puerto con el socket de discovery
        if self.discovery_socket:
            close_socket(self.discovery_socket)
            self.discovery_socket = None
            self.discovery_timer.stop()
        try:
            self.data_socket = create_udp_socket(PC_UDP_PORT, blocking=False)
            self.data_timer.start()
            self.append_to_console(f"Escuchando datos UDP en puerto {PC_UDP_PORT}")
            self.config_autoload_done = False
        except Exception as exc:
            self.append_to_console(f"No se pudo abrir socket de datos: {exc}")
            self.data_socket = None

    def handle_window_change(self, value: int):
        """Ajusta la ventana temporal del plot (en segundos)."""
        seconds = max(WINDOW_MIN_SEC, min(WINDOW_MAX_SEC, int(value)))
        if seconds == int(self.window_seconds):
            self.window_label.setText(f"{seconds} s")
            return
        self.window_seconds = seconds
        self.window_label.setText(f"{seconds} s")
        self.recompute_buffers()

    def set_sampling_rate(self, fs_hz: float):
        """Actualiza la tasa de muestreo estimada y reescala buffers."""
        if fs_hz <= 0:
            return
        self.samples_per_second = fs_hz
        self.recompute_buffers()

    def recompute_buffers(self):
        """Recalcula longitud de buffers y eje X conservando datos recientes."""
        new_len = max(int(self.window_seconds * self.samples_per_second), 10)
        old_data = self.channel_data
        new_data = np.zeros((self.num_channels, new_len))
        copy_len = min(old_data.shape[1], new_len)
        if copy_len > 0:
            new_data[:, -copy_len:] = old_data[:, -copy_len:]
        self.data_len = new_len
        self.data_x = np.linspace(0, self.window_seconds, self.data_len)
        self.channel_data = new_data

    def update_battery_indicator(self, batt_byte: int):
        """Actualiza el indicador de batería a partir del byte 0..255 recibido."""
        if not self.battery_label:
            return
        now = time.monotonic()
        if now - self._last_batt_update < 1.0:
            return
        self._last_batt_update = now
        try:
            # El dispositivo envía 0..100 directamente como porcentaje
            percent = max(0, min(100, int(batt_byte)))
        except Exception:
            percent = 0
        blocks = int(percent / 10)
        bar = "[" + "#" * blocks + "-" * (10 - blocks) + "]"
        self.battery_label.setText(f"Batería: {percent:3d}% {bar}")
        for i in range(self.num_channels):
            curve = self.channel_curves.get(i)
            if curve:
                curve.setData(self.data_x, self.channel_data[i])

    def stop_data_listener(self):
        if self.data_socket:
            close_socket(self.data_socket)
            self.data_socket = None
        self.data_timer.stop()

    def poll_data_packets(self):
        if not self.data_socket:
            self.data_timer.stop()
            return
        try:
            while True:
                try:
                    data, addr = self.data_socket.recvfrom(UDP_RECV_BUFSIZE)
                except BlockingIOError:
                    break
                if len(data) < 4:
                    continue
                # Filtrar posibles respuestas de texto (OK/ERR) u otros mensajes no-binarios
                if data[0] >= 65 and data[0] <= 122 and b" " in data[:8]:
                    try:
                        msg = data.decode("utf-8", errors="ignore").strip()
                        if msg:
                            self.append_to_console(f"UDP texto: {msg}")
                        continue
                    except Exception:
                        pass
                num_dev_hdr = data[0]
                count_per_hdr = data[1]
                pkt_idx = data[2]
                batt = data[3]
                payload_len = len(data) - 4
                self.update_battery_indicator(batt)

                num_dev = num_dev_hdr
                count_per = count_per_hdr
                expected_payload = num_dev * count_per * CHANNELS_PER_DEVICE * 2
                if num_dev == 0 or count_per == 0 or payload_len != expected_payload:
                    dims = deduce_dims_from_payload(
                        payload_len, max_devices=NUM_DEVICES, channels_per_device=CHANNELS_PER_DEVICE, bytes_per_sample=2
                    )
                    if not dims:
                        continue
                    num_dev, count_per = dims
                payload = data[4:4 + payload_len]
                gains_snapshot = self._device_gains_snapshot(num_dev)
                steps_uv, default_step = build_steps_from_gains(
                    num_dev, gains_snapshot, ADS_GAIN_DEFAULT, PACK_BASE_UV, CHANNELS_PER_DEVICE
                )
                # Ajustar plots disponibles si cambia el número de ADS activos
                if num_dev != self.available_devices:
                    self.apply_device_availability(num_dev)
                # Chequeo de pérdida de paquetes (contador mod 256)
                first_packet = self.last_packet_idx is None
                if not first_packet:
                    expected_idx = (self.last_packet_idx + 1) % 256
                    if pkt_idx != expected_idx:
                        self.lost_packets += (pkt_idx - expected_idx) % 256
                        self.append_to_console(f"Perdida de paquetes: {self.lost_packets} (idx prev {self.last_packet_idx} -> {pkt_idx})")
                self.last_packet_idx = pkt_idx
                # Parsear todos los dispositivos y canales; datos vienen como int16 con paso dependiente de ganancia
                writer = self.console_mgr.log_writer if self.console_mgr else None
                if writer and self.console_mgr and not self.console_mgr.log_header_written:
                    header_channels = [f"D{d+1}-CH{c+1}" for d in range(num_dev) for c in range(CHANNELS_PER_DEVICE)]
                    writer.writerow(["META", "ads_active", num_dev, "fs_hz", self.samples_per_second])
                    writer.writerow(["GAINS"] + gains_snapshot[: len(header_channels)])
                    writer.writerow(["sample_id"] + header_channels)
                    self.console_mgr.log_header_written = True
                    self.console_mgr.log_sample_id = 0
                offset = 0
                for k in range(count_per):
                    row_values = []
                    for d in range(num_dev):
                        for ch in range(CHANNELS_PER_DEVICE):
                            val_packed = int.from_bytes(payload[offset:offset+2], byteorder="little", signed=True)
                            offset += 2
                            idx = d * CHANNELS_PER_DEVICE + ch
                            step_uv = steps_uv[idx] if idx < len(steps_uv) else default_step
                            uv = float(val_packed) * step_uv
                            self.channel_data[idx] = np.roll(self.channel_data[idx], -1)
                            self.channel_data[idx][-1] = uv
                            if writer and self.console_mgr:
                                row_values.append(uv)
                    if writer and row_values and self.console_mgr:
                        writer.writerow([self.console_mgr.log_sample_id] + row_values)
                        self.console_mgr.log_sample_id += 1
                self.live_data_received = True
                # Log corto solo del primer paquete recibido
                if num_dev > 0 and count_per > 0 and first_packet:
                    first_vals = []
                    base_offset = 0
                    for ch in range(min(3, CHANNELS_PER_DEVICE)):
                        val_packed = int.from_bytes(payload[base_offset:base_offset+2], byteorder="little", signed=True)
                        step_uv = steps_uv[ch] if ch < len(steps_uv) else default_step
                        uv = float(val_packed) * step_uv
                        first_vals.append(round(uv, 3))
                        base_offset += 2
                    self.append_to_console(f"Datos UDP idx={pkt_idx} devs={num_dev} count={count_per} batt={batt} ch0..2 (uV)={first_vals}")
                if not self.config_autoload_done:
                    self.config_autoload_done = True
                    try:
                        self.config_read_from_device()
                    except Exception as exc:
                        self.append_to_console(f"No se pudo auto-leer configuraciA3n: {exc}")
        except Exception as exc:
            self.append_to_console(f"Error leyendo datos UDP: {exc}")
            self.stop_data_listener()

    # --- Discovery UDP ---

    def start_discovery_listen(self):
        """Abre un socket UDP en PC_UDP_PORT y escucha anuncios del dispositivo."""
        # Si ya está escuchando, reiniciar buffers
        if self.discovery_socket:
            close_socket(self.discovery_socket)
            self.discovery_socket = None
        # Liberar el puerto si estaba usándose para datos
        if self.data_socket:
            close_socket(self.data_socket)
            self.data_socket = None
            self.data_timer.stop()
        try:
            self.discovery_socket = start_discovery_socket(PC_UDP_PORT)
            self.discovery_timer.start()
            self.discovery_deadline = time.monotonic() + 10.0
            self.append_to_console(f"Buscando dispositivo en UDP {PC_UDP_PORT} ...")
            self.label_serial_status.setText(f"Escuchando UDP {PC_UDP_PORT}")
            self.config_autoload_done = False
        except Exception as exc:
            self.discovery_socket = None
            self.append_to_console(f"No se pudo abrir UDP {PC_UDP_PORT}: {exc}")
            self.label_serial_status.setText(f"Error UDP: {exc}")

    def poll_discovery(self):
        """Lee mensajes entrantes y muestra IP/reportes."""
        if not self.discovery_socket:
            self.discovery_timer.stop()
            return
        try:
            while True:
                try:
                    # Si el socket se cerró durante el bucle, salir
                    if not self.discovery_socket:
                        break
                    data, addr = self.discovery_socket.recvfrom(1024)
                except BlockingIOError:
                    break
                ip = addr[0]
                msg = data.decode("utf-8", errors="ignore").strip()
                display = msg if msg else "(sin mensaje)"
                # Imprimir siempre el mensaje recibido (discovery o config)
                # Manejo de mensajes: snapshot vs. genérico
                if msg.startswith("CFG"):
                    # Mostrar snapshot con formato
                    self.append_to_console(f"Snapshot de configuración desde {ip}:")
                    fs_hz = None
                    num_dev = None
                    for line in msg.splitlines():
                        self.append_to_console(line)
                        parts = line.strip().split()
                        if len(parts) >= 3 and parts[0] == "CFG" and parts[1] == "FS":
                            try:
                                fs_hz = float(parts[2])
                            except ValueError:
                                fs_hz = None
                        if len(parts) >= 3 and parts[0] == "CFG" and parts[1] == "NUM_DEV":
                            try:
                                num_dev = int(parts[2])
                            except ValueError:
                                num_dev = None
                    if fs_hz:
                        self.set_sampling_rate(fs_hz)
                        self.append_to_console(f"FS recibida: {fs_hz} Hz")
                    if num_dev is not None:
                        self.apply_device_availability(num_dev)
                    if self.awaiting_config:
                        self.awaiting_config = False
                        self.stop_discovery()
                        break
                else:
                    # Evitar duplicados exactos consecutivos
                    if not hasattr(self, "_last_udp_msg") or self._last_udp_msg != msg:
                        self.append_to_console(f"UDP desde {ip}: {display}")
                self._last_udp_msg = msg
                self.label_serial_status.setText(f"IP dispositivo: {ip}")
                self.device_ip = ip
                if self.device_ip and not self.config_autoload_done:
                    try:
                        self.config_read_from_device()
                        self.config_autoload_done = True
                    except Exception as exc:
                        self.append_to_console(f"No se pudo leer configuraciA3n tras discovery: {exc}")
                # Si es mensaje DISCOVERY, responder con la IP de la PC
                if msg.startswith("DISCOVERY"):
                    try:
                        # Obtener IP local real usada hacia ese destino
                        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        probe.connect((ip, addr[1]))
                        local_ip = probe.getsockname()[0]
                        probe.close()
                    except Exception:
                        local_ip = ip  # fallback
                    reply = f"DISCOVERY_REPLY {local_ip}".encode("utf-8", errors="ignore")
                    try:
                        self.discovery_socket.sendto(reply, (ip, addr[1]))
                        self.append_to_console(f"Respondido DISCOVERY_REPLY {local_ip} a {ip}:{addr[1]}")
                        self.label_serial_status.setText(f"IP dispositivo: {ip}")
                        # Mantener socket abierto unos segundos para capturar snapshot
                        self.awaiting_config = True
                        self.config_deadline = time.monotonic() + 3.0
                    except Exception as exc:
                        self.append_to_console(f"Error enviando reply: {exc}")
        except Exception as exc:
            self.append_to_console(f"Error leyendo UDP: {exc}")
            self.label_serial_status.setText(f"Error UDP: {exc}")
            self.stop_discovery()
            return
        # Timeout esperando snapshot
        if self.awaiting_config and self.config_deadline and time.monotonic() > self.config_deadline:
            self.awaiting_config = False
            self.stop_discovery()
        if self.discovery_deadline and time.monotonic() > self.discovery_deadline:
            self.append_to_console("No se encontró el dispositivo (timeout).")
            self.label_serial_status.setText("Sin respuesta")
            self.stop_discovery()

    def stop_discovery(self):
        if self.discovery_socket:
            close_socket(self.discovery_socket)
            self.discovery_socket = None
        self.discovery_timer.stop()
        self.discovery_deadline = None
        self.awaiting_config = False
        self.config_deadline = None
    # --- Métodos de Ploteo y Control ---

    def create_plots(self):
        """Crea un PlotWidget por dispositivo y curvas por canal."""
        self.device_plots = []
        self.device_curves = []
        self.channel_curves = {}

        for d in range(NUM_DEVICES):
            plot = pg.PlotWidget(name=f'Device{d+1}')
            plot.setLabel('left', f'Disp {d+1} (µV)')
            plot.setBackground('#1e1e1e')
            plot.showGrid(x=True, y=True, alpha=0.3)
            # Formato de eje Y en notación científica
            axis_y = plot.plotItem.getAxis('left')
            if axis_y:
                axis_y.enableAutoSIPrefix(False)
                axis_y.setLabel(text=f'Disp {d+1} (µV)')
                axis_y.setLogMode(False)
                axis_y.setTickFont(QFont('Consolas', 9))
                # Forzar notación científica en los ticks
                axis_y.tickStrings = lambda values, scale, spacing: [f"{v:.3e}" for v in values]
            if plot.plotItem.legend is None:
                plot.addLegend()

            curves = {}
            for ch in range(CHANNELS_PER_DEVICE):
                idx = d * CHANNELS_PER_DEVICE + ch
                curve = plot.plot(
                    pen={'color': self.channel_colors[idx], 'width': 2},
                    name=self.channel_names[idx]
                )
                curves[idx] = curve
                self.channel_curves[idx] = curve

            self.device_plots.append(plot)
            self.device_curves.append(curves)
            self.plots_layout.addWidget(plot, d // 2, d % 2)

    def update_plot_layout(self):
        """Ajusta la grilla de plots según dispositivos activos."""
        # Limpiar layout actual
        while self.plots_layout.count():
            item = self.plots_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)

        # Reset de estiramientos
        for r in range(NUM_DEVICES):
            self.plots_layout.setRowStretch(r, 0)

        active_indices = [i for i, enabled in enumerate(self.device_enabled) if enabled]
        # Distribuir en grilla por filas (1 columna de ancho completo)
        for n, d in enumerate(active_indices):
            row = n
            col = 0
            plot = self.device_plots[d]
            plot.setVisible(True)
            self.plots_layout.addWidget(plot, row, col)
            self.plots_layout.setRowStretch(row, 1)

        # Ocultar los que no están activos
        for d in range(NUM_DEVICES):
            if d not in active_indices:
                self.device_plots[d].setVisible(False)

    def update_plot(self):
        """Actualiza las gráficas con los datos recibidos (en µV)."""
        for i in range(self.num_channels):
            curve = self.channel_curves.get(i)
            if curve and curve.isVisible():
                curve.setData(self.data_x, self.channel_data[i])

    def set_channel_state(self, device_id: int, channel_id: int, enabled: bool, log_change: bool = True):
        """Actualiza estado de un canal (checkbox + curva) evitando bucles de señales."""
        idx = device_id * CHANNELS_PER_DEVICE + channel_id
        cb = self.channel_checks.get((device_id, channel_id))
        if cb and cb.isChecked() != enabled:
            cb.blockSignals(True)
            cb.setChecked(enabled)
            cb.blockSignals(False)

        curve = self.channel_curves.get(idx)
        if curve:
            curve.setVisible(enabled)

        if log_change:
            self.log_message(f"Disp {device_id + 1} Canal {channel_id + 1}: {'Activado' if enabled else 'Desactivado'}")

    def apply_device_availability(self, num_available: int):
        """Habilita/deshabilita dispositivos/plots según ADS inicializados."""
        self.available_devices = max(0, min(NUM_DEVICES, int(num_available)))
        for d, cb_dev in enumerate(self.device_checks):
            is_avail = d < self.available_devices
            cb_dev.setEnabled(is_avail)
            cb_dev.blockSignals(True)
            cb_dev.setChecked(is_avail)
            cb_dev.blockSignals(False)
            self.device_enabled[d] = is_avail
            for ch in range(CHANNELS_PER_DEVICE):
                cb = self.channel_checks.get((d, ch))
                if cb:
                    cb.setEnabled(is_avail)
                    cb.blockSignals(True)
                    cb.setChecked(is_avail)
                    cb.blockSignals(False)
                    self.set_channel_state(d, ch, is_avail, log_change=False)
        self.update_plot_layout()

    def toggle_device(self, state):
        """Activa/desactiva todos los canales de un dispositivo."""
        checkbox = self.sender()
        if not checkbox:
            return
        device_id = checkbox.property("device_id")
        if device_id is None:
            return
        enabled = (state == Qt.Checked)
        self.device_enabled[device_id] = enabled
        for ch in range(CHANNELS_PER_DEVICE):
            self.set_channel_state(device_id, ch, enabled, log_change=False)
        self.update_plot_layout()
        self.log_message(f"Dispositivo {device_id + 1}: {'Activado' if enabled else 'Desactivado'}")

    def toggle_channel(self, state):
        """Muestra u oculta la curva de un canal según el CheckBox."""
        checkbox = self.sender()
        if not checkbox:
            return
        device_id = checkbox.property("device_id")
        channel_id = checkbox.property("channel_id")
        if device_id is None or channel_id is None:
            return
        enabled = (state == Qt.Checked)
        self.set_channel_state(device_id, channel_id, enabled, log_change=True)


    def start_signal(self):
        """Inicia o reanuda la adquisición de datos."""
        if not self.timer.isActive():
            self.config_autoload_done = False
            # Abrir listener de datos antes de enviar START para que el dispositivo responda al puerto correcto
            self.start_data_listener()
            if self.device_ip:
                sent_ok = False
                # Preferimos enviar START usando el socket de datos ya bindeado al puerto esperado
                if self.data_socket:
                    try:
                        self.data_socket.sendto(b"START", (self.device_ip, PC_UDP_PORT))
                        sent_ok = True
                    except Exception:
                        sent_ok = False
                if not sent_ok:
                    sent_ok = send_udp_bytes(self.device_ip, b"START", PC_UDP_PORT)
                self.append_to_console(f"Comando START {'enviado' if sent_ok else 'no enviado'} a {self.device_ip}")
            else:
                self.append_to_console("IP del dispositivo desconocida. Usa 'Buscar dispositivo' antes de iniciar.")
            self.timer.start()
            self.is_running = True
            self.btn_start.setEnabled(False)
            self.btn_pause.setEnabled(True)
            # Limpiar gráficas antes de arrancar
            for i in range(self.num_channels):
                self.channel_data[i].fill(0)
            self.log_message("Simulación de señal iniciada.")

    def pause_signal(self):
        """Pausa la adquisición de datos."""
        if self.timer.isActive():
            self.timer.stop()
            self.is_running = False
            self.btn_start.setEnabled(True)
            self.btn_pause.setEnabled(False)
            self.log_message("Simulación de señal pausada.")
            # Enviar comando STOP por UDP si conocemos IP
            if self.device_ip:
                sent_ok = False
                if self.data_socket:
                    try:
                        self.data_socket.sendto(b"STOP", (self.device_ip, PC_UDP_PORT))
                        sent_ok = True
                    except Exception:
                        sent_ok = False
                if not sent_ok:
                    sent_ok = send_udp_bytes(self.device_ip, b"STOP", PC_UDP_PORT)
                self.append_to_console(f"Comando STOP {'enviado' if sent_ok else 'no enviado'} a {self.device_ip}")

    def _set_log_toggle_state(self, recording: bool):
        if self.console_mgr:
            self.console_mgr._set_log_toggle_state(recording)

    def toggle_logging(self, checked: bool):
        """Botón toggle para iniciar/detener grabación a CSV."""
        if checked:
            # Preguntar nombre de archivo
            path = None
            try:
                from PyQt5.QtWidgets import QInputDialog
                fname, ok = QInputDialog.getText(self, "Nombre de archivo", "Guardar CSV como:", text=f"udp_log_{int(time.time())}.csv")
                if ok and fname.strip():
                    path = fname.strip()
            except Exception:
                path = None
            self.start_logging(path)
            if not (self.console_mgr and self.console_mgr.log_file):
                self._set_log_toggle_state(False)
        else:
            self.stop_logging()

    # --- Logging de datos UDP ---
    def start_logging(self, path: Optional[str] = None):
        """Abre un archivo CSV para guardar muestras recibidas (uV int16, LSB=24/gain)."""
        if self.console_mgr:
            self.console_mgr.start_logging(path)

    def stop_logging(self):
        if self.console_mgr:
            self.console_mgr.stop_logging()

    def closeEvent(self, event):
        """Cerrar conexiones al salir."""
        # Enviar comando CONNECTIVITY para devolver el dispositivo al estado de búsqueda
        if self.device_ip:
            if send_udp_bytes(self.device_ip, b"CONNECTIVITY", PC_UDP_PORT):
                self.append_to_console(f"Comando CONNECTIVITY enviado a {self.device_ip}")
            else:
                self.append_to_console("No se pudo enviar CONNECTIVITY (UDP).")
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
            except Exception:
                pass
        if self.discovery_socket:
            try:
                self.discovery_socket.close()
            except Exception:
                pass
        self.stop_data_listener()
        self.stop_logging()
        event.accept()


if __name__ == '__main__':
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    
    app = QApplication(sys.argv)
    window = SignalPlotterGUI()
    window.show()
    sys.exit(app.exec_())
