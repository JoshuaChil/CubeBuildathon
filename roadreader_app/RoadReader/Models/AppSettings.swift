import Foundation
import Combine

enum ForwardProtocol: String, CaseIterable, Identifiable {
    case udp
    case tcp

    var id: String { rawValue }
    var label: String { rawValue.uppercased() }
}

/// User-tunable settings, persisted in UserDefaults.
final class AppSettings: ObservableObject {
    private enum Keys {
        static let deviceNamePrefix = "deviceNamePrefix"
        static let serviceUUID = "serviceUUID"
        static let forwardingEnabled = "forwardingEnabled"
        static let forwardHost = "forwardHost"
        static let forwardPort = "forwardPort"
        static let forwardProtocol = "forwardProtocol"
        static let minMarkDuration = "minMarkDuration"
        static let uploadURL = "uploadURL"
        static let uploadAPIKey = "uploadAPIKey"
        static let deviceID = "deviceID"
    }

    static let nordicUARTService = "6E400001-B5A3-F393-E0A9-E50E24DC4CA1"

    private let defaults = UserDefaults.standard

    /// Devices whose advertised name starts with this are connected to automatically.
    @Published var deviceNamePrefix: String {
        didSet { defaults.set(deviceNamePrefix, forKey: Keys.deviceNamePrefix) }
    }
    /// BLE service that carries the data stream (defaults to the Nordic UART Service used by most ESP32 sketches).
    @Published var serviceUUID: String {
        didSet { defaults.set(serviceUUID, forKey: Keys.serviceUUID) }
    }
    @Published var forwardingEnabled: Bool {
        didSet { defaults.set(forwardingEnabled, forKey: Keys.forwardingEnabled) }
    }
    @Published var forwardHost: String {
        didSet { defaults.set(forwardHost, forKey: Keys.forwardHost) }
    }
    @Published var forwardPort: Int {
        didSet { defaults.set(forwardPort, forKey: Keys.forwardPort) }
    }
    @Published var forwardProtocol: ForwardProtocol {
        didSet { defaults.set(forwardProtocol.rawValue, forKey: Keys.forwardProtocol) }
    }
    /// A quick tap on Detect Bump labels rows for at least this many seconds.
    @Published var minMarkDuration: Double {
        didSet { defaults.set(minMarkDuration, forKey: Keys.minMarkDuration) }
    }

    /// HTTP(S) endpoint that receives session CSVs (see SessionUploader for the request format).
    @Published var uploadURL: String {
        didSet { defaults.set(uploadURL, forKey: Keys.uploadURL) }
    }
    @Published var uploadAPIKey: String {
        didSet { defaults.set(uploadAPIKey, forKey: Keys.uploadAPIKey) }
    }
    /// Stable per-install identifier sent with uploads so sessions can be grouped by phone.
    let deviceID: String

    init() {
        if let id = defaults.string(forKey: Keys.deviceID) {
            deviceID = id
        } else {
            let id = UUID().uuidString
            defaults.set(id, forKey: Keys.deviceID)
            deviceID = id
        }
        uploadURL = defaults.string(forKey: Keys.uploadURL) ?? ""
        uploadAPIKey = defaults.string(forKey: Keys.uploadAPIKey) ?? ""
        deviceNamePrefix = defaults.string(forKey: Keys.deviceNamePrefix) ?? "RoadReader"
        serviceUUID = defaults.string(forKey: Keys.serviceUUID) ?? AppSettings.nordicUARTService
        forwardingEnabled = defaults.object(forKey: Keys.forwardingEnabled) as? Bool ?? true
        forwardHost = defaults.string(forKey: Keys.forwardHost) ?? "192.168.1.100"
        forwardPort = defaults.object(forKey: Keys.forwardPort) as? Int ?? 5005
        forwardProtocol = ForwardProtocol(rawValue: defaults.string(forKey: Keys.forwardProtocol) ?? "") ?? .udp
        minMarkDuration = defaults.object(forKey: Keys.minMarkDuration) as? Double ?? 0.5
    }
}
