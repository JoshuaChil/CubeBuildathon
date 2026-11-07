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
        static let supabaseURL = "supabaseURL"
        static let supabaseKey = "supabaseKey"
        static let supabaseTable = "supabaseTable"
        static let deviceID = "deviceID"
        static let liveAnalysisEnabled = "liveAnalysisEnabled"
        static let analysisPort = "analysisPort"
        static let vehicleType = "vehicleType"
        static let liveBroadcastEnabled = "liveBroadcastEnabled"

        static let persistable: Set<String> = [
            forwardingEnabled, forwardHost, forwardPort, forwardProtocol, uploadURL,
            liveAnalysisEnabled, analysisPort, vehicleType, deviceNamePrefix,
            supabaseURL, supabaseKey, supabaseTable, liveBroadcastEnabled,
        ]
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

    /// Supabase project (see Config/SupabaseConfig.swift for the defaults and SupabaseUploader for the requests).
    @Published var supabaseURL: String {
        didSet { defaults.set(supabaseURL, forKey: Keys.supabaseURL) }
    }
    @Published var supabaseKey: String {
        didSet { defaults.set(supabaseKey, forKey: Keys.supabaseKey) }
    }
    @Published var supabaseTable: String {
        didSet { defaults.set(supabaseTable, forKey: Keys.supabaseTable) }
    }
    var supabaseCredentials: SupabaseUploader.Credentials? {
        SupabaseUploader.Credentials(url: supabaseURL, anonKey: supabaseKey, table: supabaseTable)
    }
    /// Live Broadcasting: stream rows into the Supabase table while recording (needs supabaseCredentials).
    @Published var liveBroadcastEnabled: Bool {
        didSet { defaults.set(liveBroadcastEnabled, forKey: Keys.liveBroadcastEnabled) }
    }
    var canBroadcastLive: Bool { liveBroadcastEnabled && supabaseCredentials != nil }
    /// Show live road analysis while recording (polls listener.py --analyze on the forwarding host).
    @Published var liveAnalysisEnabled: Bool {
        didSet { defaults.set(liveAnalysisEnabled, forKey: Keys.liveAnalysisEnabled) }
    }
    @Published var analysisPort: Int {
        didSet { defaults.set(analysisPort, forKey: Keys.analysisPort) }
    }
    /// Which vehicle model the road analysis inverts (the box's mount differs a lot between them).
    @Published var vehicleType: VehicleType {
        didSet { defaults.set(vehicleType.rawValue, forKey: Keys.vehicleType) }
    }

    /// Stable per-install identifier sent with uploads so sessions can be grouped by phone.
    let deviceID: String

    /// Analysis endpoints next to the upload URL: .../upload -> .../results/<device>/<session> or .../report/...
    func analysisURL(kind: String, session name: String) -> URL? {
        guard var comps = URLComponents(string: uploadURL.trimmingCharacters(in: .whitespaces)),
              comps.scheme?.hasPrefix("http") == true else { return nil }
        var base = comps.percentEncodedPath
        while base.hasSuffix("/") { base.removeLast() }
        if base.hasSuffix("/upload") { base.removeLast("/upload".count) }
        var allowed = CharacterSet.urlPathAllowed
        allowed.remove("/")
        let enc = { (s: String) in s.addingPercentEncoding(withAllowedCharacters: allowed) ?? s }
        comps.percentEncodedPath = "\(base)/\(kind)/\(enc(deviceID))/\(enc(name))"
        comps.query = nil
        return comps.url
    }

    init() {
        // Settings passed as launch arguments (e.g. `-forwardHost 10.0.0.5` from Xcode's scheme or
        // `devicectl device process launch`) are saved, so the next normal launch keeps them.
        let args = UserDefaults.standard.volatileDomain(forName: UserDefaults.argumentDomain)
        for (key, value) in args where Keys.persistable.contains(key) {
            if let s = value as? String, let n = Int(s), [Keys.forwardPort, Keys.analysisPort].contains(key) {
                defaults.set(n, forKey: key)
            } else if let s = value as? String, ["YES", "NO", "true", "false"].contains(s) {
                defaults.set(s == "YES" || s == "true", forKey: key)
            } else {
                defaults.set(value, forKey: key)
            }
        }
        if let id = defaults.string(forKey: Keys.deviceID) {
            deviceID = id
        } else {
            let id = UUID().uuidString
            defaults.set(id, forKey: Keys.deviceID)
            deviceID = id
        }
        uploadURL = defaults.string(forKey: Keys.uploadURL) ?? ""
        uploadAPIKey = defaults.string(forKey: Keys.uploadAPIKey) ?? ""
        // Fall back to SupabaseConfig.swift until something was typed in Settings on this phone.
        supabaseURL = defaults.string(forKey: Keys.supabaseURL).flatMap { $0.isEmpty ? nil : $0 } ?? SupabaseConfig.url
        supabaseKey = defaults.string(forKey: Keys.supabaseKey).flatMap { $0.isEmpty ? nil : $0 } ?? SupabaseConfig.anonKey
        supabaseTable = defaults.string(forKey: Keys.supabaseTable).flatMap { $0.isEmpty ? nil : $0 } ?? SupabaseConfig.table
        deviceNamePrefix = defaults.string(forKey: Keys.deviceNamePrefix) ?? "RoadReader"
        serviceUUID = defaults.string(forKey: Keys.serviceUUID) ?? AppSettings.nordicUARTService
        forwardingEnabled = defaults.object(forKey: Keys.forwardingEnabled) as? Bool ?? true
        forwardHost = defaults.string(forKey: Keys.forwardHost) ?? "192.168.1.100"
        forwardPort = defaults.object(forKey: Keys.forwardPort) as? Int ?? 5005
        forwardProtocol = ForwardProtocol(rawValue: defaults.string(forKey: Keys.forwardProtocol) ?? "") ?? .udp
        minMarkDuration = defaults.object(forKey: Keys.minMarkDuration) as? Double ?? 0.5
        liveAnalysisEnabled = defaults.object(forKey: Keys.liveAnalysisEnabled) as? Bool ?? true
        analysisPort = defaults.object(forKey: Keys.analysisPort) as? Int ?? 8081
        vehicleType = VehicleType(rawValue: defaults.string(forKey: Keys.vehicleType) ?? "") ?? .bike
        liveBroadcastEnabled = defaults.object(forKey: Keys.liveBroadcastEnabled) as? Bool ?? false
    }
}
