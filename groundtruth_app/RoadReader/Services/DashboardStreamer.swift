import Foundation
import Combine

/// Live Broadcasting: streams every reading of the running session straight to the GroundTruth dashboard
/// server (software/dashboard/server.py), which fans it out to the browsers showing the live page.
/// The CSV file and the computer stream carry on as usual; nothing goes to a database.
///
/// Rows are queued on the main thread as they arrive (200 Hz) and POSTed as JSON about four times a second:
///
///     POST {dashboard URL}/ingest
///     {"device_id": "...", "session": "session_20261108_153012",
///      "rows": [{"t": 1762620000123, "ax": .., "ay": .., "az": .., "gx": .., "gy": .., "gz": ..,
///                "lat": .., "lon": .., "flag": 0}, ...]}
///
/// plus `{"event": "start"}` when the session opens and `{"event": "stop", "rows_sent": N}` when it ends.
/// A failed batch stays at the head of the queue and is retried with backoff, so a Wi-Fi hiccup delays rows
/// instead of losing them; only a queue overflow (roughly five minutes offline) drops rows.
///
/// Call `start`, `enqueue` and `stop` on the main thread.
final class DashboardStreamer: ObservableObject {
    enum Status: Equatable {
        case off
        case starting
        case live
        case retrying(String)
        case draining

        var title: String {
            switch self {
            case .off: return "Off"
            case .starting: return "Connecting to dashboard…"
            case .live: return "LIVE"
            case .retrying: return "Reconnecting…"
            case .draining: return "Sending last rows…"
            }
        }
    }

    enum StreamError: LocalizedError {
        case server(status: Int, body: String)

        var errorDescription: String? {
            switch self {
            case .server(let status, let body):
                return "Dashboard returned \(status)" + (body.isEmpty ? "" : ": \(body.prefix(120))")
            }
        }
    }

    @Published private(set) var status: Status = .off
    /// Name of the session being streamed (the CSV file name without extension).
    @Published private(set) var sessionName = ""
    /// Where rows are going, for the UI.
    @Published private(set) var destination = ""
    @Published private(set) var lastError: String?

    /// Rows the dashboard server has accepted. Polled by the UI timer rather than published, to avoid 200 Hz re-renders.
    private(set) var sentRows = 0
    /// Rows thrown away because the queue overflowed while the server was unreachable.
    private(set) var droppedRows = 0
    /// Rows still queued when the last session stopped that could not be delivered.
    private(set) var undeliveredRows = 0
    private(set) var retryCount = 0
    private(set) var isActive = false
    var pendingRows: Int { queue.count }

    private struct Row: Encodable {
        let t: Int
        let ax: Double, ay: Double, az: Double, gx: Double, gy: Double, gz: Double
        let lat: Double?, lon: Double?   // omitted when nil; the server treats a missing key as no fix
        let flag: Int
    }

    private struct Batch: Encodable {
        let device_id: String
        let session: String
        var rows: [Row]? = nil
        var event: String? = nil
        var rows_sent: Int? = nil
    }

    private var queue: [Row] = []
    private var endpoint: URL?
    private var deviceID = ""
    private var flushTimer: Timer?
    private var inFlight = false
    private var backoff: TimeInterval = 0.5
    private var nextAttempt = Date.distantPast
    private var urlSession: URLSession = {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 10
        cfg.waitsForConnectivity = false
        return URLSession(configuration: cfg)
    }()

    private let flushInterval: TimeInterval = 0.25
    private let maxBatch = 500
    private let maxQueue = 60_000

    // MARK: - Lifecycle

    /// Opens a live session towards `baseURL` (e.g. http://192.168.1.20:8090) and starts the flush timer.
    func start(baseURL: URL, session: String, deviceID: String) {
        guard status != .draining else { return }
        reset()
        endpoint = baseURL.appendingPathComponent("ingest")
        self.deviceID = deviceID
        sessionName = session
        destination = baseURL.host.map { h in baseURL.port.map { "\(h):\($0)" } ?? h } ?? baseURL.absoluteString
        isActive = true
        status = .starting

        // Tell the dashboard a session has begun. Rows start flowing with the first flush regardless.
        Task { @MainActor [weak self] in
            guard let self, let url = self.endpoint else { return }
            do {
                try await self.post(Batch(device_id: deviceID, session: session, event: "start"), to: url)
                if self.isActive { self.status = .live; self.lastError = nil }
            } catch {
                if self.isActive, self.status == .starting { self.status = .retrying(error.localizedDescription) }
                self.lastError = error.localizedDescription
            }
        }

        let timer = Timer(timeInterval: flushInterval, repeats: true) { [weak self] _ in self?.flush() }
        RunLoop.main.add(timer, forMode: .common)
        flushTimer = timer
    }

