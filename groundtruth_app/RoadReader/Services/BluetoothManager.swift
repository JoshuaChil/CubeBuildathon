import Foundation
import CoreBluetooth
import Combine

struct DiscoveredDevice: Identifiable, Equatable {
    let id: UUID
    let name: String
    let rssi: Int
    let advertisesService: Bool
}

enum BluetoothState: Equatable {
    case unknown
    case poweredOff
    case unauthorized
    case unsupported
    case scanning
    case connecting(String)
    case connected(String)
    case disconnected

    var isConnected: Bool {
        if case .connected = self { return true }
        return false
    }

    var deviceName: String? {
        switch self {
        case .connecting(let n), .connected(let n): return n
        default: return nil
        }
    }

    var title: String {
        switch self {
        case .unknown: return "Starting Bluetooth…"
        case .poweredOff: return "Bluetooth is off"
        case .unauthorized: return "Bluetooth access denied"
        case .unsupported: return "Bluetooth unsupported"
        case .scanning: return "Looking for Road Reader device…"
        case .connecting(let n): return "Connecting to \(n)…"
        case .connected(let n): return "Connected to \(n)"
        case .disconnected: return "Disconnected"
        }
    }
}

/// Nordic UART Service UUIDs (what most ESP32 BLE sketches use).
enum NUS {
    static let service = CBUUID(string: AppSettings.nordicUARTService)
    static let rx = CBUUID(string: "6E400002-B5A3-F393-E0A9-E50E24DC4CA1") // phone -> ESP32
    static let tx = CBUUID(string: "6E400003-B5A3-F393-E0A9-E50E24DC4CA1") // ESP32 -> phone
}

/// Scans for the ESP32, connects, and turns incoming notification chunks into complete text lines.
/// All CoreBluetooth callbacks are delivered on the main queue.
final class BluetoothManager: NSObject, ObservableObject {
    @Published private(set) var state: BluetoothState = .unknown
    @Published private(set) var devices: [DiscoveredDevice] = []
    @Published private(set) var isSubscribed = false
    @Published private(set) var canStream = false
    @Published private(set) var lastError: String?

    var deviceNamePrefix = "RoadReader"
    var serviceUUID: CBUUID = NUS.service
    /// Called on the main queue with each complete, newline-terminated line.
    var onLine: ((String) -> Void)?

    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var peripheralsByID: [UUID: CBPeripheral] = [:]
    private var notifyCharacteristic: CBCharacteristic?
    private var notifyIsExactMatch = false
    private var writeCharacteristic: CBCharacteristic?
    private var rxBuffer = Data()
    private var shouldAutoReconnect = false
    private var wantsStreaming = false

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: nil)
    }

    // MARK: - Public API

    func startScanning() {
        guard central.state == .poweredOn else { return }
        devices.removeAll()
        peripheralsByID.removeAll()
        state = .scanning
        central.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
    }

    func stopScanning() {
        central.stopScan()
    }

    func connect(to device: DiscoveredDevice) {
        guard let p = peripheralsByID[device.id] else { return }
        central.stopScan()
        peripheral = p
        p.delegate = self
        shouldAutoReconnect = true
        state = .connecting(device.name)
        central.connect(p, options: nil)
    }

    func disconnect() {
        shouldAutoReconnect = false
        wantsStreaming = false
        if let p = peripheral {
            central.cancelPeripheralConnection(p)
        } else {
            resetConnection()
            startScanning()
        }
    }

    /// Subscribes to notifications and asks the ESP32 to start streaming.
    func startStreaming() {
        wantsStreaming = true
        rxBuffer.removeAll()
        if let c = notifyCharacteristic {
            peripheral?.setNotifyValue(true, for: c)
        }
        send(command: "START")
    }

    func stopStreaming() {
        wantsStreaming = false
        send(command: "STOP")
        if let c = notifyCharacteristic {
            peripheral?.setNotifyValue(false, for: c)
        }
    }

    // MARK: - Helpers

    private func send(command: String) {
        guard let p = peripheral, let c = writeCharacteristic,
              let data = (command + "\n").data(using: .utf8) else { return }
        let type: CBCharacteristicWriteType = c.properties.contains(.writeWithoutResponse) ? .withoutResponse : .withResponse
        p.writeValue(data, for: c, type: type)
    }

    private func resetConnection() {
        peripheral = nil
        notifyCharacteristic = nil
        notifyIsExactMatch = false
        writeCharacteristic = nil
        isSubscribed = false
        canStream = false
        rxBuffer.removeAll()
    }

    private func consumeBuffer() {
        while let newline = rxBuffer.firstIndex(of: 0x0A) {
            let lineData = rxBuffer.subdata(in: rxBuffer.startIndex..<newline)
            rxBuffer.removeSubrange(rxBuffer.startIndex...newline)
            if let line = String(data: lineData, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines), !line.isEmpty {
                onLine?(line)
            }
        }
        // Guard against a device that never sends newlines.
        if rxBuffer.count > 4096 { rxBuffer.removeAll() }
    }
}

