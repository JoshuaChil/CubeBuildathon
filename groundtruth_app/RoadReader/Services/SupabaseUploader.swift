import Foundation

/// Uploads a recorded session to Supabase as one row per IMU reading, through the PostgREST
/// REST API (no SDK needed). Credentials come from Config/SupabaseConfig.swift or Settings > Supabase.
///
/// Target table (SQL in ../../supabase/schema.sql):
///     id BIGINT, session_date DATE, session_number INTEGER, recorded_at TIMESTAMPTZ,
///     ax ay az gx gy gz latitude longitude DOUBLE PRECISION, anomaly_detected SMALLINT
///
/// Mapping from the session CSV (IMUSample.csvHeader):
///     ax..gz                 -> ax..gz
///     crack_pothole_detected -> anomaly_detected (0 = normal, 1 = anomaly)
///     phone_time_ms          -> recorded_at (UTC, millisecond precision)
///     latitude, longitude    -> latitude, longitude (NULL for rows recorded before the first GPS fix)
///     session_date           = the phone's local calendar date of the first row
///     session_number         = 1 + the highest number already in the table for that date
///                              (and never lower than what this phone has already used that day)
///
/// Requests (every one carries `apikey: <anon key>` and `Authorization: Bearer <anon key>`):
///     GET  {url}/rest/v1/{table}?select=session_number&session_date=eq.{date}&order=session_number.desc&limit=1
///     POST {url}/rest/v1/{table}   body = JSON array of rows, sent in chunks; Prefer: return=minimal
struct SupabaseUploader {
    /// Validated connection details. `nil` while Settings/SupabaseConfig still hold placeholders.
    struct Credentials: Equatable {
        let baseURL: URL
        let anonKey: String
        let table: String

        init?(url: String, anonKey: String, table: String) {
            let urlText = url.trimmingCharacters(in: .whitespacesAndNewlines)
            let key = anonKey.trimmingCharacters(in: .whitespacesAndNewlines)
            let tbl = table.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !SupabaseConfig.isPlaceholder(url: urlText, anonKey: key),
                  var comps = URLComponents(string: urlText),
                  comps.scheme == "https", comps.host?.isEmpty == false,
                  !tbl.isEmpty else { return nil }
            comps.query = nil
            comps.fragment = nil
            while comps.path.hasSuffix("/") { comps.path.removeLast() }
            guard let base = comps.url else { return nil }
            baseURL = base
            self.anonKey = key
            self.table = tbl
        }

