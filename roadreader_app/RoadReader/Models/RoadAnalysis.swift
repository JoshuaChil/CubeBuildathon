import Foundation
import SwiftUI
import CoreLocation

/// Road analysis produced by software/roadimu on the computer.
///
/// Live:      GET http://<host>:<analysisPort>/live            (listener.py --analyze)
/// Uploaded:  POST /upload response "analysis", GET /results/<device_id>/<session_name>   (upload_server.py)
/// JSON keys are snake_case; decode with `RoadAnalysisJSON.decoder`.
enum RoadAnalysisJSON {
    static var decoder: JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }
}

enum VehicleType: String, CaseIterable, Identifiable {
    case bike, car
    var id: String { rawValue }
    var label: String { rawValue.capitalized }
}

/// IRI-based condition used by the pipeline: good < 2, fair < 4, poor < 6, else very poor (m/km).
enum RoadCondition {
    static func color(_ condition: String?) -> Color {
        switch condition {
        case "good": return .green
        case "fair": return .yellow
        case "poor": return .orange
        case "very poor": return .red
        default: return .gray
        }
    }

    static func title(_ condition: String?) -> String {
        (condition ?? "unknown").capitalized
    }
}

struct RoadEvent: Codable, Identifiable, Hashable {
    let type: String          // pothole | depression | bump
    let severity: Double      // 0...1
    let magnitudeMm: Double   // negative = dip
    let lengthM: Double?
    let sM: Double            // distance along the ride, m
    let tS: Double?
    let lat: Double?
    let lon: Double?

    var id: String { "\(type)-\(sM)" }
    var isDip: Bool { magnitudeMm < 0 }
    var title: String { type == "bump" ? "Bump" : (type == "depression" ? "Sunken patch" : "Pothole") }
    var coordinate: CLLocationCoordinate2D? {
        guard let lat, let lon else { return nil }
        return CLLocationCoordinate2D(latitude: lat, longitude: lon)
    }
}

struct RoadSegment: Codable, Identifiable, Hashable {
    let startM: Double
    let endM: Double
    let lat: Double?
    let lon: Double?
    let iriMPerKm: Double
    let rmsMm: Double
    let condition: String

    var id: Double { startM }
}

/// GET /live
struct LiveAnalysis: Codable {
    struct Current: Codable {
        let iriMPerKm: Double
        let rmsMm: Double
        let condition: String
    }

    let status: String        // waiting | collecting | ok | error
    let message: String
    let rows: Int?
    let durationS: Double?
    let distanceM: Double?
    let speedMps: Double?
    let meanIri: Double?
    let current: Current?
    let potholes: Int?
    let bumps: Int?
    let events: [RoadEvent]?
    let notes: [String]?
    let processingMs: Int?
}

/// The "analysis" object in the upload response (also stored per session on the phone).
struct UploadAnalysis: Codable, Equatable {
    let ok: Bool
    let vehicle: String?
    let distanceM: Double?
    let meanIriMPerKm: Double?
    let condition: String?
    let potholes: Int?
    let bumps: Int?
    let notes: [String]?
    let error: String?
}

/// GET /results/<device_id>/<session_name>
struct SessionResults: Codable {
    struct Summary: Codable {
        let distanceM: Double?
        let durationS: Double?
        let meanSpeedMps: Double?
        let meanIriMPerKm: Double?
        let elevationRangeM: Double?
        let potholes: Int?
        let bumps: Int?
        let buttonPresses: Int?
    }

    struct TrackPoint: Codable {
        let sM: Double
        let lat: Double?
        let lon: Double?
        let elevationM: Double?
    }

    let summary: Summary
    let notes: [String]
    let segments: [RoadSegment]
    let events: [RoadEvent]
    let track: [TrackPoint]

    /// Map polyline for one roughness segment, built from the track points inside it.
    func coordinates(for segment: RoadSegment) -> [CLLocationCoordinate2D] {
        track
            .filter { $0.sM >= segment.startM - 1 && $0.sM <= segment.endM + 1 }
            .compactMap { p in
                guard let lat = p.lat, let lon = p.lon else { return nil }
                return CLLocationCoordinate2D(latitude: lat, longitude: lon)
            }
    }
}
