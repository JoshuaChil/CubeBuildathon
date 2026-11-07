import Foundation
import CoreLocation

/// One row of IMU data received from the ESP32, labelled by the app and tagged with GPS.
///
/// Wire format from the ESP32 (one line per sample, newline terminated):
///     ax,ay,az,gx,gy,gz                 (6 numeric fields)
///     t_ms,ax,ay,az,gx,gy,gz            (7 fields, first is the ESP32 millis() clock)
///
/// Dataset/forwarded format (see `csvHeader`): columns 1-6 are the IMU values, column 7 is
/// "Crack/Pothole Detected" (1 while the user is pressing/holding Detect Bump, else 0),
/// and the remaining columns are phone-side metadata and GPS.
struct IMUSample: Identifiable {
    let id = UUID()
    let receivedAt: Date
    let deviceTimeMs: Int?
    let ax: Double
    let ay: Double
    let az: Double
    let gx: Double
    let gy: Double
    let gz: Double
    /// Column 7: "Crack/Pothole Detected".
    let obstructionDetected: Bool
    let location: CLLocation?

    static let csvHeader = [
        "ax", "ay", "az", "gx", "gy", "gz",
        "crack_pothole_detected",
        "phone_time_ms", "device_time_ms",
        "latitude", "longitude", "altitude_m", "speed_mps", "horizontal_accuracy_m",
    ].joined(separator: ",")

    /// Parses a raw line from the ESP32. Returns nil for headers, blank lines or malformed rows.
    static func parse(_ line: String, location: CLLocation?, detected: Bool, receivedAt: Date = Date()) -> IMUSample? {
        let separators = CharacterSet(charactersIn: ",;\t ")
        let fields = line
            .split(whereSeparator: { $0.unicodeScalars.allSatisfy(separators.contains) })
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }

        let numbers = fields.compactMap(Double.init)
        guard numbers.count == fields.count else { return nil }

        var deviceTime: Int?
        var imu: [Double]
        switch numbers.count {
        case 6:
            imu = numbers
        case 7...:
            deviceTime = Int(numbers[0])
            imu = Array(numbers[1..<7])
        default:
            return nil
        }

        return IMUSample(
            receivedAt: receivedAt,
            deviceTimeMs: deviceTime,
            ax: imu[0], ay: imu[1], az: imu[2],
            gx: imu[3], gy: imu[4], gz: imu[5],
            obstructionDetected: detected,
            location: location
        )
    }

    var csvRow: String {
        var cols: [String] = [
            fmt(ax), fmt(ay), fmt(az), fmt(gx), fmt(gy), fmt(gz),
            obstructionDetected ? "1" : "0",
            String(Int(receivedAt.timeIntervalSince1970 * 1000)),
            deviceTimeMs.map(String.init) ?? "",
        ]
        if let loc = location, loc.horizontalAccuracy >= 0 {
            cols += [
                String(format: "%.6f", loc.coordinate.latitude),
                String(format: "%.6f", loc.coordinate.longitude),
                String(format: "%.1f", loc.altitude),
                String(format: "%.2f", max(loc.speed, 0)),
                String(format: "%.1f", loc.horizontalAccuracy),
            ]
        } else {
            cols += ["", "", "", "", ""]
        }
        return cols.joined(separator: ",")
    }

    private func fmt(_ v: Double) -> String {
        String(format: "%.4f", v)
    }
}
