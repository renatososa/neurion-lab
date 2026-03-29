"""
Cliente básico UDP para Neurion (ESP32-AP).

Funcionalidad:
- Envía CONNECT para registrar la IP/puerto como destino de streaming.
- Permite enviar comandos por consola (CH/BIAS/TEST/SAVE/LOAD).
- Recibe muestras en uV empaquetadas como int16 y muestra info mínima (contador, primer canal).
- Resolución depende de la ganancia: LSB = 24/gain uV (usa --gain para el primer canal).

Uso:
    python pc_client.py --esp-ip 192.168.4.1 --port 5000 [--gain 24]
Conectarse primero al AP del ESP32 (SSID/PASS definidos en config_pins.h).
"""

import argparse
import socket
import struct
import threading
import time

from gui_constants import PACK_BASE_UV, PC_UDP_PORT


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--esp-ip", default="192.168.4.1", help="IP del ESP32 (AP)")
    p.add_argument("--port", type=int, default=PC_UDP_PORT, help="Puerto UDP (PC_UDP_PORT)")
    p.add_argument("--bind", default="0.0.0.0", help="IP local para bind")
    p.add_argument("--gain", type=int, default=24, help="Ganancia esperada del canal (LSB = 24/gain uV)")
    return p.parse_args()


def send_command(sock: socket.socket, dest, cmd: str):
    data = cmd.encode("ascii")
    sock.sendto(data, dest)


def recv_loop(sock: socket.socket, stop_event: threading.Event, step_uv: float):
    packet_count = 0
    while not stop_event.is_set():
        try:
            sock.settimeout(1.0)
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue
        if not data:
            continue
        # Diferenciar respuesta de comando (texto) vs. datos binarios
        if data[0] in (ord("O"), ord("E")) and b" " in data[:7]:
            # Probable "OK ..." o "ERR ..."
            print(f"[CMD RESP] {data.decode(errors='ignore').strip()}")
            continue
        if len(data) < 2:
            continue
        num_dev = data[0]
        count_per = data[1]
        payload = data[2:]
        expected = num_dev * count_per * 8 * 2
        if len(payload) < expected:
            print(f"[WARN] Paquete corto: len={len(payload)} esperado={expected}")
            continue
        # Solo mostramos la primera muestra del primer dispositivo
        first_val = struct.unpack_from("<h", payload, 0)[0]
        first_uv = first_val * step_uv
        packet_count += 1
        print(f"[DATA] pkt#{packet_count} devs={num_dev} count={count_per} ch0={first_val} -> {first_uv:.3f} uV (step {step_uv:.3f} uV/LSB)")


def main():
    args = parse_args()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    dest = (args.esp_ip, args.port)
    gain = args.gain if args.gain > 0 else 24
    step_uv = PACK_BASE_UV / float(gain)

    # Registrar destino
    print(f"Enviando CONNECT a {dest} ...")
    send_command(sock, dest, "CONNECT")

    stop_event = threading.Event()
    t = threading.Thread(target=recv_loop, args=(sock, stop_event, step_uv), daemon=True)
    t.start()

    try:
        while True:
            cmd = input("cmd> ").strip()
            if not cmd:
                continue
            if cmd.lower() in ("quit", "exit"):
                break
            send_command(sock, dest, cmd)
    finally:
        stop_event.set()
        t.join(timeout=1.0)
        sock.close()


if __name__ == "__main__":
    main()
