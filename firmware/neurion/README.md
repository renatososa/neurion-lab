# Firmware Neurion ESP32-S3 + ADS1299

Firmware de adquisicion multicanal para ESP32-S3 con soporte para hasta 4 ADS1299, configuracion persistente, streaming UDP y puntos de extension para ampliar el procesamiento sin modificar el nucleo principal.

## Caracteristicas principales

- Soporte para hasta 4 ADS1299 sobre un SPI compartido, sincronizados mediante `START`.
- Streaming UDP de bloques de muestras hacia la GUI de PC.
- Configuracion por canal y bias con persistencia en NVS.
- Indicacion de estado con LED y monitoreo de bateria por ADC.
- Hooks de usuario para agregar procesamiento propio sobre muestras decimadas.

## Archivos principales

- `neurion.ino`: punto de entrada, maquina de estados y loop principal.
- `ads1299.h/.cpp`: driver y administracion de dispositivos ADS1299.
- `wifi_comm.h/.cpp`: transporte UDP y comandos de configuracion.
- `ads_config_storage.h/.cpp`: guardado y carga de configuracion en flash.
- `filtering.h/.cpp`: decimado y procesamiento basico.
- `status_led.h/.cpp` e `icled_wurt_spi.*`: control del LED de estado.
- `battery_monitor.*`: supervision de bateria.
- `user_hooks.*`: puntos de extension para logica del usuario.
- `config_pins.h`: pines, red y parametros dependientes del hardware.

## Flujo general

1. `setup()` inicializa el LED, los ADS, los filtros y la configuracion persistente.
2. Se aplica la configuracion al hardware y se levanta la comunicacion WiFi/UDP.
3. El loop procesa comandos, actualiza estado de bateria y transmite muestras cuando corresponde.

## Comandos UDP

Los comandos ingresan por `PC_UDP_PORT` y reciben respuestas `OK ...` o `ERR ...`.

- `CONNECT`
- `CH <dev> <ch> <gain> <powerDown> <test>`
- `BIAS <dev> <senspMask> <sensnMask>`
- `BIASDRV <dev> <enable> [refInt]`
- `BIASDRV <dev> 0xHH`
- `TEST <dev> <enable> <amp> <freq>`
- `SAVE`
- `LOAD`

## Notas

- El filtrado actual es basico y sigue marcado como placeholder.
- `config_pins.h` debe ajustarse al hardware y a la red reales antes de desplegar.
- Si un ADS no responde a tiempo, el firmware registra la condicion y continua operando.
