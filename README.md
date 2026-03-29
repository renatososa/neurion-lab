# Neurion LAB

Neurion LAB is an open, modular biopotential acquisition platform for education, prototyping, and experimentation. This repository groups the embedded firmware, desktop GUI, hardware assets, and supporting documentation used by the current prototype.

## Repository structure

- `firmware/`: ESP32-S3 and ADS1299 acquisition firmware
- `gui/`: desktop GUI for configuration, monitoring, and data capture
- `hardware/pcb/`: PCB design files and manufacturing assets
- `hardware/carcasa/`: enclosure files, renders, and printable models
- `docs/`: project documentation and supporting images

## Current status

The current prototype includes:

- custom modular electronics
- ESP32-S3 firmware
- ADS1299-based biopotential acquisition
- desktop GUI for configuration and live signal visualization
- experimental ECG validation

## Licensing

- Software in `firmware/` and `gui/`: MIT. See `LICENSE` and `LICENSE-software`.
- Hardware designs in `hardware/`: CERN-OHL-S v2. See `LICENSE-hardware`.
- Documentation in `docs/`: CC BY 4.0. See `LICENSE-docs`.
