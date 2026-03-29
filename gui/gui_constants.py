"""Constantes compartidas para la GUI y clientes Neurion."""

# Puerto UDP usado para discovery/comandos y streaming
PC_UDP_PORT = 5000

# Referencia ADS1299 (V) y ganancia asumida por defecto
ADS_VREF = 4.5
ADS_GAIN_DEFAULT = 24

# Paso base en uV; el LSB transmitido es PACK_BASE_UV / ganancia
PACK_BASE_UV = 24.0

# Parámetros de ventana y refresco de ploteo
WINDOW_MIN_SEC = 1
WINDOW_MAX_SEC = 15
PLOT_UPDATE_MS = 50

# Topología de dispositivos/canales
NUM_DEVICES = 4
CHANNELS_PER_DEVICE = 8

# Permite recibir paquetes grandes cuando NET_BLOCK_SAMPLES es alto en el firmware
NET_BLOCK_SAMPLES_MAX = 10
# 4 bytes de header + muestras (devices * canales * 2 bytes * samples) + margen
UDP_RECV_BUFSIZE = 4 + (NUM_DEVICES * CHANNELS_PER_DEVICE * 2 * NET_BLOCK_SAMPLES_MAX) + 512

__all__ = [
    "PC_UDP_PORT",
    "ADS_VREF",
    "ADS_GAIN_DEFAULT",
    "PACK_BASE_UV",
    "WINDOW_MIN_SEC",
    "WINDOW_MAX_SEC",
    "PLOT_UPDATE_MS",
    "NUM_DEVICES",
    "CHANNELS_PER_DEVICE",
    "NET_BLOCK_SAMPLES_MAX",
    "UDP_RECV_BUFSIZE",
]