        /// {baseURL}/rest/v1/{table}?{query}
        func endpoint(query: [URLQueryItem] = []) -> URL {
            let url = baseURL.appendingPathComponent("rest").appendingPathComponent("v1").appendingPathComponent(table)
            guard !query.isEmpty, var comps = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return url }
            comps.queryItems = query
            return comps.url ?? url
        }
    }

    /// What the phone remembers about a session once it is in Supabase.
    struct Receipt: Codable, Hashable {
        let sessionDate: String   // yyyy-MM-dd
        let sessionNumber: Int
        let rows: Int
        let uploadedAt: Date
    }

    enum UploadError: LocalizedError {
        case unreadableFile
        case missingColumns([String])
        case noRows
        case server(status: Int, message: String)

        var errorDescription: String? {
            switch self {
            case .unreadableFile: return "Could not read the session file."
            case .missingColumns(let cols): return "CSV is missing columns: \(cols.joined(separator: ", "))."
            case .noRows: return "The session has no readings."
            case .server(let status, let message):
                return "Supabase returned \(status)" + (message.isEmpty ? "" : ": \(message.prefix(300))")
            }
        }
    }

    /// One row of the table. Optionals are encoded as explicit `null` so every object in a bulk insert
    /// has the same keys, which PostgREST requires.
    struct Reading: Encodable {
        let sessionDate: String
        let sessionNumber: Int
        let recordedAt: String
        let ax: Double, ay: Double, az: Double
        let gx: Double, gy: Double, gz: Double
        let latitude: Double?
        let longitude: Double?
        let anomalyDetected: Int

        enum CodingKeys: String, CodingKey {
            case sessionDate = "session_date"
            case sessionNumber = "session_number"
            case recordedAt = "recorded_at"
            case ax, ay, az, gx, gy, gz, latitude, longitude
            case anomalyDetected = "anomaly_detected"
        }

        func encode(to encoder: Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(sessionDate, forKey: .sessionDate)
            try c.encode(sessionNumber, forKey: .sessionNumber)
            try c.encode(recordedAt, forKey: .recordedAt)
            try c.encode(ax, forKey: .ax)
            try c.encode(ay, forKey: .ay)
            try c.encode(az, forKey: .az)
            try c.encode(gx, forKey: .gx)
            try c.encode(gy, forKey: .gy)
            try c.encode(gz, forKey: .gz)
            try c.encode(latitude, forKey: .latitude)     // nil -> null
            try c.encode(longitude, forKey: .longitude)   // nil -> null
            try c.encode(anomalyDetected, forKey: .anomalyDetected)
        }
    }

    /// Rows per POST. 1000 rows is roughly 150 KB of JSON.
    var chunkSize = 1000
    var urlSession: URLSession = .shared

    // MARK: - Public API

    /// Local calendar date of the session, as stored in `session_date`.
    static func sessionDate(for info: SessionInfo) -> String {
        sessionDate(for: info.startedAt ?? info.modified)
    }

    /// Local calendar date for `date`, as stored in `session_date`.
    static func sessionDate(for date: Date) -> String {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = .current
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: date)
    }

    /// Parses the session CSV, picks the next session number for `sessionDate`, and inserts every row.
    /// `progress(done, total)` is called on a background thread after each chunk.
    func upload(_ info: SessionInfo, credentials: Credentials, sessionDate: String, minimumNumber: Int = 1,
                progress: ((Int, Int) -> Void)? = nil) async throws -> Receipt {
        guard let text = try? String(contentsOf: info.url, encoding: .utf8) else { throw UploadError.unreadableFile }
        let parsed = try Self.parse(csv: text)
        guard !parsed.isEmpty else { throw UploadError.noRows }

        let serverNext = try await nextSessionNumber(date: sessionDate, credentials: credentials)
        let number = max(serverNext, minimumNumber)

        let rows = parsed.map { r in
            Reading(sessionDate: sessionDate, sessionNumber: number, recordedAt: r.recordedAt,
                    ax: r.ax, ay: r.ay, az: r.az, gx: r.gx, gy: r.gy, gz: r.gz,
                    latitude: r.latitude, longitude: r.longitude, anomalyDetected: r.anomaly)
        }

        var done = 0
        for start in stride(from: 0, to: rows.count, by: chunkSize) {
            let chunk = Array(rows[start..<min(start + chunkSize, rows.count)])
            try await insert(chunk, credentials: credentials)
            done += chunk.count
            progress?(done, rows.count)
        }
        return Receipt(sessionDate: sessionDate, sessionNumber: number, rows: rows.count, uploadedAt: Date())
    }

    // MARK: - CSV

    struct ParsedRow {
        let recordedAt: String
        let ax: Double, ay: Double, az: Double, gx: Double, gy: Double, gz: Double
        let latitude: Double?, longitude: Double?
        let anomaly: Int
    }

    /// Reads the app's CSV by column name, so column order changes in IMUSample do not break uploads.
    static func parse(csv text: String) throws -> [ParsedRow] {
        var lines = text.split(whereSeparator: \.isNewline)
        guard !lines.isEmpty else { return [] }
        let header = lines.removeFirst().split(separator: ",", omittingEmptySubsequences: false)
            .map { $0.trimmingCharacters(in: .whitespaces) }
        let index = Dictionary(header.enumerated().map { ($1, $0) }, uniquingKeysWith: { a, _ in a })

        let required = ["ax", "ay", "az", "gx", "gy", "gz", "crack_pothole_detected", "phone_time_ms"]
        let missing = required.filter { index[$0] == nil }
        guard missing.isEmpty else { throw UploadError.missingColumns(missing) }
        let col = { (name: String) in index[name]! }
        let latCol = index["latitude"], lonCol = index["longitude"]

        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        iso.timeZone = TimeZone(identifier: "UTC")

        var rows: [ParsedRow] = []
        rows.reserveCapacity(lines.count)
        for line in lines {
            let f = line.split(separator: ",", omittingEmptySubsequences: false)
            func num(_ i: Int) -> Double? { i < f.count ? Double(f[i].trimmingCharacters(in: .whitespaces)) : nil }
            guard let ax = num(col("ax")), let ay = num(col("ay")), let az = num(col("az")),
                  let gx = num(col("gx")), let gy = num(col("gy")), let gz = num(col("gz")),
                  let ms = num(col("phone_time_ms")) else { continue }   // skip malformed rows
            let anomaly = num(col("crack_pothole_detected")).map { $0 != 0 ? 1 : 0 } ?? 0
            rows.append(ParsedRow(
                recordedAt: iso.string(from: Date(timeIntervalSince1970: ms / 1000)),
                ax: ax, ay: ay, az: az, gx: gx, gy: gy, gz: gz,
                latitude: latCol.flatMap(num), longitude: lonCol.flatMap(num),
                anomaly: anomaly))
        }
        return rows
    }

    // MARK: - Requests

    private func request(_ url: URL, credentials: Credentials) -> URLRequest {
        var req = URLRequest(url: url, timeoutInterval: 60)
        req.setValue(credentials.anonKey, forHTTPHeaderField: "apikey")
        req.setValue("Bearer \(credentials.anonKey)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        return req
    }

    func nextSessionNumber(date: String, credentials: Credentials) async throws -> Int {
        let url = credentials.endpoint(query: [
            URLQueryItem(name: "select", value: "session_number"),
            URLQueryItem(name: "session_date", value: "eq.\(date)"),
            URLQueryItem(name: "order", value: "session_number.desc"),
            URLQueryItem(name: "limit", value: "1"),
        ])
        var req = request(url, credentials: credentials)
        req.httpMethod = "GET"
        let (data, response) = try await urlSession.data(for: req)
        try check(response, data)
        struct Row: Decodable { let session_number: Int? }
        let rows = (try? JSONDecoder().decode([Row].self, from: data)) ?? []
        return (rows.first?.session_number ?? 0) + 1
    }

    func insert(_ rows: [Reading], credentials: Credentials) async throws {
        var req = request(credentials.endpoint(), credentials: credentials)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("return=minimal", forHTTPHeaderField: "Prefer")
        let body = try JSONEncoder().encode(rows)
        let (data, response) = try await urlSession.upload(for: req, from: body)
        try check(response, data)
    }

    private func check(_ response: URLResponse, _ data: Data) throws {
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            struct PostgRESTError: Decodable { let message: String?; let hint: String?; let details: String? }
            let err = try? JSONDecoder().decode(PostgRESTError.self, from: data)
            let message = [err?.message, err?.hint].compactMap { $0 }.joined(separator: " — ")
            throw UploadError.server(status: status, message: message.isEmpty ? (String(data: data, encoding: .utf8) ?? "") : message)
        }
    }
}
