import sys
import time
import socket
import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QHBoxLayout, QVBoxLayout, QGridLayout,
    QSizePolicy, QPushButton, QLabel,
    QCheckBox, QGroupBox, QPlainTextEdit,
    QLineEdit, QStackedWidget, QComboBox, QScrollArea, QToolButton, QListView,
    QSlider, QSplitter, QStylePainter, QStyleOptionComboBox, QStyle
)
from PyQt5.QtCore import QTimer, Qt, QSize, QPoint, QRect
from PyQt5.QtGui import QFont, QPainter, QPalette, QPen, QPolygon, QColor
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
    get_nav_button_stylesheet,
    load_icon,
    make_visibility_icon,
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
    send_udp_command_collect as gc_send_udp_command_collect,
    send_udp_bytes,
    close_socket,
)
from gui_console import ConsoleManager
import re


# Configurar pyqtgraph con tema por defecto
apply_pyqtgraph_theme()

# pyserial es opcional; si no está disponible, se avisa en la consola
# --- 1. CONFIGURACIÓN DEL TEMA OSCURO Y ESTILO ---

class ArrowComboBox(QComboBox):
    """QComboBox con flecha visible incluso bajo estilos globales custom."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setStyleSheet(
            """
            QComboBox {
                padding-left: 8px;
                padding-right: 34px;
            }
            QComboBox::drop-down {
                width: 0px;
                border: none;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0px;
                height: 0px;
            }
            """
        )

    def paintEvent(self, event):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.currentText = ""

        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.CC_ComboBox, option)
        painter.setRenderHint(QPainter.Antialiasing, True)
        base_color = self.palette().color(QPalette.Base)
        text_color = self.palette().color(QPalette.Text)
        border_color = self.palette().color(QPalette.Mid)

        if base_color.lightness() < 128:
            panel_color = base_color.lighter(120)
        else:
            panel_color = base_color.darker(106)

        if not self.isEnabled():
            text_color.setAlpha(120)
            border_color.setAlpha(90)
            panel_color.setAlpha(120)

        panel_rect = QRect(self.width() - 32, 2, 30, max(0, self.height() - 4))
        painter.fillRect(panel_rect, panel_color)

        separator_pen = QPen(border_color)
        separator_pen.setWidth(1)
        painter.setPen(separator_pen)
        painter.drawLine(panel_rect.left(), panel_rect.top() + 2, panel_rect.left(), panel_rect.bottom() - 2)

        text_rect = self.rect().adjusted(8, 0, -36, 0)
        painter.setPen(text_color)
        text = self.fontMetrics().elidedText(self.currentText(), Qt.ElideRight, max(0, text_rect.width()))
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)

        painter.setPen(Qt.NoPen)
        painter.setBrush(text_color)
        center_x = panel_rect.center().x()
        center_y = panel_rect.center().y() + 1
        triangle = QPolygon(
            [
                QPoint(center_x - 5, center_y - 3),
                QPoint(center_x + 5, center_y - 3),
                QPoint(center_x, center_y + 4),
            ]
        )
        painter.drawPolygon(triangle)
        painter.end()


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
        self.display_channel_data = np.zeros((self.num_channels, self.data_len))
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
        self.device_wifi_state = None
        self.device_sta_ip = None
        self.device_ap_ip = None
        self.wifi_scan_results = []
        self.wifi_networks_group = None
        self.wifi_networks_hint = None
        self.wifi_networks_empty = None
        self.wifi_networks_list = None
        self.data_socket = None
        self.data_timer = QTimer()
        self.data_timer.setInterval(50)
        self.data_timer.timeout.connect(self.poll_data_packets)
        self.battery_poll_timer = QTimer()
        self.battery_poll_timer.setInterval(5000)
        self.battery_poll_timer.timeout.connect(self.poll_idle_battery)
        self.live_data_received = False
        self.window_label = None
        self.notch_50hz_enabled = False
        self.gui_raw_guard_enabled = False
        self.gui_visual_guard_enabled = False
        self.visual_spike_threshold_uv = 20000.0
        self.raw_spike_threshold_uv = 20000.0
        self.raw_recovery_threshold_uv = 4000.0
        self.raw_recovery_required = 3
        self.packed_saturation_threshold = 32000
        self.last_packet_idx = None
        self.lost_packets = 0
        self.saturated_samples = 0
        self.raw_spike_rejections = 0
        self.visual_spike_rejections = 0
        self.available_devices = 0
        self.battery_label = None
        self._last_batt_update = 0.0
        self._last_saturation_log = 0.0
        self._last_raw_spike_log = 0.0
        self._last_visual_spike_log = 0.0
        self.config_device_combo = None
        self.config_rows = []
        self.config_apply_all = {}
        self.filter_profile_options = ["ECG", "EOG", "EMG", "EEG"]
        self.basic_signal_defaults = {
            "ECG": 2,
            "EOG": 2,
            "EMG": 4,
            "EEG": 24,
        }
        self.filter_profiles_by_device = [
            ["ECG"] * CHANNELS_PER_DEVICE for _ in range(NUM_DEVICES)
        ]
        self._config_ui_device = 0
        self.config_autoload_done = False
        self.is_dark_theme = False
        self.btn_log_toggle = None
        self.basic_signal_combo = None
        self.basic_signal_summary = None
        self.basic_signal_buttons = {}
        self.basic_selected_profile = None
        self.basic_group = None
        self.advanced_group = None
        self.btn_show_advanced = None
        self.btn_back_basic = None
        self.btn_password_toggle = None
        self._basic_signal_syncing = False
        self.btn_diag_toggle = None
        self.status_conn_value = None
        self.status_device_value = None
        self.status_stream_value = None
        self.status_record_value = None
        self.status_batt_value = None
        self.status_hint_label = None
        self.btn_measure_clear = None
        self.measurement_label = None
        self.measurement_cursor_buttons = {}
        self.measurement_active_plot = None
        self.measurement_active_device = None
        self.measurement_target = None
        self.measurement_markers = {
            "v1": {"value": None, "line": None, "angle": 90},
            "v2": {"value": None, "line": None, "angle": 90},
            "h1": {"value": None, "line": None, "angle": 0},
            "h2": {"value": None, "line": None, "angle": 0},
        }
        self.function_group = None
        self.function_type_combo = None
        self.function_channel_combo = None
        self.btn_function_add = None
        self.function_list_scroll = None
        self.function_list_container = None
        self.function_list_layout = None
        self.function_empty_label = None
        self.function_definitions = [
            {"key": "rms", "label": "RMS"},
            {"key": "gamma", "label": "Gamma (>30 Hz)"},
            {"key": "beta", "label": "Beta (13-30 Hz)"},
            {"key": "alpha", "label": "Alfa (8-13 Hz)"},
            {"key": "theta", "label": "Theta (4-8 Hz)"},
            {"key": "delta", "label": "Delta (<4 Hz)"},
            {"key": "envelope", "label": "Envolvente"},
        ]
        self.active_channel_functions = []
        self.function_curves = {}
        self.function_widgets = {}
        self.function_signal_cache = {}
        self.function_channel_cache = {}
        self.function_fft_cache = {}
        self._next_function_entry_id = 1
        self.function_colors = [
            "#ff6b6b",
            "#00bcd4",
            "#f59e0b",
            "#8b5cf6",
            "#10b981",
            "#ef4444",
            "#3b82f6",
            "#f97316",
            "#14b8a6",
            "#ec4899",
        ]
        self._plot_dirty = True
        self._function_data_dirty = True
        # Ganancias conocidas desde el dispositivo (no las de la UI)
        self.gains_from_device = [
            [ADS_GAIN_DEFAULT] * CHANNELS_PER_DEVICE for _ in range(NUM_DEVICES)
        ]
        self._raw_valid_history = np.zeros((self.num_channels, 3))
        self._raw_valid_count = np.zeros(self.num_channels, dtype=int)
        self._raw_last_valid_uv = np.zeros(self.num_channels)
        self._raw_valid_initialized = np.zeros(self.num_channels, dtype=bool)
        self._raw_hold_active = np.zeros(self.num_channels, dtype=bool)
        self._raw_hold_reference_uv = np.zeros(self.num_channels)
        self._raw_recovery_good_count = np.zeros(self.num_channels, dtype=int)
        self._visual_last_value = np.zeros(self.num_channels)
        self._visual_initialized = np.zeros(self.num_channels, dtype=bool)
        self._notch_x1 = np.zeros(self.num_channels)
        self._notch_x2 = np.zeros(self.num_channels)
        self._notch_y1 = np.zeros(self.num_channels)
        self._notch_y2 = np.zeros(self.num_channels)
        self._notch_coeffs = None
        self._update_notch_coeffs()
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
        layout_izquierdo.setContentsMargins(10, 10, 10, 10)
        layout_izquierdo.setSpacing(10)

        workflow_title = QLabel("Flujo de trabajo")
        workflow_title.setProperty("role", "sectionTitle")
        workflow_hint = QLabel("Conecta el equipo, ajusta parametros y luego adquiere datos en vivo.")
        workflow_hint.setProperty("role", "sectionHint")
        workflow_hint.setWordWrap(True)
        layout_izquierdo.addWidget(workflow_title)
        layout_izquierdo.addWidget(workflow_hint)

        # Navegacion principal del flujo
        nav_widget = QWidget()
        nav_widget.setObjectName("NavBar")
        nav_layout = QHBoxLayout(nav_widget)
        nav_layout.setContentsMargins(0, 0, 0, 6)
        nav_layout.setSpacing(0)

        self.nav_conect = make_nav_button(nav_layout, "1. Conectar", True, icon_path="wifi.svg", icon_color="#abb2bf", tooltip="Conectividad")
        self.nav_config = make_nav_button(nav_layout, "2. Configurar", False, tooltip="Configuracion")
        self.nav_record = make_nav_button(nav_layout, "3. Adquirir", False, icon_path="record.svg", icon_size=22, icon_color="#abb2bf", tooltip="Grabacion")
        self.nav_buttons = [self.nav_conect, self.nav_config, self.nav_record]


        layout_izquierdo.addWidget(nav_widget)

        # Contenidos según pestaña
        self.nav_stack = QStackedWidget()
        layout_izquierdo.addWidget(self.nav_stack, 1)

        # Indicador simple de bateria
        self.battery_label = QLabel("Bateria: -- %")
        self.battery_label.setStyleSheet("color: #abb2bf; font-weight: bold;")
        layout_izquierdo.addWidget(self.battery_label)

        conect_page = QWidget()
        conect_layout = QVBoxLayout(conect_page)
        conect_layout.setContentsMargins(6, 6, 6, 6)
        conect_layout.setSpacing(10)

        connect_title = QLabel("Conectar dispositivo")
        connect_title.setProperty("role", "sectionTitle")
        connect_hint = QLabel("Segui estos pasos para conectar y dejar el equipo listo para usar.")
        connect_hint.setProperty("role", "sectionHint")
        connect_hint.setWordWrap(True)
        conect_layout.addWidget(connect_title)
        conect_layout.addWidget(connect_hint)

        connection_guide_group = QGroupBox("Guia de conexion")
        connection_guide_group.setStyleSheet("QGroupBox { color: #e5c07b; }")
        connection_guide_layout = QVBoxLayout(connection_guide_group)
        connection_guide_layout.setContentsMargins(10, 8, 10, 8)
        connection_guide_layout.setSpacing(8)

        factory_mode_hint = QLabel(
            "LED verde titilando -> conectate a <b>NEURION_AP</b> "
            "(clave: <b>clave1234</b>) -> <b>Buscar dispositivo</b> "
            "-> <b>Listar redes WiFi</b> -> <b>Configurar WiFi local</b>."
        )
        factory_mode_hint.setProperty("role", "sectionHint")
        factory_mode_hint.setWordWrap(True)

        sta_mode_hint = QLabel(
            "LED azul fijo -> conectate a la misma red WiFi del equipo "
            "-> <b>Buscar dispositivo</b> -> listo para usar."
        )
        sta_mode_hint.setProperty("role", "sectionHint")
        sta_mode_hint.setWordWrap(True)

        reset_mode_hint = QLabel(
            "LED azul titilando por mas de 5 s -> mantene <b>RESET</b> por 10 s "
            "-> espera LED verde titilando -> segui el primer procedimiento."
        )
        reset_mode_hint.setProperty("role", "sectionHint")
        reset_mode_hint.setWordWrap(True)

        connection_guide_layout.addWidget(factory_mode_hint)
        connection_guide_layout.addWidget(sta_mode_hint)
        connection_guide_layout.addWidget(reset_mode_hint)
        conect_layout.addWidget(connection_guide_group)

        # Botón de discovery (escucha broadcast del dispositivo)
        self.btn_discover = QPushButton("Buscar dispositivo")
        self.btn_discover.setProperty("variant", "primary")
        self.btn_discover.setFocusPolicy(Qt.NoFocus)
        self.btn_discover.setToolTip("Escuchar en el puerto UDP para detectar la IP del dispositivo")
        self.btn_discover.clicked.connect(self.start_discovery_listen)
        conect_layout.addWidget(self.btn_discover)

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
        password_row = QHBoxLayout()
        password_row.setContentsMargins(0, 0, 0, 0)
        password_row.setSpacing(8)
        password_row.addWidget(self.input_password, 1)
        self.btn_password_toggle = QToolButton()
        self.btn_password_toggle.setCheckable(True)
        self.btn_password_toggle.setFocusPolicy(Qt.NoFocus)
        self.btn_password_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_password_toggle.setFixedSize(32, 32)
        self.btn_password_toggle.setIconSize(QSize(18, 18))
        self.btn_password_toggle.setToolTip("Mostrar u ocultar la contraseña")
        self.btn_password_toggle.toggled.connect(self.toggle_password_visibility)
        self._refresh_password_toggle_ui()
        password_row.addWidget(self.btn_password_toggle)
        wifi_grid.addLayout(password_row, 1, 1)

        self.btn_send_wifi = QPushButton("Configurar WiFi local")
        self.btn_send_wifi.setFocusPolicy(Qt.NoFocus)
        self.btn_send_wifi.setToolTip("Envía las credenciales por UDP al dispositivo detectado")
        self.btn_send_wifi.clicked.connect(self.handle_wifi_credentials)
        self.btn_send_wifi.setToolTip("Primero lista las redes WiFi y luego envia las credenciales al dispositivo detectado")
        wifi_grid.addWidget(self.btn_send_wifi, 2, 0, 1, 2)

        self.btn_ap_mode = None

        self.btn_scan_wifi = QPushButton("Listar redes WiFi")
        self.btn_scan_wifi.setProperty("variant", "ghost")
        self.btn_scan_wifi.setFocusPolicy(Qt.NoFocus)
        self.btn_scan_wifi.setToolTip("Escanear redes visibles desde la ESP32 y mostrarlas en la consola")
        self.btn_scan_wifi.clicked.connect(self.handle_scan_wifi_networks)
        wifi_grid.addWidget(self.btn_scan_wifi, 3, 0, 1, 2)
        self.btn_send_wifi.setEnabled(False)
        self.btn_scan_wifi.setEnabled(False)

        self.label_serial_status = QLabel("")
        self.label_serial_status.setStyleSheet("color: #98c379;")
        wifi_grid.addWidget(self.label_serial_status, 5, 0, 1, 2)

        conect_layout.addWidget(wifi_group)

        self.wifi_networks_group = QGroupBox("Redes detectadas")
        self.wifi_networks_group.setStyleSheet("QGroupBox { color: #56b6c2; }")
        wifi_networks_layout = QVBoxLayout(self.wifi_networks_group)
        wifi_networks_layout.setContentsMargins(10, 8, 10, 10)
        wifi_networks_layout.setSpacing(8)
        self.wifi_networks_hint = QLabel("Pulsa 'Listar redes WiFi' para ver las redes visibles desde el dispositivo.")
        self.wifi_networks_hint.setProperty("role", "sectionHint")
        self.wifi_networks_hint.setWordWrap(True)
        wifi_networks_layout.addWidget(self.wifi_networks_hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(0)
        scroll.setMinimumHeight(180)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_content = QWidget()
        scroll_content.setObjectName("wifiScrollContent")
        self.wifi_scroll_content = scroll_content
        self.wifi_networks_list = QVBoxLayout(scroll_content)
        self.wifi_networks_list.setContentsMargins(0, 0, 0, 0)
        self.wifi_networks_list.setSpacing(8)
        self.wifi_networks_empty = QLabel("Aun no hay redes listadas.")
        self.wifi_networks_empty.setProperty("role", "sectionHint")
        self.wifi_networks_empty.setWordWrap(True)
        self.wifi_networks_list.addWidget(self.wifi_networks_empty)
        self.wifi_networks_list.addStretch(1)
        self.wifi_scroll = scroll
        scroll.setWidget(scroll_content)
        wifi_networks_layout.addWidget(scroll)
        conect_layout.addWidget(self.wifi_networks_group)
        conect_layout.addStretch(1)

        # Pestaña Configuración
        config_page = QWidget()
        config_layout = QVBoxLayout(config_page)
        config_layout.setContentsMargins(6, 6, 6, 6)
        config_layout.setSpacing(8)
        config_layout.setAlignment(Qt.AlignTop)

        config_title = QLabel("Configurar adquisición")
        config_title.setProperty("role", "sectionTitle")
        config_hint = QLabel("Empieza con una configuración básica por tipo de señal y usa la sección avanzada solo si necesitas ajustar canal por canal.")
        config_hint.setProperty("role", "sectionHint")
        config_hint.setWordWrap(True)
        config_layout.addWidget(config_title)
        config_layout.addWidget(config_hint)

        self.basic_group = QGroupBox("Configuración básica")
        self.basic_group.setStyleSheet("QGroupBox { color: #56b6c2; }")
        basic_group_layout = QVBoxLayout(self.basic_group)
        basic_group_layout.setContentsMargins(10, 8, 10, 8)
        basic_group_layout.setSpacing(10)
        basic_hint = QLabel(
            "Elige el tipo de señal para aplicar un perfil rápido al ADS visible. "
            "Ganancias sugeridas: ECG 2, EOG 2, EMG 4, EEG 24."
        )
        basic_hint.setProperty("role", "sectionHint")
        basic_hint.setWordWrap(True)
        basic_group_layout.addWidget(basic_hint)
        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        self.basic_signal_buttons = {}
        for profile_name in self.filter_profile_options:
            btn_preset = QPushButton(profile_name)
            btn_preset.setCheckable(True)
            btn_preset.clicked.connect(lambda checked, p=profile_name: self.apply_basic_signal_profile(p, checked))
            btn_preset.setToolTip(
                f"{profile_name}: ganancia {self.basic_signal_defaults[profile_name]} para todos los canales"
            )
            preset_row.addWidget(btn_preset)
            self.basic_signal_buttons[profile_name] = btn_preset
        basic_group_layout.addLayout(preset_row)
        self.basic_signal_summary = QLabel("Aplicará filtro y ganancia sugerida a todos los canales del ADS seleccionado.")
        self.basic_signal_summary.setProperty("role", "sectionHint")
        self.basic_signal_summary.setWordWrap(True)
        basic_group_layout.addWidget(self.basic_signal_summary)
        self.btn_show_advanced = QPushButton("Ajustes avanzados")
        self.btn_show_advanced.clicked.connect(lambda: self.show_advanced_config(True))
        basic_group_layout.addWidget(self.btn_show_advanced, alignment=Qt.AlignRight)
        config_layout.addWidget(self.basic_group)

        self.advanced_group = QGroupBox("Configuración avanzada")
        self.advanced_group.setStyleSheet("QGroupBox { color: #e06c75; }")
        config_group_layout = QVBoxLayout(self.advanced_group)
        config_group_layout.setContentsMargins(10, 8, 10, 8)
        config_group_layout.setSpacing(10)
        self.btn_back_basic = QPushButton("Volver a configuración básica")
        self.btn_back_basic.clicked.connect(lambda: self.show_advanced_config(False))
        config_group_layout.addWidget(self.btn_back_basic, alignment=Qt.AlignRight)

        device_row = QHBoxLayout()
        device_row.setSpacing(6)
        self.config_device_label = QLabel("Dispositivo:")
        device_row.addWidget(self.config_device_label)
        self.config_device_combo = QComboBox()
        self.config_device_combo.currentIndexChanged.connect(self.on_config_device_changed)
        device_row.addWidget(self.config_device_combo)
        device_row.addStretch(1)
        config_group_layout.addLayout(device_row)
        self._refresh_config_device_selector(0)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        bulk_hint = QLabel("La fila 'Todos' permite cambios masivos antes de editar canal por canal.")
        bulk_hint.setProperty("role", "sectionHint")
        bulk_hint.setWordWrap(True)
        config_group_layout.addWidget(bulk_hint)
        # Hacer columnas equiespaciadas
        for col_idx in range(7):
            grid.setColumnStretch(col_idx, 1)
        headers = ["Canal", "ON/OFF", "Gain", "Test", "Bias", "Filtrado"]
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

        cb_all_bias = QCheckBox()
        cb_all_bias.setToolTip("Aplicar Bias a todos los canales (Bias+ y Bias-)")
        cb_all_bias.setStyleSheet(
            "QCheckBox::indicator { width: 16px; height: 16px; }"
            "QCheckBox::indicator:checked { background-color: #98c379; border: 1px solid #98c379; }"
            "QCheckBox::indicator:unchecked { border: 1px solid #3e4451; }"
        )
        cb_all_bias.stateChanged.connect(self.apply_all_bias_changed)
        grid.addWidget(cb_all_bias, 1, 4, alignment=Qt.AlignCenter)

        combo_all_filter = QComboBox()
        for profile_name in self.filter_profile_options:
            combo_all_filter.addItem(profile_name, profile_name)
        combo_all_filter.setCurrentIndex(0)
        combo_all_filter.setToolTip(self._filter_profile_summary("ECG"))
        combo_all_filter.currentIndexChanged.connect(self.apply_all_filter_changed)
        grid.addWidget(combo_all_filter, 1, 5)

        self.config_apply_all = {
            "on": cb_all_on,
            "gain": combo_all_gain,
            "test": cb_all_test,
            "bias": cb_all_bias,
            "filter": combo_all_filter,
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

            cb_bias = QCheckBox()
            cb_bias.setChecked(False)
            cb_bias.setStyleSheet(
                "QCheckBox::indicator { width: 16px; height: 16px; }"
                "QCheckBox::indicator:checked { background-color: #98c379; border: 1px solid #98c379; }"
                "QCheckBox::indicator:unchecked { border: 1px solid #3e4451; }"
            )
            grid.addWidget(cb_bias, row_idx, 4, alignment=Qt.AlignCenter)

            combo_filter = QComboBox()
            for profile_name in self.filter_profile_options:
                combo_filter.addItem(profile_name, profile_name)
            combo_filter.setCurrentIndex(0)
            combo_filter.setToolTip(self._filter_profile_summary("ECG"))
            grid.addWidget(combo_filter, row_idx, 5)

            self.config_rows.append({
                "on": cb_on,
                "gain": combo_gain,
                "test": cb_test,
                "bias": cb_bias,
                "filter": combo_filter,
            })

        self._load_filter_profiles_for_device(self._config_ui_device)

        config_group_layout.addLayout(grid)
        actions_hint = QLabel("Lee la configuración actual o aplica los cambios avanzados al dispositivo.")
        actions_hint.setProperty("role", "sectionHint")
        actions_hint.setWordWrap(True)
        config_group_layout.addWidget(actions_hint)
        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(12)
        self.btn_cfg_read = QPushButton("Leer del equipo")
        self.btn_cfg_send = QPushButton("Aplicar cambios")
        self.btn_theme_toggle = QPushButton("Tema claro")
        self.btn_theme_toggle.setCheckable(True)
        self.btn_theme_toggle.setChecked(False)
        for btn in (self.btn_cfg_read, self.btn_cfg_send):
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.btn_cfg_read.clicked.connect(self.config_read_from_device)
        self.btn_cfg_send.clicked.connect(self.config_send_to_device)
        actions_layout.addStretch(1)
        actions_layout.addWidget(self.btn_cfg_read)
        actions_layout.addWidget(self.btn_cfg_send)
        actions_layout.addStretch(1)
        config_group_layout.addLayout(actions_layout)
        config_layout.addWidget(self.advanced_group)
        self.advanced_group.setVisible(False)
        config_layout.addStretch(1)

        # Pestaña Record (contenido actual)
        record_page = QWidget()
        record_layout = QVBoxLayout(record_page)
        record_layout.setContentsMargins(6, 6, 6, 6)
        record_layout.setSpacing(10)

        record_title = QLabel("Adquirir y visualizar")
        record_title.setProperty("role", "sectionTitle")
        record_hint = QLabel("Controla qué dispositivos y canales se muestran. Los comandos principales están sobre la gráfica.")
        record_hint.setProperty("role", "sectionHint")
        record_hint.setWordWrap(True)
        record_layout.addWidget(record_title)
        record_layout.addWidget(record_hint)

        self.btn_start = QPushButton("▶ Iniciar")
        self.btn_pause = QPushButton("▮▮ Pausar")
        self.btn_log_toggle = QPushButton("Grabar CSV")
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
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_start.clicked.connect(self.start_signal)
        self.btn_pause.clicked.connect(self.pause_signal)
        self.btn_log_toggle.toggled.connect(self.toggle_logging)
        self.btn_measure_clear = QPushButton("✕")
        self.btn_measure_clear.setToolTip("Quita las lineas horizontales y verticales actuales")
        self.btn_measure_clear.setAutoDefault(False)
        self.btn_measure_clear.setDefault(False)
        self.btn_measure_clear.setFocusPolicy(Qt.NoFocus)
        self.btn_measure_clear.setFixedSize(32, 32)
        self.btn_measure_clear.clicked.connect(self.clear_measurements)
        for cursor_key in ("V1", "V2", "H1", "H2"):
            btn_cursor = QPushButton(cursor_key)
            btn_cursor.setCheckable(True)
            btn_cursor.setAutoDefault(False)
            btn_cursor.setDefault(False)
            btn_cursor.setFocusPolicy(Qt.NoFocus)
            btn_cursor.setToolTip(f"Selecciona {cursor_key} y luego haz click en la grafica")
            btn_cursor.clicked.connect(lambda checked, key=cursor_key.lower(): self.select_measurement_target(key, checked))
            self.measurement_cursor_buttons[cursor_key.lower()] = btn_cursor
        self.measurement_label = QLabel("V2 - V1 = --, --\nH2 - H1 = --")
        self.measurement_label.setProperty("role", "sectionHint")
        self.measurement_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.measurement_label.setWordWrap(False)
        self.measurement_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.measurement_group = QGroupBox("Medicion")
        self.measurement_group.setStyleSheet("QGroupBox { color: #e5c07b; }")
        self.measurement_group.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        measurement_group_layout = QHBoxLayout(self.measurement_group)
        measurement_group_layout.setContentsMargins(10, 8, 10, 10)
        measurement_group_layout.setSpacing(8)
        measurement_group_layout.addWidget(self.measurement_cursor_buttons["v1"])
        measurement_group_layout.addWidget(self.measurement_cursor_buttons["v2"])
        measurement_group_layout.addWidget(self.measurement_cursor_buttons["h1"])
        measurement_group_layout.addWidget(self.measurement_cursor_buttons["h2"])
        measurement_group_layout.addWidget(self.btn_measure_clear)
        measurement_group_layout.addWidget(self.measurement_label)
        self._refresh_measurement_label_width()

        channel_group = QGroupBox("Visibilidad de canales")
        channel_group.setStyleSheet("QGroupBox { color: #c678dd; }")
        channel_layout = QVBoxLayout(channel_group)
        channel_hint = QLabel("Activa dispositivos completos o canales individuales para despejar la gráfica.")
        channel_hint.setProperty("role", "sectionHint")
        channel_hint.setWordWrap(True)
        channel_layout.addWidget(channel_hint)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        grid.addWidget(QLabel("Canal"), 0, 0)
        self.device_checks = []
        self.device_column_widgets = {}
        for d in range(NUM_DEVICES):
            cb_dev = QCheckBox(f"{d+1}")
            cb_dev.setStyleSheet("color: #abb2bf; font-weight: 700;")
            cb_dev.setChecked(False)
            cb_dev.setProperty("device_id", d)
            cb_dev.stateChanged.connect(self.toggle_device)
            self.device_checks.append(cb_dev)
            self.device_column_widgets[d] = [cb_dev]
            grid.addWidget(cb_dev, 0, d + 1)

        self.channel_checks = {}
        self.channel_row_labels = []
        for ch in range(CHANNELS_PER_DEVICE):
            channel_label = QLabel(f"Canal {ch+1}")
            self.channel_row_labels.append(channel_label)
            grid.addWidget(channel_label, ch + 1, 0)
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
                self.device_column_widgets[d].append(cb)
                grid.addWidget(cb, ch + 1, d + 1)

        channel_layout.addLayout(grid)
        record_layout.addWidget(channel_group)

        # Slicer de ventana temporal
        window_group = QGroupBox("Ajustes de visualización")
        window_group.setStyleSheet("QGroupBox { color: #e5c07b; }")
        window_layout = QVBoxLayout(window_group)
        window_hint = QLabel("Ajusta cuántos segundos ves en pantalla y activa filtros visuales solo para lectura.")
        window_hint.setProperty("role", "sectionHint")
        window_hint.setWordWrap(True)
        window_layout.addWidget(window_hint)
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
        self.cb_notch_50hz = QCheckBox("Filtro visual 50 Hz")
        self.cb_notch_50hz.setChecked(False)
        self.cb_notch_50hz.setToolTip("Aplica un notch de 50 Hz solo a la visualizacion")
        self.cb_notch_50hz.stateChanged.connect(self.set_visual_notch_enabled)
        window_layout.addWidget(self.cb_notch_50hz)
        record_layout.addWidget(window_group)

        self.function_group = QGroupBox("Funciones")
        self.function_group.setStyleSheet("QGroupBox { color: #56b6c2; }")
        function_layout = QVBoxLayout(self.function_group)
        function_layout.setContentsMargins(10, 8, 10, 10)
        function_layout.setSpacing(8)
        function_hint = QLabel(
            "Agrega funciones derivadas por canal. Se grafican con el mismo color del canal y trazo diferenciado."
        )
        function_hint.setProperty("role", "sectionHint")
        function_hint.setWordWrap(True)
        function_layout.addWidget(function_hint)

        function_controls = QHBoxLayout()
        function_controls.setContentsMargins(0, 0, 0, 0)
        function_controls.setSpacing(4)
        self.function_type_combo = ArrowComboBox()
        self.function_type_combo.setView(QListView())
        self.function_type_combo.setFixedWidth(122)
        self.function_type_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.function_type_combo.setToolTip("Selecciona la transformacion a calcular")
        for function_def in self.function_definitions:
            self.function_type_combo.addItem(function_def["label"], function_def["key"])
        self.function_channel_combo = ArrowComboBox()
        self.function_channel_combo.setView(QListView())
        self.function_channel_combo.setMaxVisibleItems(max(CHANNELS_PER_DEVICE, 8))
        self.function_channel_combo.setFixedWidth(92)
        self.function_channel_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.function_channel_combo.setToolTip("Selecciona el canal al que se aplicara la transformacion")
        self.btn_function_add = QPushButton("Agregar")
        self.btn_function_add.setAutoDefault(False)
        self.btn_function_add.setDefault(False)
        self.btn_function_add.setFocusPolicy(Qt.NoFocus)
        self.btn_function_add.setFixedWidth(92)
        self.btn_function_add.clicked.connect(self.add_channel_function)
        function_controls.addWidget(self.function_type_combo)
        function_controls.addWidget(self.function_channel_combo)
        function_controls.addWidget(self.btn_function_add)
        function_controls.addStretch(1)
        function_layout.addLayout(function_controls)

        self.function_list_scroll = QScrollArea()
        self.function_list_scroll.setWidgetResizable(True)
        self.function_list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.function_list_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.function_list_scroll.setFrameShape(0)
        self.function_list_scroll.setMinimumHeight(110)
        self.function_list_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.function_list_container = QWidget()
        self.function_list_scroll.setWidget(self.function_list_container)
        self.function_list_layout = QVBoxLayout(self.function_list_container)
        self.function_list_layout.setContentsMargins(0, 0, 0, 0)
        self.function_list_layout.setSpacing(6)
        self.function_empty_label = QLabel("Aun no hay funciones agregadas.")
        self.function_empty_label.setProperty("role", "sectionHint")
        self.function_empty_label.setWordWrap(True)
        self.function_list_layout.addWidget(self.function_empty_label)
        self.function_list_layout.addStretch(1)
        function_layout.addWidget(self.function_list_scroll)
        record_layout.addWidget(self.function_group)
        self._refresh_function_channel_combo()
        self._render_channel_functions()
        record_layout.addStretch(1)

        self.nav_stack.addWidget(conect_page)  # 0
        self.nav_stack.addWidget(config_page)  # 1
        self.nav_stack.addWidget(record_page)  # 2
        # Mostrar la pestaña de Conectividad al iniciar
        self.nav_stack.setCurrentIndex(0)

        self.nav_conect.toggled.connect(lambda checked: checked and self.nav_stack.setCurrentIndex(0))
        self.nav_config.toggled.connect(lambda checked: checked and self.nav_stack.setCurrentIndex(1))
        self.nav_record.toggled.connect(lambda checked: checked and self.nav_stack.setCurrentIndex(2))

        
        # --- 3. CONFIGURACIÓN DEL PANEL DERECHO (Plot y Consola) ---
        # Panel derecho alojado en el splitter principal
        self.panel_derecho = QWidget()
        self.main_splitter.addWidget(self.panel_derecho)
        layout_derecho = QVBoxLayout(self.panel_derecho)
        layout_derecho.setContentsMargins(0, 0, 0, 0)
        layout_derecho.setSpacing(10)

        self.status_bar = QWidget()
        self.status_bar.setObjectName("StatusBar")
        status_layout = QHBoxLayout(self.status_bar)
        status_layout.setContentsMargins(12, 10, 12, 10)
        status_layout.setSpacing(10)
        self.status_conn_value = self._build_status_card(status_layout, "Conexion", "Sin enlace")
        self.status_device_value = self._build_status_card(status_layout, "Dispositivo", "Sin IP")
        self.status_stream_value = self._build_status_card(status_layout, "Streaming", "En pausa")
        self.status_record_value = self._build_status_card(status_layout, "Grabacion", "Sin archivo")
        self.status_batt_value = self._build_status_card(status_layout, "Bateria", "-- %")

        status_actions = QHBoxLayout()
        status_actions.setSpacing(8)
        self.btn_theme_toggle.clicked.connect(self.toggle_theme)
        self.btn_diag_toggle = QPushButton("Diagnostico")
        self.btn_diag_toggle.setCheckable(True)
        self.btn_diag_toggle.clicked.connect(self.toggle_diagnostics)
        status_actions.addWidget(self.btn_theme_toggle)
        status_actions.addWidget(self.btn_diag_toggle)
        status_layout.addStretch(1)
        status_layout.addLayout(status_actions)
        layout_derecho.addWidget(self.status_bar)

        self.acquisition_bar = QWidget()
        self.acquisition_bar.setObjectName("AcquisitionBar")
        acquisition_layout = QHBoxLayout(self.acquisition_bar)
        acquisition_layout.setContentsMargins(12, 10, 12, 10)
        acquisition_layout.setSpacing(12)
        acquisition_copy = QVBoxLayout()
        acquisition_title = QLabel("Monitor en vivo")
        acquisition_title.setProperty("role", "sectionTitle")
        acquisition_hint = QLabel("Inicia la adquisicion, pausa el flujo o graba CSV sin abandonar la vista de señales.")
        acquisition_hint.setProperty("role", "sectionHint")
        acquisition_hint.setWordWrap(True)
        acquisition_copy.addWidget(acquisition_title)
        acquisition_copy.addWidget(acquisition_hint)
        acquisition_layout.addLayout(acquisition_copy, 1)
        acquisition_layout.addWidget(self.btn_start)
        acquisition_layout.addWidget(self.btn_pause)
        acquisition_layout.addWidget(self.btn_log_toggle)
        acquisition_layout.addWidget(self.measurement_group)
        layout_derecho.addWidget(self.acquisition_bar)

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
        console_layout.setContentsMargins(8, 8, 8, 8)

        console_title = QLabel("Diagnostico y comandos")
        console_title.setProperty("role", "consoleTitle")
        console_layout.addWidget(console_title)
        
        # Área de Historial/Salida
        self.console_output = QPlainTextEdit()
        self.console_output.setReadOnly(True)
        self.console_output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.console_output.setStyleSheet("font-family: 'Consolas', 'Courier New';")
        console_layout.addWidget(self.console_output)
        
        # Entrada de Comando
        self.console_input = QLineEdit()
        self.console_input.setPlaceholderText("Escribe un comando y presiona Enter...")
        
        # Conexión: Cuando se presiona Enter, se llama a handle_command
        self.console_input.returnPressed.connect(self.handle_command) 
        
        console_layout.addWidget(self.console_input)
        self.right_splitter.addWidget(self.console_container)
        self.right_splitter.setStretchFactor(0, 3)
        self.right_splitter.setStretchFactor(1, 1)
        self.console_container.setVisible(False)
        self.right_splitter.setSizes([1, 0])

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
        self.apply_theme(False)
        self._set_runtime_status(
            connection="Sin enlace",
            device="Sin IP",
            streaming="En pausa",
            recording="Sin archivo",
            battery="-- %",
            hint="Conectate al AP del dispositivo y pulsa Buscar dispositivo.",
            connection_state="neutral",
        )

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
        self._sync_basic_signal_profile_from_ui()

    def apply_all_test_changed(self, state):
        """Aplica el flag de test a todos los canales."""
        enabled = state == Qt.Checked
        for row in self.config_rows:
            cb = row["test"]
            cb.blockSignals(True)
            cb.setChecked(enabled)
            cb.blockSignals(False)

    def apply_all_bias_changed(self, state):
        """Aplica Bias a todos los canales."""
        enabled = state == Qt.Checked
        for row in self.config_rows:
            cb = row["bias"]
            cb.blockSignals(True)
            cb.setChecked(enabled)
            cb.blockSignals(False)

    def apply_all_filter_changed(self, index: int):
        """Aplica el perfil de filtrado seleccionado a todos los canales."""
        combo_all = self.config_apply_all.get("filter")
        if not combo_all:
            return
        profile_name = combo_all.itemData(index)
        if not profile_name:
            return
        for row in self.config_rows:
            combo = row["filter"]
            combo.blockSignals(True)
            idx = combo.findData(profile_name)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.setToolTip(self._filter_profile_summary(profile_name))
            combo.blockSignals(False)
        self._save_filter_profiles_for_current_device()
        self._sync_basic_signal_profile_from_ui()

    def _filter_profile_summary(self, profile_name: str) -> str:
        """Devuelve una descripcion corta del perfil, definido en firmware."""
        return f"{profile_name}: perfil definido en firmware"

    def _update_basic_signal_buttons(self):
        for profile_name, button in self.basic_signal_buttons.items():
            is_selected = profile_name == self.basic_selected_profile
            button.blockSignals(True)
            button.setChecked(is_selected)
            button.blockSignals(False)
            self._set_button_variant(button, "primary" if is_selected else "secondary")

    def apply_basic_signal_profile(self, profile_name: str, checked: bool = True):
        """Aplica un perfil rapido de filtro y ganancia al ADS visible y lo envía al dispositivo."""
        if self._basic_signal_syncing or profile_name not in self.basic_signal_defaults:
            return
        if not checked and self.basic_selected_profile == profile_name:
            checked = True
        if not checked:
            return

        gain_value = self.basic_signal_defaults[profile_name]
        self.basic_selected_profile = profile_name
        self._update_basic_signal_buttons()

        combo_all_filter = self.config_apply_all.get("filter")
        if combo_all_filter:
            idx = combo_all_filter.findData(profile_name)
            if idx >= 0:
                combo_all_filter.setCurrentIndex(idx)
        combo_all_gain = self.config_apply_all.get("gain")
        if combo_all_gain:
            idx = combo_all_gain.findData(gain_value)
            if idx >= 0:
                combo_all_gain.setCurrentIndex(idx)

        if self.basic_signal_summary:
            self.basic_signal_summary.setText(
                f"Perfil básico seleccionado: {profile_name}. "
                f"Filtro {profile_name} y ganancia {gain_value} aplicados a todos los canales del ADS visible."
            )
        self.append_to_console(
            f"Configuracion basica aplicada: {profile_name} con ganancia {gain_value} para el ADS visible."
        )
        if self.device_ip:
            self.config_send_to_device()

    def _sync_basic_signal_profile_from_ui(self):
        """Sincroniza el selector basico con la configuracion visible en la UI."""
        if not self.basic_signal_buttons or not self.config_rows:
            return

        filters = []
        gains = []
        for row in self.config_rows:
            filters.append(str(row["filter"].currentData() or row["filter"].currentText() or ""))
            gain_data = row["gain"].currentData()
            if gain_data is None:
                try:
                    gain_data = int(row["gain"].currentText())
                except Exception:
                    gain_data = None
            gains.append(gain_data)

        matched_profile = None
        if filters and gains and len(set(filters)) == 1 and len(set(gains)) == 1:
            candidate = filters[0]
            if candidate in self.basic_signal_defaults and gains[0] == self.basic_signal_defaults[candidate]:
                matched_profile = candidate

        self._basic_signal_syncing = True
        self.basic_selected_profile = matched_profile
        self._update_basic_signal_buttons()
        if matched_profile is None:
            if self.basic_signal_summary:
                self.basic_signal_summary.setText(
                    "Configuracion personalizada. Usa la seccion avanzada para revisar o ajustar canal por canal."
                )
        else:
            if self.basic_signal_summary:
                gain_value = self.basic_signal_defaults[matched_profile]
                self.basic_signal_summary.setText(
                    f"Perfil básico seleccionado: {matched_profile}. "
                    f"Filtro {matched_profile} y ganancia {gain_value} preparados para todos los canales."
                )
        self._basic_signal_syncing = False

    def apply_quick_filter_preset(self, profile_name: str):
        """Aplica un preset de filtrado a todos los canales visibles en la UI."""
        if not self.basic_signal_buttons:
            return
        self.apply_basic_signal_profile(profile_name, checked=True)

    def _save_filter_profiles_for_current_device(self):
        """Guarda en cache los perfiles de filtrado visibles para el ADS seleccionado."""
        dev_idx = self._config_ui_device
        if dev_idx is None or dev_idx < 0 or dev_idx >= NUM_DEVICES:
            return
        for ch_idx, row in enumerate(self.config_rows):
            profile_name = row["filter"].currentData() or row["filter"].currentText() or "ECG"
            self.filter_profiles_by_device[dev_idx][ch_idx] = str(profile_name)

    def _load_filter_profiles_for_device(self, dev_idx: int):
        """Carga en la tabla los perfiles de filtrado cacheados para un ADS."""
        if dev_idx is None or dev_idx < 0 or dev_idx >= NUM_DEVICES:
            return
        for ch_idx, row in enumerate(self.config_rows):
            combo = row["filter"]
            combo.blockSignals(True)
            profile_name = self.filter_profiles_by_device[dev_idx][ch_idx]
            idx = combo.findData(profile_name)
            if idx < 0:
                idx = combo.findData("ECG")
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.setToolTip(self._filter_profile_summary(profile_name))
            combo.blockSignals(False)

        combo_all = self.config_apply_all.get("filter")
        if combo_all:
            combo_all.blockSignals(True)
            first_profile = self.filter_profiles_by_device[dev_idx][0]
            idx = combo_all.findData(first_profile)
            if idx < 0:
                idx = combo_all.findData("ECG")
            if idx >= 0:
                combo_all.setCurrentIndex(idx)
            combo_all.setToolTip(self._filter_profile_summary(first_profile))
            combo_all.blockSignals(False)

    def on_config_device_changed(self, index: int):
        """Preserva los perfiles de filtrado al cambiar el ADS visible en Config."""
        if index < 0:
            return
        new_dev = self.config_device_combo.itemData(index)
        if new_dev is None:
            return
        self._save_filter_profiles_for_current_device()
        self._config_ui_device = int(new_dev)
        self._load_filter_profiles_for_device(self._config_ui_device)
        self._sync_basic_signal_profile_from_ui()

    def _refresh_config_device_selector(self, num_available: int):
        """Muestra solo los ADS detectados y oculta el selector si hay uno solo."""
        if self.config_device_combo is None:
            return

        visible_devices = max(0, min(NUM_DEVICES, int(num_available)))
        previous_dev = self.config_device_combo.currentData()
        if previous_dev is None:
            previous_dev = self._config_ui_device

        self.config_device_combo.blockSignals(True)
        self.config_device_combo.clear()
        for d in range(visible_devices):
            self.config_device_combo.addItem(f"ADS {d+1}", d)

        if visible_devices > 0:
            selected_dev = int(previous_dev) if previous_dev is not None else 0
            selected_dev = max(0, min(visible_devices - 1, selected_dev))
            idx = self.config_device_combo.findData(selected_dev)
            if idx < 0:
                idx = 0
                selected_dev = int(self.config_device_combo.itemData(0))
            self.config_device_combo.setCurrentIndex(idx)
            self._config_ui_device = selected_dev
        else:
            self._config_ui_device = 0
        self.config_device_combo.blockSignals(False)

        show_selector = visible_devices > 1
        if hasattr(self, "config_device_label") and self.config_device_label is not None:
            self.config_device_label.setVisible(show_selector)
        self.config_device_combo.setVisible(show_selector)

        if visible_devices > 0:
            self._load_filter_profiles_for_device(self._config_ui_device)
            self._sync_basic_signal_profile_from_ui()

    def _device_gains_snapshot(self, devices: int) -> list:
        """Devuelve la ganancia por canal segAon Aoltima lectura desde el dispositivo."""
        gains = []
        for d in range(max(1, devices)):
            if d < len(self.gains_from_device):
                gains.extend(self.gains_from_device[d][:CHANNELS_PER_DEVICE])
            else:
                gains.extend([ADS_GAIN_DEFAULT] * CHANNELS_PER_DEVICE)
        return gains

    def _set_button_variant(self, button: Optional[QPushButton], variant: str, **properties):
        if not button:
            return
        button.setProperty("variant", variant)
        for key, value in properties.items():
            button.setProperty(key, value)
        button.style().unpolish(button)
        button.style().polish(button)
        button.update()

    def toggle_password_visibility(self, checked: bool):
        if not self.input_password:
            return
        self.input_password.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self._refresh_password_toggle_ui()

    def _refresh_password_toggle_ui(self):
        if not self.btn_password_toggle:
            return
        icon_color = "#cbd5e1" if self.is_dark_theme else "#475569"
        border = "#3e4451" if self.is_dark_theme else "#c7d0dc"
        hover_bg = "#2a3039" if self.is_dark_theme else "#eef2f7"
        pressed_bg = "#343b46" if self.is_dark_theme else "#dde8f4"
        checked = self.btn_password_toggle.isChecked()
        self.btn_password_toggle.setIcon(make_visibility_icon(checked, icon_color, size=18))
        self.btn_password_toggle.setToolTip("Ocultar contraseña" if checked else "Mostrar contraseña")
        self.btn_password_toggle.setStyleSheet(f"""
