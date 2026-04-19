import socket
import re
from typing import Dict, List, Optional, Tuple

from gui_constants import CHANNELS_PER_DEVICE, PC_UDP_PORT


def parse_dump_config(resp: str) -> Dict[int, Dict[int, int]]:
    """
    Parsea la respuesta del comando DUMP (registros CHnSET por dispositivo).
    Devuelve un dict {dev_idx: {ch_idx: reg_value}}.
    """
    cfg: Dict[int, Dict[int, int]] = {}
    lines = resp.strip().splitlines()
    current_dev: Optional[int] = None
    for line in lines:
        line = line.strip()
        if line.startswith("DEV "):
            try:
                current_dev = int(line.split()[1])
            except Exception:
                current_dev = None
            continue
        if line.startswith("CH") and current_dev is not None:
            # Soporta líneas tipo "CH 0 REG 0x61" y "CH1SET = 0x61"
            # Extraer índice de canal (1-8 en dump) y valor hex.
            ch_idx = None
            # Buscar número después de "CH"
            m = re.match(r"CH\s*([0-9]+)", line, flags=re.IGNORECASE)
            if m:
                try:
                    ch_idx = int(m.group(1))
                    # En el dump se imprime CH1SET..CH8SET, convertir a base cero
                    if "SET" in line and ch_idx > 0:
                        ch_idx -= 1
                except Exception:
                    ch_idx = None
            else:
                m = re.match(r"CH([0-9]+)SET", line, flags=re.IGNORECASE)
                if m:
                    try:
                        ch_idx = int(m.group(1)) - 1
                    except Exception:
                        ch_idx = None
            if ch_idx is None or ch_idx < 0:
                continue

            # Buscar primer token hexadecimal
            reg_val = None
            for token in line.replace("=", " ").split():
                if token.lower().startswith("0x"):
                    try:
                        reg_val = int(token, 16)
                        break
                    except Exception:
                        reg_val = None
            if reg_val is None:
                continue

            cfg.setdefault(current_dev, {})[ch_idx] = reg_val
    return cfg


def deduce_dims_from_payload(
    payload_len: int,
    max_devices: int = 4,
    channels_per_device: int = CHANNELS_PER_DEVICE,
    bytes_per_sample: int = 2,
) -> Optional[Tuple[int, int]]:
    """Intenta deducir num_dev y count_per a partir del tamaño del payload."""
    for nd in range(1, max_devices + 1):
        block = nd * channels_per_device * bytes_per_sample
        if block > 0 and payload_len % block == 0:
            return nd, payload_len // block
    return None


def build_steps_from_gains(
    num_dev: int,
    gains_snapshot: List[int],
    default_gain: int,
    pack_base_uv: float,
    channels_per_device: int = CHANNELS_PER_DEVICE,
) -> Tuple[List[float], float]:
    """Calcula el paso en uV por canal usando las ganancias conocidas."""
    default_step = pack_base_uv / float(default_gain if default_gain > 0 else 1)
    steps_uv: List[float] = []
    total_channels = num_dev * channels_per_device
    for idx in range(total_channels):
        gain_val = default_gain
        if idx < len(gains_snapshot):
            try:
                g_int = int(gains_snapshot[idx])
                if g_int > 0:
                    gain_val = g_int
            except Exception:
                pass
        steps_uv.append(pack_base_uv / float(gain_val))
    return steps_uv, default_step


def create_udp_socket(port: int = PC_UDP_PORT, blocking: bool = False) -> socket.socket:
    """Crea y bindea un socket UDP en el puerto indicado."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    sock.setblocking(blocking)
    return sock


def start_discovery_socket(port: int = PC_UDP_PORT) -> socket.socket:
    """Crea un socket UDP non-blocking para discovery en el puerto indicado."""
    return create_udp_socket(port, blocking=False)


def send_udp_command(
    device_ip: str,
    command: str,
    port: int = PC_UDP_PORT,
    timeout: float = 1.0,
    bufsize: int = 1024,
) -> Optional[str]:
    """Envía un comando UDP y devuelve la respuesta como texto (si llega)."""
    if not device_ip:
        return None
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(command.encode("utf-8", errors="ignore"), (device_ip, port))
            data, _ = sock.recvfrom(bufsize)
            return data.decode("utf-8", errors="ignore").strip()
        except Exception:
            return None


def send_udp_command_collect(
    device_ip: str,
    command: str,
    port: int = PC_UDP_PORT,
    timeout: float = 1.0,
    idle_timeout: float = 0.35,
    bufsize: int = 1024,
    max_packets: int = 64,
) -> List[str]:
    """Envía un comando UDP y recopila múltiples respuestas hasta agotar el tiempo o quedar idle."""
    if not device_ip:
        return []
    responses: List[str] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.settimeout(timeout)
            sock.sendto(command.encode("utf-8", errors="ignore"), (device_ip, port))
            while len(responses) < max_packets:
                data, _ = sock.recvfrom(bufsize)
                text = data.decode("utf-8", errors="ignore").strip()
                if text:
                    responses.append(text)
                sock.settimeout(idle_timeout)
        except Exception:
            pass
    return responses


def send_udp_bytes(
    device_ip: str,
    payload: bytes,
    port: int = PC_UDP_PORT,
) -> bool:
    """Envía bytes crudos por UDP al dispositivo (sin esperar respuesta)."""
    if not device_ip:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(payload, (device_ip, port))
        return True
    except Exception:
        return False


def close_socket(sock: Optional[socket.socket]) -> None:
    """Cierra un socket UDP ignorando errores."""
    if not sock:
        return
    try:
        sock.close()
    except Exception:
        pass


__all__ = [
    "parse_dump_config",
    "deduce_dims_from_payload",
    "build_steps_from_gains",
    "create_udp_socket",
    "start_discovery_socket",
    "send_udp_command",
    "send_udp_command_collect",
    "send_udp_bytes",
    "close_socket",
]
