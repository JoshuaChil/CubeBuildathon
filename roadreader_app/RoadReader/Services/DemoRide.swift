import Foundation
import CoreLocation

/// Plays back a recorded ride (Resources/demo_ride.csv) as if it came from the ESP32, in real time,
/// with the GPS fixes that were recorded alongside it. Lets the whole app → computer → road analysis
/// path run without the sensor box (and without actually riding).
///
/// File columns: t_ms,ax,ay,az,gx,gy,gz,lat,lon,speed_mps   (regenerate with
/// `python -m roadimu simulate --scenario nyc_mixed --vehicle bike --format demo --out <dir>`).
final class DemoRide {
    private struct Row {
        let line: String  // exactly what the ESP32 would send: t_ms,ax,ay,az,gx,gy,gz
        let tMs: Double
        let location: CLLocation?
    }

    /// Called on the main thread for every sample: (raw ESP32 line, GPS fix at that moment).
    var onLine: ((String, CLLocation?) -> Void)?
    var onFinished: (() -> Void)?

    private(set) var duration: TimeInterval = 0
    private var rows: [Row] = []
    private var index = 0
    private var startedAt: Date?
    private var timer: Timer?

    var isRunning: Bool { timer != nil }
    var progress: Double { rows.isEmpty ? 0 : Double(index) / Double(rows.count) }

    func start() throws {
        stop()
        if rows.isEmpty { try load() }
        index = 0
        startedAt = Date()
        let t = Timer(timeInterval: 0.02, repeats: true) { [weak self] _ in self?.tick() }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    private func tick() {
        guard let startedAt, let first = rows.first else { return }
        let elapsedMs = Date().timeIntervalSince(startedAt) * 1000
        while index < rows.count, rows[index].tMs - first.tMs <= elapsedMs {
            let row = rows[index]
            onLine?(row.line, row.location)
            index += 1
        }
        if index >= rows.count {
            stop()
            onFinished?()
        }
    }

    private func load() throws {
        guard let url = Bundle.main.url(forResource: "demo_ride", withExtension: "csv") else {
            throw NSError(domain: "DemoRide", code: 1, userInfo: [NSLocalizedDescriptionKey: "demo_ride.csv is missing from the app bundle."])
        }
        let text = try String(contentsOf: url, encoding: .utf8)
        var parsed: [Row] = []
        parsed.reserveCapacity(40_000)
        var lastFix: CLLocation?
        var lastKey = ""
        for (i, line) in text.split(separator: "\n").enumerated() where i > 0 {
            let f = line.split(separator: ",", omittingEmptySubsequences: false)
            guard f.count >= 10, let t = Double(f[0]) else { continue }
            let key = "\(f[7]),\(f[8])"
            if key != lastKey, let lat = Double(f[7]), let lon = Double(f[8]) {
                lastFix = CLLocation(coordinate: CLLocationCoordinate2D(latitude: lat, longitude: lon),
                                     altitude: 0, horizontalAccuracy: 5, verticalAccuracy: 10,
                                     course: -1, speed: Double(f[9]) ?? -1, timestamp: Date())
                lastKey = key
            }
            parsed.append(Row(line: f[0...6].joined(separator: ","), tMs: t, location: lastFix))
        }
        rows = parsed
        duration = ((parsed.last?.tMs ?? 0) - (parsed.first?.tMs ?? 0)) / 1000
    }
}
