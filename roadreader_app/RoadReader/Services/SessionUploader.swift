import Foundation

/// POSTs a session CSV to the web database as multipart/form-data.
///
/// Request contract (any backend can implement this):
///     POST <uploadURL>
///     Authorization: Bearer <apiKey>          (only when an API key is set)
///     Content-Type: multipart/form-data
///     fields:  session_name, device_id, rows, flagged, duration_s, started_at (ISO 8601)
///     file:    "file" — the CSV (text/csv), filename = session_name
/// Any 2xx response counts as success.
struct SessionUploader {
    enum UploadError: LocalizedError {
        case unreadableFile
        case server(status: Int, body: String)

        var errorDescription: String? {
            switch self {
            case .unreadableFile: return "Could not read the session file."
            case .server(let status, let body):
                return "Server returned \(status)" + (body.isEmpty ? "" : ": \(body.prefix(200))")
            }
        }
    }

    /// Returns the server's road analysis of the session, if it ran one (upload_server.py does).
    @discardableResult
    func upload(_ info: SessionInfo, to endpoint: URL, apiKey: String, deviceID: String, vehicle: VehicleType = .bike) async throws -> UploadAnalysis? {
        guard let csv = try? Data(contentsOf: info.url) else { throw UploadError.unreadableFile }

        let boundary = "RoadReader-\(UUID().uuidString)"
        var request = URLRequest(url: endpoint, timeoutInterval: 180)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        if !apiKey.isEmpty {
            request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        }

        var body = Data()
        func field(_ name: String, _ value: String) {
            body.append("--\(boundary)\r\n")
            body.append("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n")
            body.append("\(value)\r\n")
        }
        field("session_name", info.name)
        field("device_id", deviceID)
        field("rows", String(info.rows))
        field("flagged", String(info.flagged))
        field("duration_s", String(format: "%.1f", info.duration))
        field("vehicle", vehicle.rawValue)
        if let start = info.startedAt {
            field("started_at", ISO8601DateFormatter().string(from: start))
        }
        body.append("--\(boundary)\r\n")
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(info.name)\"\r\n")
        body.append("Content-Type: text/csv\r\n\r\n")
        body.append(csv)
        body.append("\r\n--\(boundary)--\r\n")

        let (data, response) = try await URLSession.shared.upload(for: request, from: body)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            throw UploadError.server(status: status, body: String(data: data, encoding: .utf8) ?? "")
        }
        struct Response: Decodable { let analysis: UploadAnalysis? }
        return (try? RoadAnalysisJSON.decoder.decode(Response.self, from: data))?.analysis
    }
}

private extension Data {
    mutating func append(_ string: String) {
        if let d = string.data(using: .utf8) { append(d) }
    }
}
