# Neurion LAB

Neurion LAB es una plataforma abierta y modular de adquisicion de biopotenciales orientada a educacion, prototipado y experimentacion. Este repositorio agrupa el firmware embebido, la GUI de escritorio, los activos de hardware y la documentacion de soporte del prototipo actual.

## Estructura del repositorio

- `firmware/neurion/`: sketch y modulos del firmware de adquisicion basado en ESP32-S3 y ADS1299
- `gui/`: aplicacion de escritorio para configuracion, monitoreo y captura de datos
- `hardware/pcb/`: archivos de diseno de PCB y activos de manufactura
- `hardware/carcasa/`: archivos de carcasa, renders y modelos imprimibles
- `docs/`: documentacion del proyecto e imagenes de apoyo

## Estado actual

El prototipo actual incluye:

- electronica modular propia
- firmware para ESP32-S3
- adquisicion de biopotenciales basada en ADS1299
- GUI de escritorio para configuracion y visualizacion de senales en vivo
- validacion experimental con ECG

## Licencias

- Software en `firmware/neurion/` y `gui/`: MIT. Ver `LICENSE` y `LICENSE-software`.
- Disenos de hardware en `hardware/`: CERN-OHL-S v2. Ver `LICENSE-hardware`.
- Documentacion en `docs/`: CC BY 4.0. Ver `LICENSE-docs`.
