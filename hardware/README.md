# Hardware

**Goal:** build a rugged box that reads the IMU, marks bump labels from a button, and streams everything to the phone over BLE.

## Folder layout

```
hardware/
├── firmware/     ESP32 code (PlatformIO or Arduino IDE)
├── wiring/       Pinout, schematic and wiring photos
├── enclosure/    CAD files (STEP/STL) for the box and the bike/car mount
└── BOM.md        Parts list
```

## Parts

See [BOM.md](BOM.md). The core parts are the **ICM-42688-P** IMU and an **ESP32**.

## Task checklist

- [ ] Wire the IMU to the ESP32 (SPI or I2C) and document it in [wiring/pinout.md](wiring/pinout.md)
- [ ] Read accel and gyro over serial; confirm the ranges and the sample rate
- [ ] Add the push button (debounced) → `flags.bit0` in the packet
- [ ] BLE GATT server matching [docs/data-schema.md](../docs/data-schema.md) (see [firmware/README.md](firmware/README.md))
- [ ] Battery + power switch; measure runtime
- [ ] Enclosure + mount ([enclosure/README.md](enclosure/README.md)); the IMU must be **rigidly** fixed (no foam), with a known orientation
- [ ] Status LED (connected / recording)
- [ ] Build a second unit as a backup for the demo

## Notes

- Mark the axis orientation on the outside of the box (e.g. "Z up, X forward"). Software and ML depend on it.
- Use one mount position for all training rides.
