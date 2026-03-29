# Neurion GUI

Aplicacion de escritorio en Python para descubrir el dispositivo, configurar parametros del ADS1299, visualizar senales en vivo y registrar datos recibidos por UDP.

## Archivos principales

- `main_gui.py`: ventana principal, graficas y flujo de la aplicacion.
- `gui_comm.py`: sockets UDP, parsing y utilidades de comunicacion.
- `gui_console.py`: consola integrada y manejo de mensajes.
- `gui_constants.py`: constantes compartidas entre modulos.
- `gui_styles.py`: estilos, iconos y apariencia de la interfaz.
- `pc_client.py`: cliente liviano para pruebas de comunicacion.
- `NeurionLab_CDC.inf`: archivo INF para el dispositivo CDC en Windows.

## Dependencias

- Python 3
- `PyQt5`
- `pyqtgraph`
- `numpy`
- `pyserial` opcional para funciones seriales

## Ejecucion

```bash
python main_gui.py
```

## Capacidades actuales

- descubrimiento del equipo por red local
- recepcion de tramas UDP con datos en vivo
- visualizacion multicanal y control de ventana temporal
- envio de comandos de configuracion al firmware
- consola para diagnostico y mensajes de estado