    /// Queues one labelled, geotagged sample. Cheap enough to call per row.
    func enqueue(_ s: IMUSample) {
        guard isActive else { return }
        var lat: Double?
        var lon: Double?
        if let l = s.location, l.horizontalAccuracy >= 0 {
            lat = l.coordinate.latitude
            lon = l.coordinate.longitude
        }
        queue.append(Row(t: Int(s.receivedAt.timeIntervalSince1970 * 1000),
                         ax: s.ax, ay: s.ay, az: s.az, gx: s.gx, gy: s.gy, gz: s.gz,
                         lat: lat, lon: lon, flag: s.obstructionDetected ? 1 : 0))
        if queue.count > maxQueue {
            // Drop the oldest rows that are not part of an in-flight batch (the first `maxBatch`).
            let excess = queue.count - maxQueue
            let dropStart = inFlight ? min(maxBatch, queue.count - excess) : 0
            queue.removeSubrange(dropStart..<(dropStart + excess))
            droppedRows += excess
        }
        if queue.count >= maxBatch { flush() }
    }

    /// Stops accepting rows and tries to deliver what is still queued. Returns true only if every row
    /// of the session reached the dashboard.
    @MainActor
    func stop() async -> Bool {
        guard isActive else { return false }
        isActive = false
        flushTimer?.invalidate()
        flushTimer = nil
        status = .draining

        // Let an in-flight batch finish (the request times out after 10 s).
        var waited = 0
        while inFlight && waited < 60 {
            try? await Task.sleep(nanoseconds: 200_000_000)
            waited += 1
        }

        var attempts = 0
        while !queue.isEmpty, let url = endpoint, attempts < 5 {
            let n = min(maxBatch, queue.count)
            do {
                try await post(Batch(device_id: deviceID, session: sessionName, rows: Array(queue.prefix(n))), to: url)
                queue.removeFirst(n)
                sentRows += n
            } catch {
                attempts += 1
                lastError = error.localizedDescription
                try? await Task.sleep(nanoseconds: 500_000_000)
            }
        }
        if let url = endpoint {
            try? await post(Batch(device_id: deviceID, session: sessionName, event: "stop", rows_sent: sentRows), to: url)
        }

        undeliveredRows = queue.count
        let complete = queue.isEmpty && droppedRows == 0
        queue.removeAll()
        endpoint = nil
        status = .off
        return complete
    }

    // MARK: - Internals

    private func reset() {
        flushTimer?.invalidate()
        flushTimer = nil
        queue.removeAll()
        endpoint = nil
        sentRows = 0
        droppedRows = 0
        undeliveredRows = 0
        retryCount = 0
        inFlight = false
        backoff = 0.5
        nextAttempt = .distantPast
        lastError = nil
    }

    private func flush() {
        guard !inFlight, let url = endpoint, !queue.isEmpty, Date() >= nextAttempt else { return }
        let n = min(maxBatch, queue.count)
        let batch = Batch(device_id: deviceID, session: sessionName, rows: Array(queue.prefix(n)))
        inFlight = true

        Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                try await self.post(batch, to: url)
                self.queue.removeFirst(n)
                self.sentRows += n
                self.backoff = 0.5
                self.lastError = nil
                if self.isActive { self.status = .live }
            } catch {
                self.retryCount += 1
                self.lastError = error.localizedDescription
                if self.isActive { self.status = .retrying(error.localizedDescription) }
                self.nextAttempt = Date().addingTimeInterval(self.backoff)
                self.backoff = min(self.backoff * 2, 10)
            }
            self.inFlight = false
            if self.isActive, self.queue.count >= self.maxBatch { self.flush() }
        }
    }

    private func post(_ batch: Batch, to url: URL) async throws {
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        let body = try JSONEncoder().encode(batch)
        let (data, response) = try await urlSession.upload(for: req, from: body)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            throw StreamError.server(status: status, body: String(data: data, encoding: .utf8) ?? "")
        }
    }
}
