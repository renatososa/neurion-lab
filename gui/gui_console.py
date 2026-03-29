import csv
import time
from typing import Optional

from PyQt5.QtCore import QDateTime, Qt
from PyQt5.QtWidgets import QPlainTextEdit, QLineEdit, QPushButton


class ConsoleManager:
    """Encapsula la consola de comandos y el manejo de logging en CSV."""

    def __init__(
        self,
        console_output: QPlainTextEdit,
        console_input: QLineEdit,
        log_toggle: Optional[QPushButton] = None,
    ):
        self.console_output = console_output
        self.console_input = console_input
        self.log_toggle = log_toggle

        # Historial de comandos
        self.command_history = []
        self.history_index = 0

        # Estado de logging a CSV
        self.log_file: Optional[object] = None
        self.log_writer: Optional[csv.writer] = None
        self.log_path: Optional[str] = None
        self.log_header_written: bool = False
        self.log_sample_id: int = 0

    def set_log_toggle(self, btn: Optional[QPushButton]):
        self.log_toggle = btn

    def log_message(self, message: str):
        """Añade un mensaje al historial con timestamp."""
        time_str = QDateTime.currentDateTime().toString("[hh:mm:ss] ")
        self.console_output.appendPlainText(time_str + message)
        self.console_output.verticalScrollBar().setValue(self.console_output.verticalScrollBar().maximum())

    def append_to_console(self, text: str, is_command: bool = False):
        """Añade texto formateado a la consola."""
        time_str = QDateTime.currentDateTime().toString("[hh:mm:ss] ")
        if is_command:
            self.console_output.appendHtml(f"<span style='color: #e5c07b;'>{time_str} > {text}</span>")
        else:
            self.console_output.appendPlainText(time_str + text)
        self.console_output.verticalScrollBar().setValue(self.console_output.verticalScrollBar().maximum())

    def clear_console(self):
        self.console_output.clear()

    # --- Historial de comandos ---
    def record_command(self, cmd: str):
        self.command_history.append(cmd)
        self.history_index = len(self.command_history)

    def handle_history_key(self, event) -> bool:
        """Maneja flechas arriba/abajo cuando el foco está en la consola."""
        if not self.console_input.hasFocus():
            return False
        if event.key() == Qt.Key_Up and self.command_history:
            self.history_index = max(0, self.history_index - 1)
            self.console_input.setText(self.command_history[self.history_index])
            self.console_input.end(False)
            return True
        if event.key() == Qt.Key_Down and self.command_history:
            self.history_index = min(len(self.command_history), self.history_index + 1)
            if self.history_index == len(self.command_history):
                self.console_input.clear()
            else:
                self.console_input.setText(self.command_history[self.history_index])
                self.console_input.end(False)
            return True
        return False

    # --- Logging a CSV ---
    def _set_log_toggle_state(self, recording: bool):
        if not self.log_toggle:
            return
        self.log_toggle.blockSignals(True)
        self.log_toggle.setChecked(recording)
        self.log_toggle.setText("Grabando..." if recording else "Grabar")
        self.log_toggle.blockSignals(False)

    def start_logging(self, path: Optional[str] = None) -> bool:
        """Abre un archivo CSV para guardar muestras recibidas."""
        if self.log_file:
            self.stop_logging()
        if not path:
            path = f"udp_log_{int(time.time())}.csv"
        try:
            f = open(path, "w", newline="", encoding="utf-8")
            writer = csv.writer(f)
            self.log_file = f
            self.log_writer = writer
            self.log_path = path
            self.log_header_written = False
            self.log_sample_id = 0
            self.append_to_console(f"Logging iniciado en {path}")
            self._set_log_toggle_state(True)
            return True
        except Exception as exc:
            self.append_to_console(f"No se pudo abrir log: {exc}")
            self._set_log_toggle_state(False)
            return False

    def stop_logging(self):
        if self.log_file:
            try:
                self.log_file.close()
                self.append_to_console(f"Logging cerrado ({self.log_path})")
            except Exception as exc:
                self.append_to_console(f"Error al cerrar log: {exc}")
        self.log_file = None
        self.log_writer = None
        self.log_path = None
        self.log_header_written = False
        self._set_log_toggle_state(False)
