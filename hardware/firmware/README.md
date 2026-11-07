# Firmware (ESP32)

Put the PlatformIO/Arduino project here.

**What the firmware does:**
1. Initialize the ICM-42688-P (set the accel/gyro range and the output data rate).
2. Sample at a fixed rate and timestamp each sample with `millis()`.
3. Read the label button (debounced) and set `flags.bit0` on the matching sample.
4. Batch samples into BLE notifications using the packet format in [docs/data-schema.md](../../docs/data-schema.md).
5. Accept start/stop commands on the control characteristic.

**Build / flash:** _add the commands here once the project exists._