// MARK: - CBCentralManagerDelegate

extension BluetoothManager: CBCentralManagerDelegate {
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        switch central.state {
        case .poweredOn:
            // On open, immediately look for a listening device.
            startScanning()
        case .poweredOff:
            resetConnection()
            state = .poweredOff
        case .unauthorized:
            state = .unauthorized
        case .unsupported:
            state = .unsupported
        default:
            state = .unknown
        }
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        let advertisedName = advertisementData[CBAdvertisementDataLocalNameKey] as? String
        let name = advertisedName ?? peripheral.name ?? ""
        let services = advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] ?? []
        let advertisesService = services.contains(serviceUUID)

        // Hide anonymous devices to keep the list readable.
        guard !name.isEmpty || advertisesService else { return }

        peripheralsByID[peripheral.identifier] = peripheral
        let device = DiscoveredDevice(id: peripheral.identifier,
                                      name: name.isEmpty ? "Unnamed device" : name,
                                      rssi: RSSI.intValue,
                                      advertisesService: advertisesService)
        if let i = devices.firstIndex(where: { $0.id == device.id }) {
            devices[i] = device
        } else {
            devices.append(device)
        }
        devices.sort { a, b in
            if a.advertisesService != b.advertisesService { return a.advertisesService }
            return a.rssi > b.rssi
        }

        let nameMatches = !deviceNamePrefix.isEmpty &&
            name.lowercased().hasPrefix(deviceNamePrefix.lowercased())
        if case .scanning = state, advertisesService || nameMatches {
            connect(to: device)
        }
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        lastError = nil
        state = .connected(peripheral.name ?? state.deviceName ?? "device")
        peripheral.discoverServices(nil)
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        lastError = error?.localizedDescription ?? "Failed to connect"
        resetConnection()
        startScanning()
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        let name = peripheral.name ?? state.deviceName ?? "device"
        notifyCharacteristic = nil
        notifyIsExactMatch = false
        writeCharacteristic = nil
        isSubscribed = false
        canStream = false
        rxBuffer.removeAll()

        if shouldAutoReconnect {
            // Dropped link (out of range, reset, etc.): reconnect as soon as it is back.
            lastError = error?.localizedDescription
            state = .connecting(name)
            central.connect(peripheral, options: nil)
        } else {
            peripheral.delegate = nil
            self.peripheral = nil
            state = .disconnected
            startScanning()
        }
    }
}

// MARK: - CBPeripheralDelegate

extension BluetoothManager: CBPeripheralDelegate {
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard error == nil, let services = peripheral.services else {
            lastError = error?.localizedDescription
            return
        }
        for service in services {
            peripheral.discoverCharacteristics(nil, for: service)
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard error == nil, let characteristics = service.characteristics else { return }
        let inTargetService = service.uuid == serviceUUID

        for c in characteristics {
            let isNotify = c.properties.contains(.notify) || c.properties.contains(.indicate)
            let isWrite = c.properties.contains(.write) || c.properties.contains(.writeWithoutResponse)

            if isNotify {
                let exact = c.uuid == NUS.tx || inTargetService
                if notifyCharacteristic == nil || (exact && !notifyIsExactMatch) {
                    notifyCharacteristic = c
                    notifyIsExactMatch = exact
                }
            }
            if isWrite, writeCharacteristic == nil || c.uuid == NUS.rx || inTargetService {
                writeCharacteristic = c
            }
        }

        canStream = notifyCharacteristic != nil
        if wantsStreaming, let c = notifyCharacteristic, !c.isNotifying {
            peripheral.setNotifyValue(true, for: c)
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        if let error { lastError = error.localizedDescription }
        isSubscribed = characteristic.isNotifying
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard error == nil, let data = characteristic.value, !data.isEmpty else { return }
        rxBuffer.append(data)
        consumeBuffer()
    }
}