QToolButton {{
    background-color: transparent;
    border: 1px solid {border};
    border-radius: 8px;
    padding: 0px;
}}
QToolButton:hover {{ background-color: {hover_bg}; }}
QToolButton:pressed {{ background-color: {pressed_bg}; }}
QToolButton:checked {{ background-color: {hover_bg}; }}
""")

    def _apply_diagnostic_like_button_style(self, button: Optional[QPushButton], compact: bool = False):
        if not button:
            return
        text = "#cbd5e1" if self.is_dark_theme else "#334155"
        border = "#3b4350" if self.is_dark_theme else "#c7d0dc"
        base_bg = "#1a1f27" if self.is_dark_theme else "#ffffff"
        hover_bg = "#2a3039" if self.is_dark_theme else "#eef2f7"
        pressed_bg = "#343b46" if self.is_dark_theme else "#dde8f4"
        disabled_bg = "#2b2f36" if self.is_dark_theme else "#c8d1dc"
        disabled_text = "#5c6370" if self.is_dark_theme else "#7a8699"
        disabled_border = "#3e4451" if self.is_dark_theme else "#b0bbc7"
        padding = "6px 12px" if compact else "10px 16px"
        button.setStyleSheet(f"""
QPushButton {{
    background-color: {base_bg};
    color: {text};
    border: 1px solid {border};
    padding: {padding};
    font-weight: 600;
    border-radius: 8px;
}}
QPushButton:hover {{ background-color: {hover_bg}; }}
QPushButton:pressed {{ background-color: {pressed_bg}; }}
QPushButton:disabled {{
    background-color: {disabled_bg};
    color: {disabled_text};
    border: 1px solid {disabled_border};
}}""")

    def _build_status_card(self, parent_layout: QHBoxLayout, title: str, value: str) -> QLabel:
        card = QWidget()
        card.setObjectName("StatusCard")
        card.setProperty("state", "neutral")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setProperty("role", "statusTitle")
        value_label = QLabel(value)
        value_label.setProperty("role", "statusValue")
        value_label._status_card = card
        card_layout.addWidget(title_label)
        card_layout.addWidget(value_label)
        parent_layout.addWidget(card)
        return value_label

    def _set_status_card_state(self, label: Optional[QLabel], state: str):
        if not label:
            return
        card = getattr(label, "_status_card", None)
        if not card:
            return
        card.setProperty("state", state)
        card.style().unpolish(card)
        card.style().polish(card)
        card.update()

    def _set_runtime_status(
        self,
        connection: Optional[str] = None,
        device: Optional[str] = None,
        streaming: Optional[str] = None,
        recording: Optional[str] = None,
        battery: Optional[str] = None,
        hint: Optional[str] = None,
        connection_state: Optional[str] = None,
    ):
        if connection is not None and self.status_conn_value:
            self.status_conn_value.setText(connection)
        if connection_state is not None:
            self._set_status_card_state(self.status_conn_value, connection_state)
        if device is not None and self.status_device_value:
            self.status_device_value.setText(device)
        if streaming is not None and self.status_stream_value:
            self.status_stream_value.setText(streaming)
        if recording is not None and self.status_record_value:
            self.status_record_value.setText(recording)
        if battery is not None and self.status_batt_value:
            self.status_batt_value.setText(battery)
        if hint is not None and self.label_serial_status:
            self.label_serial_status.setText(hint)

    def _wifi_signal_level_from_bars(self, bars: str) -> int:
        return max(0, min(4, bars.count("|")))

    def _parse_wifi_scan_lines(self, resp_lines):
        """Parsea las líneas de SCAN_WIFI en lista de redes con debug."""
        networks = []
        self.append_to_console(f"DEBUG PARSE: {len(resp_lines)} response packets")
        for pkt_idx, packet in enumerate(resp_lines):
            if not packet:
                continue
            self.append_to_console(f"DEBUG PKT{pkt_idx}: {repr(packet[:100])}...")
            for ln_idx, line in enumerate(packet.splitlines()):
                stripped = line.strip()
                if not stripped:
                    continue
                self.append_to_console(f"DEBUG LN{ln_idx}: '{stripped}'")
                if stripped.startswith("OK SCAN_WIFI") or stripped.startswith("No se encontraron redes visibles."):
                    continue
                m = re.match(r"^(\d+)\.\s*(.+?)\s*\[([| ]{1,5})\]\s*(abierta|open|protegida|secu r)?\s*$", stripped, re.IGNORECASE)
                if m:
                    idx_str, ssid, bars, suffix = m.groups()
                    idx = int(idx_str)
                    is_open = bool(suffix and ("abierta" in suffix.lower() or "open" in suffix.lower()))
                    level = self._wifi_signal_level_from_bars("[" + bars + "]")
                    net = {"index": idx, "ssid": ssid.strip(), "bars": "[" + bars + "]", "level": level, "open": is_open}
                    networks.append(net)
                    self.append_to_console(f"DEBUG PARSED: {net}")
                else:
                    self.append_to_console(f"DEBUG PARSE FAIL: '{stripped}'")
        self.append_to_console(f"DEBUG PARSE DONE: {len(networks)} networks total")
        return networks

    def _clear_wifi_network_cards(self):
        if not self.wifi_networks_list:
            return
        while self.wifi_networks_list.count():
            item = self.wifi_networks_list.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
            elif child_layout:
                child_layout.deleteLater()

    def _make_wifi_signal_widget(self, level: int) -> QWidget:
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        active_color = "#56b6c2" if self.is_dark_theme else "#0078d4"
        inactive_color = "#2f3744" if self.is_dark_theme else "#d1d9e6"
        for idx in range(4):
            bar = QWidget()
            bar.setFixedSize(6, 6 + (idx * 4))
            color = active_color if idx < level else inactive_color
            bar.setStyleSheet(f"background-color: {color}; border-radius: 2px;")
            layout.addWidget(bar, alignment=Qt.AlignBottom)
        return wrapper

    def _select_wifi_network(self, ssid: str, is_open: bool):
        self.input_ssid.setText(ssid)
        if is_open:
            self.input_password.clear()
        self.label_serial_status.setText(f"SSID seleccionado: {ssid}")
        self._set_runtime_status(hint=f"SSID seleccionado: {ssid}")
        self.input_password.setFocus()

    def _build_wifi_network_card(self, network: dict) -> QWidget:
        card = QWidget()
        card.setObjectName("WifiNetworkCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        bg = "#1f2530" if self.is_dark_theme else "#ffffff"
        border = "#364152" if self.is_dark_theme else "#d8e0ea"
        title = "#f3f4f6" if self.is_dark_theme else "#111827"
        subtle = "#9ca3af" if self.is_dark_theme else "#4b5563"
        card.setStyleSheet(
            f"QWidget#WifiNetworkCard {{ background-color: {bg}; border: 1px solid {border}; border-radius: 10px; }}"
        )
        layout = QHBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        ssid_label = QLabel(network["ssid"])
        ssid_label.setStyleSheet(f"color: {title}; font-weight: 700; background: transparent; border: none;")
        ssid_label.setWordWrap(True)
        details = "Red abierta" if network["open"] else "Red protegida"
        details_label = QLabel(details)
        details_label.setStyleSheet(f"color: {subtle}; font-size: 12px; background: transparent; border: none;")
        text_col.addWidget(ssid_label)
        text_col.addWidget(details_label)
        layout.addLayout(text_col, 1)

        signal_icon = self._make_wifi_signal_widget(network["level"])
        layout.addWidget(signal_icon, alignment=Qt.AlignVCenter | Qt.AlignRight)

        use_button = QPushButton("Usar")
        use_button.setMinimumWidth(60)
        self._apply_diagnostic_like_button_style(use_button, compact=True)
        use_button.setFocusPolicy(Qt.NoFocus)
        use_button.clicked.connect(lambda _checked=False, s=network["ssid"], o=network["open"]: self._select_wifi_network(s, o))
        layout.addWidget(use_button, alignment=Qt.AlignVCenter)
        return card

    def _render_wifi_networks(self, networks):
        """Renders detected networks with robust widget refs."""
        self.append_to_console(f"DEBUG RENDER: {len(networks)} networks")
        self.wifi_scan_results = list(networks)
        # Robust refs
        if not hasattr(self, 'wifi_networks_group') or not self.wifi_networks_group:
            self.append_to_console("ERROR: wifi_networks_group missing")
            return
        if not hasattr(self, 'wifi_scroll_content') or not self.wifi_scroll_content:
            self.append_to_console("ERROR: wifi_scroll_content missing")
            return
        if not hasattr(self, 'wifi_networks_list') or self.wifi_networks_list is None:
            self.append_to_console("ERROR: wifi_networks_list None, recreating...")
            self.wifi_networks_list = self.wifi_scroll_content.layout()
        if self.wifi_networks_list is None:
            self.append_to_console("ERROR: cannot get layout")
            return
        self._clear_wifi_network_cards()
        scroll = self.wifi_scroll
        if scroll:
            scroll.setVisible(True)
            scroll.updateGeometry()
            scroll.repaint()
        if not networks:
            self.append_to_console("DEBUG RENDER: empty state")
            hint_text = "No se encontraron redes visibles desde el dispositivo."
            empty_text = "Vuelve a ejecutar el escaneo o acerca el dispositivo al router."
            if hasattr(self, 'wifi_networks_hint') and self.wifi_networks_hint:
                self.wifi_networks_hint.setText(hint_text)
            if hasattr(self, 'wifi_networks_empty') and self.wifi_networks_empty:
                self.wifi_networks_empty.setText(empty_text)
                self.wifi_networks_list.addWidget(self.wifi_networks_empty)
            self.wifi_networks_list.addStretch(1)
            self.wifi_networks_group.updateGeometry()
            self.wifi_networks_group.update()
            self.wifi_networks_group.repaint()
            return

        self.append_to_console("DEBUG RENDER: showing networks")
        hint_text = "Selecciona una red para completar el SSID o ingresa una manualmente."
        if hasattr(self, 'wifi_networks_hint') and self.wifi_networks_hint:
            self.wifi_networks_hint.setText(hint_text)
        for i, network in enumerate(networks):
            try:
                card = self._build_wifi_network_card(network)
                self.wifi_networks_list.addWidget(card)
                self.append_to_console(f"DEBUG RENDER: added card {i+1} '{network['ssid']}' level {network['level']}")
            except Exception as e:
                self.append_to_console(f"ERROR card {i}: {e}")
        self.wifi_networks_list.addStretch(1)
        self.wifi_networks_group.updateGeometry()
        self.wifi_networks_group.update()
        self.wifi_networks_group.repaint()
        if scroll:
            scroll.updateGeometry()
            scroll.repaint()

    def _connection_status_from_wifi_state(self):
        wifi_state = (self.device_wifi_state or "").upper()
        if wifi_state == "FACTORY":
            ap_ip = self.device_ap_ip or self.device_ip or "--"
            return (
                "Esperando SSID",
                f"Modo fabrica activo. Conectado al AP del dispositivo ({ap_ip}) y listo para recibir credenciales WiFi.",
                "warning",
            )
        if wifi_state == "AP_CONFIG":
            ap_ip = self.device_ap_ip or self.device_ip or "--"
            return (
                "Esperando SSID",
                f"Equipo en AP ({ap_ip}) y listo para configurar una red WiFi local.",
                "warning",
            )
        if wifi_state == "STA_CONNECTED":
            sta_ip = self.device_sta_ip or self.device_ip or "--"
            return (
                "Conectado a red local",
                f"Dispositivo asociado a la red local. IP STA: {sta_ip}",
                "success",
            )
        ip_hint = self.device_ip or "IP desconocida"
        return ("Dispositivo detectado", f"IP dispositivo: {ip_hint}", "warning")

    def _apply_connection_status(self):
        connection, hint, connection_state = self._connection_status_from_wifi_state()
        kwargs = {
            "connection": connection,
            "hint": hint,
            "connection_state": connection_state,
        }
        if self.device_ip:
            kwargs["device"] = self.device_ip
        self._set_runtime_status(**kwargs)

    def _apply_snapshot_wifi_line(self, parts):
        if len(parts) < 3 or parts[0] != "CFG":
            return
        key = parts[1]
        value = parts[2]
        if key == "WIFI_STATE":
            self.device_wifi_state = value.upper()
        elif key == "STA_IP":
            self.device_sta_ip = value if value != "0.0.0.0" else None
        elif key == "AP_IP":
            self.device_ap_ip = value if value != "0.0.0.0" else None

    def _apply_wifi_response(self, response):
        if not response:
            return
        parts = response.strip().split()
        if len(parts) < 5 or parts[0] != "OK" or parts[1] != "WIFI":
            return
        try:
            sta_idx = parts.index("STA")
            if sta_idx + 1 < len(parts):
                self.device_sta_ip = parts[sta_idx + 1]
        except ValueError:
            pass
        try:
            ap_idx = parts.index("AP")
            if ap_idx + 1 < len(parts):
                ap_ip = parts[ap_idx + 1]
                self.device_ap_ip = ap_ip if ap_ip != "0.0.0.0" else None
        except ValueError:
            pass
        if self.device_sta_ip:
            self.device_wifi_state = "STA_CONNECTED"

    def show_advanced_config(self, show_advanced: bool):
        if self.basic_group:
            self.basic_group.setVisible(not show_advanced)
        if self.advanced_group:
            self.advanced_group.setVisible(show_advanced)

    def toggle_diagnostics(self, checked: bool):
        if not self.console_container:
            return
        self.console_container.setVisible(checked)
        if self.btn_diag_toggle:
            self.btn_diag_toggle.setText("Ocultar diagnostico" if checked else "Diagnostico")
        if checked:
            self.right_splitter.setSizes([3, 2])
        else:
            self.right_splitter.setSizes([1, 0])

    def _apply_nav_theme(self):
        stylesheet = get_nav_button_stylesheet(self.is_dark_theme)
        for btn in getattr(self, "nav_buttons", []):
            btn.setStyleSheet(stylesheet)

    def _apply_plot_theme(self):
        bg = "#1e1e1e" if self.is_dark_theme else "#ffffff"
        axis = "#abb2bf" if self.is_dark_theme else "#334155"
        grid_alpha = 0.15 if self.is_dark_theme else 0.08
        for plot in self.device_plots:
            plot.setBackground(bg)
            plot.showGrid(x=True, y=True, alpha=grid_alpha)
            for axis_name in ("left", "bottom"):
                axis_item = plot.plotItem.getAxis(axis_name)
                if axis_item:
                    axis_item.setPen(axis)
                    axis_item.setTextPen(axis)
                    axis_item.setTickFont(QFont("Consolas", 9))

    # --- Temas (oscuro/claro) ---

    def apply_theme(self, is_dark: bool):
        """Aplica el tema oscuro o claro y actualiza el estado del toggle."""
        self.is_dark_theme = bool(is_dark)
        stylesheet = get_dark_stylesheet() if self.is_dark_theme else get_light_stylesheet()
        self.setStyleSheet(stylesheet)
        self._apply_nav_theme()
        self._apply_plot_theme()
        self._set_button_variant(self.btn_discover, "primary")
        self._set_button_variant(self.btn_send_wifi, "primary")
        self._set_button_variant(self.btn_ap_mode, "secondary")
        self._set_button_variant(self.btn_scan_wifi, "ghost")
        self._set_button_variant(self.btn_cfg_read, "secondary")
        self._set_button_variant(self.btn_cfg_send, "primary")
        self._set_button_variant(self.btn_start, "primary")
        self._set_button_variant(self.btn_pause, "secondary")
        self._set_button_variant(
            self.btn_log_toggle,
            "danger",
            recording="true" if self.btn_log_toggle.isChecked() else "false",
        )
        self._set_button_variant(self.btn_measure_clear, "danger")
        self._set_button_variant(self.btn_function_add, "primary")
        self._refresh_measurement_buttons()
        self._set_button_variant(self.btn_theme_toggle, "ghost")
        self._set_button_variant(self.btn_diag_toggle, "ghost")
        self._set_button_variant(self.btn_show_advanced, "ghost")
        self._set_button_variant(self.btn_back_basic, "ghost")
        self._refresh_password_toggle_ui()
        self._refresh_measurement_label_width()
        self._refresh_measurement_lines()
        self._render_channel_functions()
        self._apply_diagnostic_like_button_style(self.btn_scan_wifi)
        self._update_basic_signal_buttons()
        if self.wifi_scan_results:
            self._render_wifi_networks(self.wifi_scan_results)
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
            self._set_runtime_status(device="Sin IP", hint="Busca el dispositivo antes de enviar comandos UDP.")
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

    def send_udp_command_collect(self, command: str, timeout: float = 1.0, idle_timeout: float = 0.35, bufsize: int = 1024):
        """Envía un comando UDP y recoge varias respuestas de texto."""
        if not command:
            return []
        if not self.device_ip:
            self.append_to_console("IP del dispositivo desconocida. Usa 'Buscar dispositivo' antes de enviar comandos UDP.")
            self._set_runtime_status(device="Sin IP", hint="Busca el dispositivo antes de enviar comandos UDP.")
            return []
        try:
            return gc_send_udp_command_collect(
                self.device_ip,
                command,
                PC_UDP_PORT,
                timeout,
                idle_timeout,
                bufsize,
            )
        except Exception as exc:
            self.append_to_console(f"No se pudo enviar comando UDP: {exc}")
            return []

    def _parse_battery_value(self, payload: str) -> Optional[int]:
        if not payload:
            return None
        for line in payload.splitlines():
            parts = line.strip().split()
            if len(parts) >= 3 and parts[0] in {"OK", "CFG"} and parts[1] == "BATT":
                try:
                    return int(parts[2])
                except ValueError:
                    return None
        return None

    def request_battery_status(self, log_errors: bool = False) -> Optional[int]:
        """Consulta la bateria cuando el equipo esta listo pero sin streaming."""
        if not self.device_ip:
            return None
        resp = self.send_udp_command("BATT", timeout=0.35, bufsize=128, log_response=False)
        if not resp:
            if log_errors:
                self.append_to_console("Sin respuesta al consultar bateria.")
            return None
        batt_value = self._parse_battery_value(resp)
        if batt_value is None:
            if log_errors:
                self.append_to_console(f"Respuesta de bateria no valida: {resp}")
            return None
        self.update_battery_indicator(batt_value)
        return batt_value

    def _should_poll_idle_battery(self) -> bool:
        return bool(
            self.device_ip
            and not self.timer.isActive()
            and not self.discovery_socket
            and not self.awaiting_config
        )

    def _update_idle_battery_polling(self, force_refresh: bool = False):
        should_poll = self._should_poll_idle_battery()
        if should_poll:
            if force_refresh:
                self.request_battery_status()
            if not self.battery_poll_timer.isActive():
                self.battery_poll_timer.start()
        else:
            self.battery_poll_timer.stop()

    def poll_idle_battery(self):
        if not self._should_poll_idle_battery():
            self.battery_poll_timer.stop()
            return
        self.request_battery_status()

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
                "Comandos enviados por UDP al dispositivo:\n"
                " - START / STOP / CONNECTIVITY / DISCOVERY_REPLY <ipPC>\n"
                " - BATT\n"
                " - p/i/c/t/x (cambian estado: PC/IDLE/CAL/TEST/ERROR)\n"
                " - CH <dev> <ch> <gain> <pd> <test>\n"
                " - FILTER <dev> <ch> <ECG|EOG|EMG|EEG>\n"
                " - BIAS <dev> <sensP> <sensN>\n"
                " - BIASDRV <dev> <en> [refInt] (1=driver ON, refInt=1 usa ref interna en CONFIG3)\n"
                "   o BIASDRV <dev> 0xHH (escribe CONFIG3 raw en hex)\n"
                " - TEST <dev> <enable> <amp> <freq>\n"
                " - SAVE / LOAD / DUMP / SCAN_WIFI\n"
                " - T <plotEnable> <dev> <channel|all>\n"
                " - HPF <0/1> (filtro pasa-altos 0.2 Hz)\n"
                " - LPF <0/1> (filtro pasa-bajos 500 Hz)\n"
                " - SET_WIFI \"ssid\" \"password\"  (usa \"\" si la red es abierta)"
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
        self._sync_basic_signal_profile_from_ui()

    def _parse_filter_profiles_from_dump(self, dump_text: str) -> dict:
        """Parsea lineas CHnFILTER del DUMP y devuelve {dev: {ch: profile}}."""
        profiles = {}
        current_dev = None
        for raw_line in dump_text.strip().splitlines():
            line = raw_line.strip()
            if line.startswith("DEV "):
                parts = line.split()
                try:
                    current_dev = int(parts[1])
                except Exception:
                    current_dev = None
                continue
            if current_dev is None:
                continue
            if not line.startswith("CH") or "FILTER" not in line:
                continue
            try:
                left, right = line.split("=", 1)
                profile_name = right.strip().upper()
                ch_token = left.split("FILTER", 1)[0].strip()
                ch_idx = int(ch_token.replace("CH", "")) - 1
            except Exception:
                continue
            if ch_idx < 0 or ch_idx >= CHANNELS_PER_DEVICE:
                continue
            if profile_name not in self.filter_profile_options:
                continue
            profiles.setdefault(current_dev, {})[ch_idx] = profile_name
        return profiles

    def _update_filter_profiles_from_dump(self, profiles_dump: dict):
        """Actualiza el cache local de perfiles segun lo leido del dispositivo."""
        for dev_idx, ch_map in profiles_dump.items():
            if dev_idx is None or dev_idx >= NUM_DEVICES:
                continue
            for ch_idx, profile_name in ch_map.items():
                if ch_idx is None or ch_idx >= CHANNELS_PER_DEVICE:
                    continue
                if profile_name in self.filter_profile_options:
                    self.filter_profiles_by_device[dev_idx][ch_idx] = profile_name

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

    def _update_device_gains_from_snapshot_line(self, line: str):
        """Actualiza caches locales usando una linea de snapshot CFG."""
        parts = line.strip().split()
        if len(parts) < 8 or parts[0] != "DEV" or parts[2] != "CH" or parts[4] != "GAIN":
            return
        try:
            dev_idx = int(parts[1])
            ch_idx = int(parts[3])
            gain_val = int(parts[5])
        except ValueError:
            return
        if dev_idx < 0 or dev_idx >= NUM_DEVICES:
            return
        if ch_idx < 0 or ch_idx >= CHANNELS_PER_DEVICE:
            return
        if gain_val <= 0:
            return
        self.gains_from_device[dev_idx][ch_idx] = gain_val
        if len(parts) >= 12 and parts[10] == "FILTER":
            profile_name = parts[11].upper()
            if profile_name in self.filter_profile_options:
                self.filter_profiles_by_device[dev_idx][ch_idx] = profile_name

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
        self._update_filter_profiles_from_dump(self._parse_filter_profiles_from_dump(resp))
        dev = self.config_device_combo.currentData()
        if dev is None:
            dev = 0
        if dev not in cfg:
            dev = sorted(cfg.keys())[0]
            idx_sel = self.config_device_combo.findData(dev)
            if idx_sel >= 0:
                self.config_device_combo.setCurrentIndex(idx_sel)
        self.load_config_into_ui(dev, cfg.get(dev, {}))
        self._load_filter_profiles_for_device(int(dev))
    
    def config_send_to_device(self):
        """Envía BIAS (P/N) y comandos CH por canal según la pestaña Config."""
        if not self.device_ip:
            self.append_to_console("No hay IP del dispositivo. Ejecuta 'Buscar dispositivo'.")
            return
        dev = self.config_device_combo.currentData()
        if dev is None:
            dev = 0
        # BIAS: construir máscaras P/N a partir de checkboxes (bit=1 canal incluido). Si ninguna, se desactiva.
        bias_mask = 0
        for ch_idx, row in enumerate(self.config_rows):
            if row["bias"].isChecked():
                bias_mask |= (1 << ch_idx)
        bias_cmd = f"BIAS {dev} {bias_mask} {bias_mask}"
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
            profile_name = str(row["filter"].currentData() or row["filter"].currentText() or "ECG").upper()
            filter_cmd = f"FILTER {dev} {ch_idx} {profile_name}"
            filter_resp = self.send_udp_command(filter_cmd, timeout=1.5, bufsize=512, log_response=False)
            if filter_resp:
                self.append_to_console(f"FILTER CH{ch_idx + 1} -> {filter_resp}")
            self.filter_profiles_by_device[int(dev)][ch_idx] = profile_name
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
            self.btn_send_wifi.setEnabled(bool(self.device_ip))
            self.btn_scan_wifi.setEnabled(bool(self.device_ip))
            self.btn_connect_serial.setEnabled(False)
            self.label_serial_status.setText("Instala pyserial para usar la conexión serial.")
            self._set_runtime_status(connection="Serial no disponible", hint="Instala pyserial para usar la conexion serial.", connection_state="danger")
            return

        try:
            ports = list(serial.tools.list_ports.comports())
        except Exception as exc:
            self.combo_ports.clear()
            self.combo_ports.addItem("Error listando puertos")
            self.combo_ports.setEnabled(False)
            self.btn_send_wifi.setEnabled(bool(self.device_ip))
            self.btn_scan_wifi.setEnabled(bool(self.device_ip))
            self.btn_connect_serial.setEnabled(False)
            self.label_serial_status.setText(f"Error: {exc}")
            self._set_runtime_status(connection="Error serial", hint=f"Error listando puertos: {exc}", connection_state="danger")
            return

        self.combo_ports.clear()
        if not ports:
            self.combo_ports.addItem("Sin puertos detectados")
            self.combo_ports.setEnabled(False)
            self.btn_send_wifi.setEnabled(bool(self.device_ip))
            self.btn_scan_wifi.setEnabled(bool(self.device_ip))
            self.btn_connect_serial.setEnabled(False)
            self.label_serial_status.setText("Conecta el dispositivo y pulsa Actualizar.")
            self._set_runtime_status(connection="Sin puerto", hint="Conecta el dispositivo y pulsa Actualizar.", connection_state="warning")
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
        self.btn_scan_wifi.setEnabled(True)
        self.btn_connect_serial.setEnabled(True)
        if likely_idx is not None:
            self.combo_ports.setCurrentIndex(likely_idx)
            self.label_serial_status.setText("Puerto sugerido: posible ESP32")
            self._set_runtime_status(connection="Puerto sugerido", hint="Se detecto un posible ESP32 en serial.", connection_state="warning")
        else:
            self.label_serial_status.setText("")
            self._set_runtime_status(connection="Puertos listos", hint="Selecciona un puerto y abre la conexion serial.", connection_state="neutral")

    def _send_serial_command(self, payload: bytes, timeout: float):
        """EnvÃ­a un comando por serial y devuelve las lÃ­neas recibidas."""
        if not self.serial_available:
            raise RuntimeError("pyserial no estÃ¡ instalado; no se puede abrir el puerto.")
        if not self.serial_conn or not self.serial_conn.is_open:
            raise RuntimeError("Conecta primero el puerto serial.")

        ser = self.serial_conn
        ser.reset_input_buffer()
        ser.write(payload)
        ser.flush()

        resp_lines = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = ser.readline()
            if not line:
                continue
            txt = line.decode("utf-8", errors="ignore").strip()
            if txt:
                resp_lines.append(txt)
        return resp_lines

    def _quote_serial_arg(self, value: str) -> str:
        """Escapa un argumento para enviarlo entre comillas al firmware."""
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

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
            self.btn_connect_serial.setText("Abrir serial")
            self.combo_ports.setEnabled(True)
            self.combo_baud.setEnabled(True)
            self.btn_refresh_ports.setEnabled(True)
            self.label_serial_status.setText("Desconectado")
            self._set_runtime_status(connection="Serial desconectado", hint="Puerto serial cerrado.", connection_state="neutral")
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
            self.btn_connect_serial.setText("Cerrar serial")
            self.combo_ports.setEnabled(False)
            self.combo_baud.setEnabled(False)
            self.btn_refresh_ports.setEnabled(False)
            self.label_serial_status.setText(f"Conectado a {port} @ {baud}")
            self.append_to_console(f"Conectado a {port} @ {baud}")
            self._set_runtime_status(connection=f"Serial {port}", hint=f"Conectado a {port} @ {baud}", connection_state="neutral")
        except Exception as exc:
            self.serial_conn = None
            self.append_to_console(f"Error al conectar: {exc}")
            self.label_serial_status.setText(f"Error: {exc}")
            self._set_runtime_status(connection="Error serial", hint=f"Error: {exc}", connection_state="danger")

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

        if not ssid:
            self.append_to_console("Completa el SSID antes de enviar.")
            return

        try:
            payload = (
                f"SET_WIFI {self._quote_serial_arg(ssid)} "
                f"{self._quote_serial_arg(pwd)}\n"
            ).encode("utf-8", errors="ignore")
            resp_lines = self._send_serial_command(payload, timeout=10.0)
            resp = " | ".join(resp_lines).strip()
            if resp:
                self.append_to_console(f"Respuesta del dispositivo: {resp}")
                self.label_serial_status.setText(resp)
                self._set_runtime_status(connection="WiFi configurado", hint=resp, connection_state="neutral")
            else:
                self.append_to_console(f"Enviado WiFi: {ssid} (sin respuesta)")
                self.label_serial_status.setText("Enviado (sin respuesta)")
                self._set_runtime_status(connection="WiFi enviado", hint="Credenciales enviadas sin respuesta.", connection_state="neutral")
        except Exception as exc:
            self.append_to_console(f"Error serial: {exc}")
            self.label_serial_status.setText(f"Error: {exc}")
            self._set_runtime_status(connection="Error serial", hint=f"Error: {exc}", connection_state="danger")

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
                self._set_runtime_status(connection="Modo AP", hint=resp, connection_state="neutral")
            else:
                self.append_to_console("Comando APMode enviado (sin respuesta)")
                self.label_serial_status.setText("APMode enviado (sin respuesta)")
                self._set_runtime_status(connection="Modo AP", hint="APMode enviado (sin respuesta)", connection_state="neutral")
        except Exception as exc:
            self.append_to_console(f"Error serial: {exc}")
            self.label_serial_status.setText(f"Error: {exc}")
            self._set_runtime_status(connection="Error serial", hint=f"Error: {exc}", connection_state="danger")

    def scan_wifi_networks(self):
        """Solicita a la ESP32 un escaneo WiFi y vuelca el resultado en la consola."""
        if not self.serial_available:
            self.append_to_console("pyserial no estÃ¡ instalado; no se puede abrir el puerto.")
            return
        if not self.serial_conn or not self.serial_conn.is_open:
            self.append_to_console("Conecta primero el puerto serial.")
            return

        self.append_to_console("Escaneando redes WiFi visibles desde la ESP32...")
        try:
            resp_lines = self._send_serial_command(b"SCAN_WIFI\n", timeout=8.0)
            if not resp_lines:
                self.append_to_console("Comando SCAN_WIFI enviado (sin respuesta)")
                self.label_serial_status.setText("SCAN_WIFI enviado (sin respuesta)")
                self._set_runtime_status(connection="Scan WiFi", hint="SCAN_WIFI enviado (sin respuesta)", connection_state="neutral")
                return

            self.label_serial_status.setText(resp_lines[0])
            self._set_runtime_status(connection="Scan WiFi", hint=resp_lines[0], connection_state="neutral")
            for line in resp_lines:
                self.append_to_console(line)
        except RuntimeError as exc:
            self.append_to_console(str(exc))
        except Exception as exc:
            self.append_to_console(f"Error serial: {exc}")
            self.label_serial_status.setText(f"Error: {exc}")
            self._set_runtime_status(connection="Error serial", hint=f"Error: {exc}", connection_state="danger")

    def handle_wifi_credentials(self):
        """Envía credenciales por UDP si el equipo ya está descubierto; si no, usa el flujo serial existente."""
        ssid = self.input_ssid.text().strip()
        pwd = self.input_password.text().strip()

        if not ssid:
            self.append_to_console("Completa el SSID antes de enviar.")
            return

        if self.device_ip:
            command = (
                f"SET_WIFI {self._quote_serial_arg(ssid)} "
                f"{self._quote_serial_arg(pwd)}"
            )
            self.append_to_console("Enviando credenciales WiFi por UDP...")
            resp_lines = self.send_udp_command_collect(command, timeout=10.0, idle_timeout=0.5, bufsize=512)
            if resp_lines:
                resp = " | ".join(resp_lines).strip()
                self.append_to_console(f"Respuesta del dispositivo: {resp}")
                if resp_lines[0].startswith("OK WIFI"):
                    self._apply_wifi_response(resp_lines[0])
                    self._apply_connection_status()
                else:
                    self.label_serial_status.setText(resp_lines[0])
                    self._set_runtime_status(connection="Error WiFi", hint=resp, connection_state="danger")
            else:
                self.append_to_console("No hubo respuesta al enviar credenciales WiFi por UDP.")
                self.label_serial_status.setText("Sin respuesta UDP")
                self._set_runtime_status(
                    connection="WiFi local",
                    hint="Sin respuesta del dispositivo al configurar WiFi por UDP.",
                    connection_state="warning",
                )
            return

        self.send_wifi_credentials()

    def handle_scan_wifi_networks(self):
        """Escanea redes por UDP si el equipo ya está descubierto; si no, usa el flujo serial existente."""
        if self.device_ip:
            self.append_to_console("Escaneando redes WiFi visibles desde la ESP32...")
            resp_lines = self.send_udp_command_collect("SCAN_WIFI", timeout=8.0, idle_timeout=0.6, bufsize=512)
            if not resp_lines:
                self.append_to_console("Comando SCAN_WIFI enviado por UDP (sin respuesta)")
                self.label_serial_status.setText("SCAN_WIFI por UDP sin respuesta")
                self._set_runtime_status(connection="Scan WiFi", hint="SCAN_WIFI por UDP sin respuesta", connection_state="warning")
                return

            self.label_serial_status.setText(resp_lines[0])
            self._set_runtime_status(connection="Scan WiFi", hint=resp_lines[0], connection_state="neutral")
            for line in resp_lines:
                self.append_to_console(line)
            return

        self.scan_wifi_networks()

    def send_ap_mode(self):
        """Envia comando para volver a modo AP usando UDP."""
        if not self.device_ip:
            self.append_to_console("No hay IP del dispositivo. Ejecuta 'Buscar dispositivo'.")
            self._set_runtime_status(device="Sin IP", hint="Busca el dispositivo antes de pedir modo AP.")
            return
        try:
            self.append_to_console("Solicitando modo AP al dispositivo...")
            resp_lines = self.send_udp_command_collect("APMode", timeout=3.0, idle_timeout=0.5, bufsize=512)
            if not resp_lines:
                self.append_to_console("Comando APMode enviado por UDP (sin respuesta)")
                self.label_serial_status.setText("APMode por UDP sin respuesta")
                self._set_runtime_status(connection="Modo AP", hint="APMode por UDP sin respuesta", connection_state="warning")
                return
            resp = " | ".join(resp_lines).strip()
            self.append_to_console(f"Respuesta del dispositivo: {resp}")
            self.label_serial_status.setText(resp_lines[0])
            self.device_wifi_state = "AP_CONFIG"
            self.device_sta_ip = None
            self.device_ap_ip = self.device_ip
            self._apply_connection_status()
        except Exception as exc:
            self.append_to_console(f"Error UDP: {exc}")
            self.label_serial_status.setText(f"Error: {exc}")
            self._set_runtime_status(connection="Error UDP", hint=f"Error: {exc}", connection_state="danger")

    def handle_wifi_credentials(self):
        """Envia credenciales por UDP al dispositivo descubierto."""
        ssid = self.input_ssid.text().strip()
        pwd = self.input_password.text().strip()

        if not ssid:
            self.append_to_console("Completa el SSID antes de enviar.")
            return
        if not self.device_ip:
            self.append_to_console("No hay IP del dispositivo. Ejecuta 'Buscar dispositivo'.")
            self._set_runtime_status(device="Sin IP", hint="Busca el dispositivo antes de enviar credenciales WiFi.")
            return

        command = (
            f"SET_WIFI {self._quote_serial_arg(ssid)} "
            f"{self._quote_serial_arg(pwd)}"
        )
        self.append_to_console("Enviando credenciales WiFi por UDP...")
        resp_lines = self.send_udp_command_collect(command, timeout=10.0, idle_timeout=0.5, bufsize=512)
        if resp_lines:
            resp = " | ".join(resp_lines).strip()
            self.append_to_console(f"Respuesta del dispositivo: {resp}")
            if resp_lines[0].startswith("OK WIFI"):
                self._apply_wifi_response(resp_lines[0])
                self._apply_connection_status()
            else:
                self.label_serial_status.setText(resp_lines[0])
                self._set_runtime_status(connection="Error WiFi", hint=resp, connection_state="danger")
            return

        self.append_to_console("No hubo respuesta al enviar credenciales WiFi por UDP.")
        self.label_serial_status.setText("Sin respuesta UDP")
        self._set_runtime_status(
            connection="WiFi local",
            hint="Sin respuesta del dispositivo al configurar WiFi por UDP.",
            connection_state="warning",
        )

    def handle_scan_wifi_networks(self):
        """Escanea redes por UDP usando el dispositivo descubierto."""
        if not self.device_ip:
            self.append_to_console("No hay IP del dispositivo. Ejecuta 'Buscar dispositivo'.")
            self._set_runtime_status(device="Sin IP", hint="Busca el dispositivo antes de listar redes WiFi.")
            return

        self.append_to_console("Escaneando redes WiFi visibles desde la ESP32...")
        resp_lines = self.send_udp_command_collect("SCAN_WIFI", timeout=8.0, idle_timeout=0.6, bufsize=512)
        if not resp_lines:
            self.append_to_console("Comando SCAN_WIFI enviado por UDP (sin respuesta)")
            self.label_serial_status.setText("SCAN_WIFI por UDP sin respuesta")
            self._set_runtime_status(connection="Scan WiFi", hint="SCAN_WIFI por UDP sin respuesta", connection_state="warning")
            return

        networks = self._parse_wifi_scan_lines(resp_lines)
        self.append_to_console(f"INFO: Found {len(networks)} networks to render in GUI")
        self._render_wifi_networks(networks)
        self.label_serial_status.setText(resp_lines[0] if resp_lines else "Scan complete")
        if networks:
            self._set_runtime_status(connection="Scan WiFi", hint=f"Se listaron {len(networks)} redes visibles.", connection_state="neutral")
        else:
            self._set_runtime_status(connection="Scan WiFi", hint="No se encontraron redes visibles.", connection_state="warning")
        for line in resp_lines:
            self.append_to_console(line)

    # --- Lectura de datos UDP (streaming) ---
    def start_data_listener(self):
        """Abre socket UDP para recibir paquetes de datos."""
        if self.data_socket:
            return
        self.battery_poll_timer.stop()
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
            if self.device_ip:
                self._set_runtime_status(
                    connection="Listo para adquirir",
                    hint=f"Dispositivo listo. Streaming escuchando en UDP {PC_UDP_PORT}.",
                    connection_state="success",
                )
            else:
                self._set_runtime_status(
                    connection=f"UDP {PC_UDP_PORT}",
                    hint=f"Escuchando datos UDP en {PC_UDP_PORT}.",
                    connection_state="neutral",
                )
        except Exception as exc:
            self.append_to_console(f"No se pudo abrir socket de datos: {exc}")
            self.data_socket = None
            self._set_runtime_status(connection="Error UDP", hint=f"No se pudo abrir socket de datos: {exc}", connection_state="danger")

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
        self._update_notch_coeffs()
        self.recompute_buffers()

    def recompute_buffers(self):
        """Recalcula longitud de buffers y eje X conservando datos recientes."""
        new_len = max(int(self.window_seconds * self.samples_per_second), 10)
        old_data = self.channel_data
        old_display = self.display_channel_data
        new_data = np.zeros((self.num_channels, new_len))
        new_display = np.zeros((self.num_channels, new_len))
        copy_len = min(old_data.shape[1], new_len)
        if copy_len > 0:
            new_data[:, -copy_len:] = old_data[:, -copy_len:]
            new_display[:, -copy_len:] = old_display[:, -copy_len:]
        self.data_len = new_len
        self.data_x = np.linspace(0, self.window_seconds, self.data_len)
        self.channel_data = new_data
        self.display_channel_data = new_display
        self._rebuild_display_buffers()

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
        self.battery_label.setText(f"Bateria: {percent:3d}% {bar}")
        self._set_runtime_status(battery=f"{percent:3d}%")

    def _update_notch_coeffs(self):
        fs_hz = float(self.samples_per_second)
        if fs_hz <= 100.0:
            self._notch_coeffs = None
            return
        w0 = 2.0 * np.pi * 50.0 / fs_hz
        if w0 <= 0.0 or w0 >= np.pi:
            self._notch_coeffs = None
            return
        q = 20.0
        alpha = np.sin(w0) / (2.0 * q)
        cos_w0 = np.cos(w0)
        a0 = 1.0 + alpha
        self._notch_coeffs = (
            1.0 / a0,
            (-2.0 * cos_w0) / a0,
            1.0 / a0,
            (-2.0 * cos_w0) / a0,
            (1.0 - alpha) / a0,
        )

    def _reset_visual_filter_state(self):
        self._raw_valid_history.fill(0.0)
        self._raw_valid_count.fill(0)
        self._raw_last_valid_uv.fill(0.0)
        self._raw_valid_initialized.fill(False)
        self._raw_hold_active.fill(False)
        self._raw_hold_reference_uv.fill(0.0)
        self._raw_recovery_good_count.fill(0)
        self._visual_last_value.fill(0.0)
        self._visual_initialized.fill(False)
        self._notch_x1.fill(0.0)
        self._notch_x2.fill(0.0)
        self._notch_y1.fill(0.0)
        self._notch_y2.fill(0.0)

    def _apply_notch_sample(self, idx: int, sample_uv: float) -> float:
        if not self.notch_50hz_enabled or self._notch_coeffs is None:
            return sample_uv
        b0, b1, b2, a1, a2 = self._notch_coeffs
        y = (
            b0 * sample_uv
            + b1 * self._notch_x1[idx]
            + b2 * self._notch_x2[idx]
            - a1 * self._notch_y1[idx]
            - a2 * self._notch_y2[idx]
        )
        self._notch_x2[idx] = self._notch_x1[idx]
        self._notch_x1[idx] = sample_uv
        self._notch_y2[idx] = self._notch_y1[idx]
        self._notch_y1[idx] = y
        return y

    def _compute_display_sample(self, idx: int, raw_uv: float) -> float:
        display_uv = raw_uv
        if self.gui_visual_guard_enabled and self._visual_initialized[idx]:
            if abs(raw_uv - self._visual_last_value[idx]) >= self.visual_spike_threshold_uv:
                self.visual_spike_rejections += 1
                now = time.monotonic()
                if now - self._last_visual_spike_log >= 1.0:
                    self._last_visual_spike_log = now
                    self.append_to_console(
                        f"Picos visuales descartados: {self.visual_spike_rejections}. "
                        "Se conserva el ultimo valor mostrado."
                    )
                display_uv = self._visual_last_value[idx]
        display_uv = self._apply_notch_sample(idx, display_uv)
        self._visual_last_value[idx] = display_uv
        self._visual_initialized[idx] = True
        return display_uv

    def _raw_reference_uv(self, idx: int) -> float:
        count = int(self._raw_valid_count[idx])
        if count <= 0:
            return 0.0
        return float(np.median(self._raw_valid_history[idx, :count]))

    def _push_raw_valid_uv(self, idx: int, uv: float):
        count = int(self._raw_valid_count[idx])
        if count < self._raw_valid_history.shape[1]:
            self._raw_valid_history[idx, count] = uv
            self._raw_valid_count[idx] = count + 1
        else:
            self._raw_valid_history[idx, :-1] = self._raw_valid_history[idx, 1:]
            self._raw_valid_history[idx, -1] = uv

    def _activate_raw_hold(self, idx: int, ref_uv: float):
        self._raw_hold_active[idx] = True
        self._raw_hold_reference_uv[idx] = ref_uv
        self._raw_recovery_good_count[idx] = 0

    def _invalidate_plot_cache(self, include_functions: bool = True):
        self._plot_dirty = True
        if include_functions:
            self._function_data_dirty = True
            self.function_signal_cache.clear()
            self.function_channel_cache.clear()
            self.function_fft_cache.clear()

    def _rebuild_display_buffers(self):
        self.display_channel_data.fill(0.0)
        self._reset_visual_filter_state()
        for sample_idx in range(self.data_len):
            for ch_idx in range(self.num_channels):
                raw_uv = float(self.channel_data[ch_idx, sample_idx])
                self.display_channel_data[ch_idx, sample_idx] = self._compute_display_sample(ch_idx, raw_uv)
        self._invalidate_plot_cache(include_functions=True)

    def set_visual_notch_enabled(self, state):
        self.notch_50hz_enabled = bool(state)
        self._rebuild_display_buffers()

    def _append_frame_values(self, frame_values, active_channels: int):
        """Inserta una muestra por canal activo manteniendo ventana deslizante."""
        active_channels = max(0, min(active_channels, self.num_channels))
        if active_channels == 0:
            return
        self.channel_data[:active_channels, :-1] = self.channel_data[:active_channels, 1:]
        self.display_channel_data[:active_channels, :-1] = self.display_channel_data[:active_channels, 1:]
        self.channel_data[:active_channels, -1] = frame_values[:active_channels]
        for idx in range(active_channels):
            self.display_channel_data[idx, -1] = self._compute_display_sample(idx, float(frame_values[idx]))
        self._invalidate_plot_cache(include_functions=True)

    def _repeat_last_frame(self, active_channels: int, repeat_count: int):
        """Rellena huecos repitiendo la ultima muestra valida para evitar picos artificiales."""
        active_channels = max(0, min(active_channels, self.num_channels))
        if active_channels == 0 or repeat_count <= 0:
            return
        last_values = self.channel_data[:active_channels, -1].copy()
        for _ in range(repeat_count):
            self._append_frame_values(last_values, active_channels)

    def _log_saturated_packet(self, data: bytes, pkt_idx: int, num_dev: int, count_per: int, events):
        """Vuelca el paquete crudo original cuando se detectan muestras saturadas."""
        if not data or not events:
            return
        summary = ", ".join(
            f"s{k}/D{d+1}-CH{ch+1}=packed:{packed}"
            for (k, d, ch, packed) in events[:8]
        )
        if len(events) > 8:
            summary += f", +{len(events) - 8} mas"
        self.append_to_console(
            f"PKT SAT idx={pkt_idx} devs={num_dev} count={count_per} bytes={len(data)} eventos={len(events)} :: {summary}"
        )
        hex_dump = data.hex(" ")
        chunk_size = 96 * 3
        for start in range(0, len(hex_dump), chunk_size):
            self.append_to_console(f"PKT HEX {hex_dump[start:start + chunk_size]}")

    def _decode_sample_uv(self, idx: int, val_packed: int, step_uv: float) -> float:
        """Convierte int16 empaquetado a uV y descarta muestras saturadas o con salto imposible."""
        if not self.gui_raw_guard_enabled:
            uv = float(val_packed) * step_uv
            self._push_raw_valid_uv(idx, uv)
            self._raw_last_valid_uv[idx] = uv
            self._raw_valid_initialized[idx] = True
            self._raw_hold_active[idx] = False
            self._raw_recovery_good_count[idx] = 0
            return uv

        ref_uv = self._raw_hold_reference_uv[idx] if self._raw_hold_active[idx] else self._raw_reference_uv(idx)
        if abs(int(val_packed)) >= self.packed_saturation_threshold:
            self.saturated_samples += 1
            now = time.monotonic()
            if now - self._last_saturation_log >= 1.0:
                self._last_saturation_log = now
                self.append_to_console(
                    f"Muestras saturadas o casi saturadas detectadas: {self.saturated_samples}. "
                    "Se conserva una referencia robusta del canal para evitar picos."
                )
            if self._raw_valid_initialized[idx]:
                self._activate_raw_hold(idx, ref_uv)
                return ref_uv
            return 0.0
        uv = float(val_packed) * step_uv
        if self._raw_valid_initialized[idx]:
            if abs(uv - ref_uv) >= self.raw_spike_threshold_uv:
                self.raw_spike_rejections += 1
                now = time.monotonic()
                if now - self._last_raw_spike_log >= 1.0:
                    self._last_raw_spike_log = now
                    self.append_to_console(
                        f"Saltos crudos descartados: {self.raw_spike_rejections}. "
                        "Se conserva una referencia robusta del canal antes de graficar."
                    )
                self._activate_raw_hold(idx, ref_uv)
                return ref_uv
        if self._raw_hold_active[idx]:
            if abs(uv - self._raw_hold_reference_uv[idx]) <= self.raw_recovery_threshold_uv:
                self._raw_recovery_good_count[idx] += 1
                if self._raw_recovery_good_count[idx] < self.raw_recovery_required:
                    return float(self._raw_hold_reference_uv[idx])
                self._raw_hold_active[idx] = False
                self._raw_recovery_good_count[idx] = 0
            else:
                self._raw_recovery_good_count[idx] = 0
                return float(self._raw_hold_reference_uv[idx])
        self._push_raw_valid_uv(idx, uv)
        self._raw_last_valid_uv[idx] = uv
        self._raw_valid_initialized[idx] = True
        return uv

    def _apply_visual_despike(self, signal: np.ndarray) -> np.ndarray:
        """Suprime picos aislados de una sola muestra solo en la visualizacion."""
        if signal.size < 3:
            return signal

        x = signal.astype(float, copy=False)
        diff = np.abs(np.diff(x))
        if diff.size == 0:
            return x

        base = float(np.median(diff))
        spike_threshold = max(20000.0, base * 12.0)
        neighbor_threshold = max(5000.0, spike_threshold * 0.2)

        y = x.copy()
        for n in range(1, x.size - 1):
            prev_v = x[n - 1]
            cur_v = x[n]
            next_v = x[n + 1]
            if (
                abs(cur_v - prev_v) >= spike_threshold
                and abs(cur_v - next_v) >= spike_threshold
                and abs(prev_v - next_v) <= neighbor_threshold
            ):
                y[n] = 0.5 * (prev_v + next_v)
        return y

    def _apply_visual_notch_50hz(self, signal: np.ndarray) -> np.ndarray:
        """Aplica un notch de 50 Hz solo para la visualizacion."""
        fs_hz = float(self.samples_per_second)
        if signal.size < 3 or fs_hz <= 0.0 or fs_hz <= 100.0:
            return signal

        w0 = 2.0 * np.pi * 50.0 / fs_hz
        if w0 <= 0.0 or w0 >= np.pi:
            return signal

        q = 20.0
        alpha = np.sin(w0) / (2.0 * q)
        cos_w0 = np.cos(w0)
        a0 = 1.0 + alpha
        b0 = 1.0 / a0
        b1 = (-2.0 * cos_w0) / a0
        b2 = 1.0 / a0
        a1 = (-2.0 * cos_w0) / a0
        a2 = (1.0 - alpha) / a0

        x = signal.astype(float, copy=False)
        y = np.empty_like(x)
        y[0] = b0 * x[0]
        y[1] = b0 * x[1] + b1 * x[0] - a1 * y[0]
        for n in range(2, x.size):
            y[n] = (
                b0 * x[n]
                + b1 * x[n - 1]
                + b2 * x[n - 2]
                - a1 * y[n - 1]
                - a2 * y[n - 2]
            )
        return y

    def _prepare_plot_data(self, signal: np.ndarray) -> np.ndarray:
        """Prepara la serie a dibujar sin tocar el buffer crudo."""
        plot_data = self._apply_visual_despike(signal)
        if self.notch_50hz_enabled:
            plot_data = self._apply_visual_notch_50hz(plot_data)
        return plot_data

    def stop_data_listener(self):
        if self.data_socket:
            close_socket(self.data_socket)
            self.data_socket = None
        self.data_timer.stop()
        self._update_idle_battery_polling(force_refresh=True)
        if not self.timer.isActive():
            self._set_runtime_status(streaming="En pausa")

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
                        missing_packets = (pkt_idx - expected_idx) % 256
                        self.lost_packets += missing_packets
                        self._repeat_last_frame(num_dev * CHANNELS_PER_DEVICE, missing_packets * count_per)
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
                saturated_events = []
                for k in range(count_per):
                    row_values = []
                    frame_values = np.zeros(num_dev * CHANNELS_PER_DEVICE, dtype=float)
                    for d in range(num_dev):
                        for ch in range(CHANNELS_PER_DEVICE):
                            val_packed = int.from_bytes(payload[offset:offset+2], byteorder="little", signed=True)
                            offset += 2
                            idx = d * CHANNELS_PER_DEVICE + ch
                            step_uv = steps_uv[idx] if idx < len(steps_uv) else default_step
                            if abs(int(val_packed)) >= self.packed_saturation_threshold:
                                saturated_events.append((k, d, ch, int(val_packed)))
                            uv = self._decode_sample_uv(idx, val_packed, step_uv)
                            frame_values[idx] = uv
                            if writer and self.console_mgr:
                                row_values.append(uv)
                    self._append_frame_values(frame_values, num_dev * CHANNELS_PER_DEVICE)
                    if writer and row_values and self.console_mgr:
                        writer.writerow([self.console_mgr.log_sample_id] + row_values)
                        self.console_mgr.log_sample_id += 1
                if saturated_events:
                    self._log_saturated_packet(data, pkt_idx, num_dev, count_per, saturated_events)
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
        self.battery_poll_timer.stop()
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
            self.device_wifi_state = None
            self.device_sta_ip = None
            self.device_ap_ip = None
            self._set_runtime_status(connection=f"Discovery UDP {PC_UDP_PORT}", hint=f"Buscando dispositivo en UDP {PC_UDP_PORT}...", connection_state="warning")
        except Exception as exc:
            self.discovery_socket = None
            self.append_to_console(f"No se pudo abrir UDP {PC_UDP_PORT}: {exc}")
            self.label_serial_status.setText(f"Error UDP: {exc}")
            self._set_runtime_status(connection="Error UDP", hint=f"Error UDP: {exc}", connection_state="danger")

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
                    data, addr = self.discovery_socket.recvfrom(4096)
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
                    batt_value = None
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
                        if len(parts) >= 3 and parts[0] == "CFG" and parts[1] == "BATT":
                            try:
                                batt_value = int(parts[2])
                            except ValueError:
                                batt_value = None
                        self._apply_snapshot_wifi_line(parts)
                        if line.startswith("DEV "):
                            self._update_device_gains_from_snapshot_line(line)
                    if fs_hz:
                        self.set_sampling_rate(fs_hz)
                        self.append_to_console(f"FS recibida: {fs_hz} Hz")
                    if num_dev is not None:
                        self.apply_device_availability(num_dev)
                    if batt_value is not None:
                        self.update_battery_indicator(batt_value)
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
                self.btn_send_wifi.setEnabled(True)
                self.btn_scan_wifi.setEnabled(True)
                self._apply_connection_status()
                self._update_idle_battery_polling()
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
            self.stop_discovery(refresh_status=False)
            return
        # Timeout esperando snapshot
        if self.awaiting_config and self.config_deadline and time.monotonic() > self.config_deadline:
            self.awaiting_config = False
            self.stop_discovery()
        if self.discovery_deadline and time.monotonic() > self.discovery_deadline:
            self.append_to_console("No se encontró el dispositivo (timeout).")
            self.label_serial_status.setText("Sin respuesta")
            self._set_runtime_status(connection="Sin respuesta", hint="No se encontro el dispositivo.", connection_state="danger")
            self.stop_discovery(refresh_status=False)

    def stop_discovery(self, refresh_status: bool = True):
        if self.discovery_socket:
            close_socket(self.discovery_socket)
            self.discovery_socket = None
        self.discovery_timer.stop()
        self.discovery_deadline = None
        self.awaiting_config = False
        self.config_deadline = None
        if refresh_status and self.device_ip:
            self._apply_connection_status()
            self._update_idle_battery_polling(force_refresh=True)
    # --- Métodos de Ploteo y Control ---

    def create_plots(self):
        """Crea un PlotWidget por dispositivo y curvas por canal."""
        self.device_plots = []
        self.device_curves = []
        self.channel_curves = {}

        for d in range(NUM_DEVICES):
            plot = pg.PlotWidget(name=f'Device{d+1}')
            plot.setLabel('left', f'Disp {d+1} (uV)')
            plot.setLabel('bottom', 'Tiempo (s)')
            plot.setBackground('#1e1e1e')
            plot.showGrid(x=True, y=True, alpha=0.15)
            axis_y = plot.plotItem.getAxis('left')
            if axis_y:
                axis_y.enableAutoSIPrefix(False)
                axis_y.setLabel(text=f'Disp {d+1} (uV)')
                axis_y.setLogMode(False)
                axis_y.setTickFont(QFont('Consolas', 9))
            plot.setMenuEnabled(False)
            plot.setMouseEnabled(x=False, y=False)
            plot.plotItem.vb.setMouseEnabled(x=False, y=False)
            plot.scene().sigMouseClicked.connect(
                lambda event, plot_ref=plot, device_idx=d: self._handle_measurement_click(plot_ref, device_idx, event)
            )

            curves = {}
            for ch in range(CHANNELS_PER_DEVICE):
                idx = d * CHANNELS_PER_DEVICE + ch
                curve = plot.plot(
                    pen={'color': self.channel_colors[idx], 'width': 2},
                    name=self.channel_names[idx]
                )
                try:
                    curve.setClipToView(True)
                    curve.setDownsampling(auto=True, method='peak')
                    curve.setSkipFiniteCheck(True)
                except Exception:
                    pass
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
        if not self._plot_dirty:
            return
        for i in range(self.num_channels):
            curve = self.channel_curves.get(i)
            device_idx = i // CHANNELS_PER_DEVICE
            if curve and curve.isVisible() and 0 <= device_idx < len(self.device_enabled) and self.device_enabled[device_idx]:
                curve.setData(self.data_x, self.display_channel_data[i])
        self._update_channel_function_curves()
        self._plot_dirty = False

    def _measurement_pen(self, marker_key: str, hover: bool = False):
        base_colors = {
            "v1": "#ff6b6b" if self.is_dark_theme else "#c92a2a",
            "v2": "#ffa94d" if self.is_dark_theme else "#e67700",
            "h1": "#4dabf7" if self.is_dark_theme else "#1864ab",
            "h2": "#69db7c" if self.is_dark_theme else "#2b8a3e",
        }
        width = 2.0 if hover else 1.5
        return pg.mkPen(base_colors.get(marker_key, "#e5c07b"), width=width, style=Qt.DashLine)

    def _refresh_measurement_buttons(self):
        for marker_key, button in self.measurement_cursor_buttons.items():
            is_active = True
            button.setEnabled(True)
            button.blockSignals(True)
            button.setChecked(marker_key == self.measurement_target)
            button.blockSignals(False)
            self._set_button_variant(
                button,
                "primary" if marker_key == self.measurement_target else "ghost",
                active="true" if marker_key == self.measurement_target else "false",
            )

    def _refresh_measurement_lines(self):
        for marker_key, marker in self.measurement_markers.items():
            item = marker.get("line")
            if item:
                item.setPen(self._measurement_pen(marker_key, hover=False))
                item.setHoverPen(self._measurement_pen(marker_key, hover=True))
                if getattr(item, "label", None):
                    item.label.setColor(self._measurement_pen(marker_key, hover=False).color())

    def _has_active_measurement(self) -> bool:
        return any(
            marker.get("line") is not None
            for marker in self.measurement_markers.values()
        )

    def select_measurement_target(self, marker_key: str, checked: bool):
        if checked:
            self.measurement_target = marker_key
        elif self.measurement_target == marker_key:
            self.measurement_target = None
        self._refresh_measurement_buttons()
        self._update_measurement_label()

    def clear_measurements(self, _checked: bool = False):
        plot = self.measurement_active_plot
        for marker_key in ("v1", "v2", "h1", "h2"):
            marker = self.measurement_markers[marker_key]
            item = marker.get("line")
            if plot and item:
                try:
                    plot.removeItem(item)
                except Exception:
                    pass
            marker["line"] = None
            marker["value"] = None
        self.measurement_active_plot = None
        self.measurement_active_device = None
        self._update_measurement_label()

    def _activate_measurement_plot(self, plot: pg.PlotWidget, device_index: int):
        if self.measurement_active_plot is plot:
            self.measurement_active_device = device_index
            return
        self.clear_measurements()
        self.measurement_active_plot = plot
        self.measurement_active_device = device_index

    def _handle_measurement_click(self, plot: pg.PlotWidget, device_index: int, event):
        if not plot.plotItem.vb.sceneBoundingRect().contains(event.scenePos()):
            return
        if event.button() == Qt.RightButton:
            self.clear_measurements()
            event.accept()
            return
        if not self.measurement_target:
            self._update_measurement_label()
            return
        if event.button() != Qt.LeftButton:
            return
        mouse_point = plot.plotItem.vb.mapSceneToView(event.scenePos())
        self._activate_measurement_plot(plot, device_index)
        value = mouse_point.x() if self.measurement_markers[self.measurement_target]["angle"] == 90 else mouse_point.y()
        self._set_measurement_marker(self.measurement_target, value)
        event.accept()

    def _set_measurement_marker(self, marker_key: str, value: float):
        plot = self.measurement_active_plot
        if not plot:
            return
        marker = self.measurement_markers[marker_key]
        item = marker.get("line")
        if item:
            try:
                plot.removeItem(item)
            except Exception:
                pass

        marker_color = self._measurement_pen(marker_key, hover=False).color()
        line = pg.InfiniteLine(
            pos=float(value),
            angle=marker["angle"],
            movable=True,
            pen=self._measurement_pen(marker_key, hover=False),
            hoverPen=self._measurement_pen(marker_key, hover=True),
            label=marker_key.upper(),
            labelOpts={"position": 0.92 if marker["angle"] == 90 else 0.08, "color": marker_color},
        )
        line.setZValue(20)
        line.sigPositionChanged.connect(self._on_measurement_line_changed)
        plot.addItem(line)

        marker["line"] = line
        marker["value"] = float(value)
        self._update_measurement_label()

    def _on_measurement_line_changed(self):
        for marker in self.measurement_markers.values():
            line = marker.get("line")
            if line:
                marker["value"] = float(line.value())
        self._update_measurement_label()

    def _format_measurement_time(self, seconds: float) -> str:
        magnitude = abs(seconds)
        if magnitude < 1.0:
            return f"{seconds * 1000:.1f} ms"
        return f"{seconds:.3f} s"

    def _refresh_measurement_label_width(self):
        if not self.measurement_label:
            return
        text = self.measurement_label.text() or ""
        lines = text.splitlines() or [text]
        fm = self.measurement_label.fontMetrics()
        width = max((fm.horizontalAdvance(line) for line in lines), default=0)
        self.measurement_label.setFixedWidth(max(80, width + 8))

    def _update_measurement_label(self):
        if not self.measurement_label:
            return
        delta_t_text = self._measurement_delta_t_text()
        delta_v_text = self._measurement_delta_v_text()
        self.measurement_label.setText(
            f"V2 - V1 = {delta_t_text}, {self._measurement_frequency_text()}\n"
            f"H2 - H1 = {delta_v_text}"
        )
        self._refresh_measurement_label_width()

    def _measurement_delta_v_text(self) -> str:
        h1 = self.measurement_markers["h1"]["value"]
        h2 = self.measurement_markers["h2"]["value"]
        if h1 is None or h2 is None:
            return "--"
        return f"{(h2 - h1):.1f} uV"

    def _measurement_delta_t_text(self) -> str:
        v1 = self.measurement_markers["v1"]["value"]
        v2 = self.measurement_markers["v2"]["value"]
        if v1 is None or v2 is None:
            return "--"
        return self._format_measurement_time(v2 - v1)

    def _measurement_frequency_text(self) -> str:
        v1 = self.measurement_markers["v1"]["value"]
        v2 = self.measurement_markers["v2"]["value"]
        if v1 is None or v2 is None:
            return "--"
        delta_t = abs(v2 - v1)
        if delta_t <= 1e-9:
            return "--"
        return f"{1.0 / delta_t:.2f} Hz"

    def _channel_display_name(self, channel_index: int) -> str:
        device_idx = channel_index // CHANNELS_PER_DEVICE
        channel_idx = channel_index % CHANNELS_PER_DEVICE
        if self.available_devices <= 1:
            return f"CH{channel_idx + 1}"
        return f"D{device_idx + 1}-CH{channel_idx + 1}"

    def _function_label_from_key(self, function_key: str) -> str:
        for function_def in self.function_definitions:
            if function_def["key"] == function_key:
                return function_def["label"]
        return function_key.upper()

    def _function_curve_pen(self, entry):
        return pg.mkPen(entry["display_color"], width=2, style=Qt.DashLine)

    def _color_rgba(self, color_hex: str, alpha: int) -> str:
        color = QColor(color_hex)
        return f"rgba({color.red()}, {color.green()}, {color.blue()}, {max(0, min(alpha, 255))})"

    def _refresh_function_channel_combo(self):
        if self.function_channel_combo is None:
            return
        previous_value = self.function_channel_combo.currentData()
        self.function_channel_combo.blockSignals(True)
        self.function_channel_combo.clear()
        active_devices = self.available_devices if self.available_devices > 0 else 1
        active_channels = active_devices * CHANNELS_PER_DEVICE
        for idx in range(active_channels):
            self.function_channel_combo.addItem(self._channel_display_name(idx), idx)
        self.function_channel_combo.blockSignals(False)
        if previous_value is not None:
            selected_index = self.function_channel_combo.findData(previous_value)
            if selected_index >= 0:
                self.function_channel_combo.setCurrentIndex(selected_index)
        if self.function_channel_combo.count() > 0 and self.function_channel_combo.currentIndex() < 0:
            self.function_channel_combo.setCurrentIndex(0)
        enabled = self.function_channel_combo.count() > 0
        if self.function_type_combo is not None:
            self.function_type_combo.setEnabled(enabled)
        self.function_channel_combo.setEnabled(enabled)
        if self.btn_function_add is not None:
            self.btn_function_add.setEnabled(enabled)

    def _clear_function_list_widgets(self):
        if self.function_list_layout is None:
            return
        while self.function_list_layout.count():
            item = self.function_list_layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget:
                widget.deleteLater()
            elif child_layout:
                while child_layout.count():
                    child_item = child_layout.takeAt(0)
                    child_widget = child_item.widget()
                    if child_widget:
                        child_widget.deleteLater()

    def _render_channel_functions(self):
        if self.function_list_layout is None:
            return
        self._clear_function_list_widgets()
        self.function_widgets = {}
        if not self.active_channel_functions:
            self.function_empty_label = QLabel("Aun no hay funciones agregadas.")
            self.function_empty_label.setProperty("role", "sectionHint")
            self.function_empty_label.setWordWrap(True)
            self.function_list_layout.addWidget(self.function_empty_label)
            self.function_list_layout.addStretch(1)
            return

        for entry in self.active_channel_functions:
            entry_color = entry["display_color"]
            row = QWidget()
            row.setStyleSheet(
                "QWidget {"
                f"background-color: {self._color_rgba(entry_color, 28)};"
                f"border: 1px solid {self._color_rgba(entry_color, 110)};"
                "border-radius: 8px;"
                "}"
            )
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 6, 8, 6)
            row_layout.setSpacing(8)

            swatch = QLabel()
            swatch.setFixedSize(14, 14)
            swatch.setStyleSheet(
                f"background-color: {entry_color}; border-radius: 7px; border: none;"
            )

            label = QLabel(
                f"{self._function_label_from_key(entry['function_key'])} - "
                f"{self._channel_display_name(entry['channel_index'])}"
            )
            label.setStyleSheet(
                f"color: {entry_color}; font-weight: 700; background: transparent; border: none;"
            )

            btn_remove = QPushButton("✕")
            btn_remove.setAutoDefault(False)
            btn_remove.setDefault(False)
            btn_remove.setFocusPolicy(Qt.NoFocus)
            btn_remove.setFixedSize(24, 24)
            btn_remove.clicked.connect(
                lambda _checked=False, entry_id=entry["id"]: self.remove_channel_function(entry_id)
            )
            self._set_button_variant(btn_remove, "danger")
            btn_remove.setText("X")
            btn_remove.setStyleSheet(
                "QPushButton {"
                "background-color: transparent;"
                f"color: {entry_color};"
                f"border: 1px solid {self._color_rgba(entry_color, 150)};"
                "border-radius: 12px;"
                "font-weight: 700;"
                "padding: 0px;"
                "}"
                "QPushButton:hover {"
                f"background-color: {self._color_rgba(entry_color, 46)};"
                "}"
                "QPushButton:pressed {"
                f"background-color: {self._color_rgba(entry_color, 72)};"
                "}"
            )

            row_layout.addWidget(swatch)
            row_layout.addWidget(label, 1)
            row_layout.addWidget(btn_remove)
            self.function_list_layout.addWidget(row)
            self.function_widgets[entry["id"]] = row

        self.function_list_layout.addStretch(1)
        self._invalidate_plot_cache(include_functions=False)

    def add_channel_function(self):
        if self.function_type_combo is None or self.function_channel_combo is None:
            return
        function_key = self.function_type_combo.currentData()
        channel_index = self.function_channel_combo.currentData()
        if function_key is None or channel_index is None:
            return
        channel_index = int(channel_index)
        for entry in self.active_channel_functions:
            if entry["function_key"] == function_key and entry["channel_index"] == channel_index:
                self.append_to_console("La funcion seleccionada ya fue agregada para ese canal.")
                return
        entry = {
            "id": self._next_function_entry_id,
            "function_key": str(function_key),
            "channel_index": channel_index,
            "display_color": self.function_colors[(self._next_function_entry_id - 1) % len(self.function_colors)],
        }
        self._next_function_entry_id += 1
        self.active_channel_functions.append(entry)
        self._function_data_dirty = True
        self._ensure_channel_function_curve(entry)
        self._render_channel_functions()
        self.append_to_console(
            f"Funcion agregada: {self._function_label_from_key(entry['function_key'])} en {self._channel_display_name(entry['channel_index'])}."
        )

    def remove_channel_function(self, entry_id: int):
        for idx, entry in enumerate(self.active_channel_functions):
            if entry["id"] != entry_id:
                continue
            self.active_channel_functions.pop(idx)
            curve = self.function_curves.pop(entry_id, None)
            if curve:
                device_idx = entry["channel_index"] // CHANNELS_PER_DEVICE
                if 0 <= device_idx < len(self.device_plots):
                    try:
                        self.device_plots[device_idx].removeItem(curve)
                    except Exception:
                        pass
            self.function_widgets.pop(entry_id, None)
            self.function_signal_cache.pop(entry_id, None)
            self._function_data_dirty = True
            self._render_channel_functions()
            self.append_to_console(
                f"Funcion eliminada: {self._function_label_from_key(entry['function_key'])} en {self._channel_display_name(entry['channel_index'])}."
            )
            return

    def _ensure_channel_function_curve(self, entry):
        entry_id = entry["id"]
        if entry_id in self.function_curves:
            return self.function_curves[entry_id]
        device_idx = entry["channel_index"] // CHANNELS_PER_DEVICE
        if device_idx < 0 or device_idx >= len(self.device_plots):
            return None
        plot = self.device_plots[device_idx]
        curve = plot.plot(
            pen=self._function_curve_pen(entry),
            name=f"{self._function_label_from_key(entry['function_key'])}-{self._channel_display_name(entry['channel_index'])}",
        )
        try:
            curve.setClipToView(True)
            curve.setDownsampling(auto=True, method='peak')
            curve.setSkipFiniteCheck(True)
        except Exception:
            pass
        self.function_curves[entry_id] = curve
        return curve

    def _moving_average(self, signal: np.ndarray, window_len: int) -> np.ndarray:
        if window_len <= 1 or signal.size == 0:
            return signal.astype(float, copy=True)
        kernel = np.ones(window_len, dtype=float) / float(window_len)
        return np.convolve(signal.astype(float, copy=False), kernel, mode="same")

    def _moving_rms(self, signal: np.ndarray, window_len: int) -> np.ndarray:
        centered = signal.astype(float, copy=False) - float(np.mean(signal))
        return np.sqrt(self._moving_average(centered * centered, window_len))

    def _bandpass_fft(self, signal: np.ndarray, low_hz: float = None, high_hz: float = None) -> np.ndarray:
        x = signal.astype(float, copy=False)
        if x.size < 4 or self.samples_per_second <= 0:
            return x.copy()
        centered = x - float(np.mean(x))
        spectrum = np.fft.rfft(centered)
        freqs = np.fft.rfftfreq(centered.size, d=1.0 / float(self.samples_per_second))
        mask = np.ones_like(freqs, dtype=bool)
        if low_hz is not None:
            mask &= freqs >= float(low_hz)
        if high_hz is not None:
            mask &= freqs < float(high_hz)
        filtered = np.fft.irfft(spectrum * mask, n=centered.size)
        return filtered.real

    def _get_centered_channel_signal(self, channel_index: int) -> np.ndarray:
        centered = self.function_channel_cache.get(channel_index)
        if centered is None:
            source = self.channel_data[channel_index].astype(float, copy=False)
            centered = source - float(np.mean(source))
            self.function_channel_cache[channel_index] = centered
        return centered

    def _get_channel_fft(self, channel_index: int):
        cached = self.function_fft_cache.get(channel_index)
        if cached is None:
            centered = self._get_centered_channel_signal(channel_index)
            spectrum = np.fft.rfft(centered)
            freqs = np.fft.rfftfreq(centered.size, d=1.0 / float(self.samples_per_second))
            cached = (centered.size, spectrum, freqs)
            self.function_fft_cache[channel_index] = cached
        return cached

    def _bandpass_fft_cached(self, channel_index: int, low_hz: float = None, high_hz: float = None) -> np.ndarray:
        signal_len, spectrum, freqs = self._get_channel_fft(channel_index)
        mask = np.ones_like(freqs, dtype=bool)
        if low_hz is not None:
            mask &= freqs >= float(low_hz)
        if high_hz is not None:
            mask &= freqs < float(high_hz)
        return np.fft.irfft(spectrum * mask, n=signal_len).real

    def _compute_channel_function_signal(self, entry) -> np.ndarray:
        cache_key = entry["id"]
        if not self._function_data_dirty and cache_key in self.function_signal_cache:
            return self.function_signal_cache[cache_key]
        channel_index = entry["channel_index"]
        if channel_index < 0 or channel_index >= self.num_channels:
            return np.zeros(self.data_len, dtype=float)
        source = self.channel_data[channel_index].astype(float, copy=False)
        function_key = entry["function_key"]
        fs_hz = max(float(self.samples_per_second), 1.0)
        if function_key == "rms":
            derived = self._moving_rms(source, max(3, int(fs_hz * 0.25)))
        elif function_key == "envelope":
            centered = self._get_centered_channel_signal(channel_index)
            derived = self._moving_average(np.abs(centered), max(3, int(fs_hz * 0.20)))
        elif function_key == "gamma":
            derived = self._bandpass_fft_cached(channel_index, low_hz=30.0, high_hz=None)
        elif function_key == "beta":
            derived = self._bandpass_fft_cached(channel_index, low_hz=13.0, high_hz=30.0)
        elif function_key == "alpha":
            derived = self._bandpass_fft_cached(channel_index, low_hz=8.0, high_hz=13.0)
        elif function_key == "theta":
            derived = self._bandpass_fft_cached(channel_index, low_hz=4.0, high_hz=8.0)
        elif function_key == "delta":
            derived = self._bandpass_fft_cached(channel_index, low_hz=None, high_hz=4.0)
        else:
            derived = source.copy()
        result = self._prepare_plot_data(np.asarray(derived, dtype=float))
        self.function_signal_cache[cache_key] = result
        return result

    def _update_channel_function_curves(self):
        for entry in self.active_channel_functions:
            curve = self._ensure_channel_function_curve(entry)
            if not curve:
                continue
            device_idx = entry["channel_index"] // CHANNELS_PER_DEVICE
            curve.setPen(self._function_curve_pen(entry))
            is_visible = 0 <= device_idx < len(self.device_plots) and self.device_enabled[device_idx]
            curve.setVisible(is_visible)
            if not is_visible:
                continue
            curve.setData(self.data_x, self._compute_channel_function_signal(entry))
        self._function_data_dirty = False

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
        self._invalidate_plot_cache(include_functions=False)

        if log_change:
            self.log_message(f"Disp {device_id + 1} Canal {channel_id + 1}: {'Activado' if enabled else 'Desactivado'}")

    def apply_device_availability(self, num_available: int):
        """Habilita/deshabilita dispositivos/plots según ADS inicializados."""
        self.available_devices = max(0, min(NUM_DEVICES, int(num_available)))
        self._refresh_config_device_selector(self.available_devices)
        self._refresh_function_channel_combo()
        for d, cb_dev in enumerate(self.device_checks):
            is_avail = d < self.available_devices
            for widget in self.device_column_widgets.get(d, []):
                widget.setVisible(is_avail)
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
        self._invalidate_plot_cache(include_functions=False)
        if self.available_devices > 0:
            if self.device_ip:
                self._set_runtime_status(device=self.device_ip)
            else:
                self._set_runtime_status(device=f"{self.available_devices} modulo(s)")
        self._update_idle_battery_polling()

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
        self._invalidate_plot_cache(include_functions=False)
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
            self._set_runtime_status(
                connection="Adquiriendo",
                streaming="En vivo",
                hint="Adquisicion en curso.",
                connection_state="success",
            )
            self._update_idle_battery_polling()
            # Limpiar gráficas antes de arrancar
            for i in range(self.num_channels):
                self.channel_data[i].fill(0)
                self.display_channel_data[i].fill(0)
            self.log_message("Simulación de señal iniciada.")

            self._reset_visual_filter_state()

    def pause_signal(self):
        """Pausa la adquisición de datos."""
        if self.timer.isActive():
            self.timer.stop()
            self.is_running = False
            self.btn_start.setEnabled(True)
            self.btn_pause.setEnabled(False)
            self._set_runtime_status(
                connection="Listo para adquirir",
                streaming="En pausa",
                hint="Adquisicion detenida.",
                connection_state="success",
            )
            self._update_idle_battery_polling(force_refresh=True)
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
        self._set_button_variant(
            self.btn_log_toggle,
            "danger",
            recording="true" if recording else "false",
        )

    def toggle_logging(self, checked: bool):
        """Botón toggle para iniciar/detener grabación a CSV."""
        self._set_button_variant(
            self.btn_log_toggle,
            "danger",
            recording="true" if checked else "false",
        )
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
            if self.console_mgr.log_path:
                self._set_runtime_status(recording=self.console_mgr.log_path, hint=f"Grabando en {self.console_mgr.log_path}")

    def stop_logging(self):
        if self.console_mgr:
            self.console_mgr.stop_logging()
        self._set_button_variant(self.btn_log_toggle, "danger", recording="false")
        self._set_runtime_status(recording="Sin archivo", hint="Grabacion detenida.")

    def closeEvent(self, event):
        """Cerrar conexiones al salir."""
        self.battery_poll_timer.stop()
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
