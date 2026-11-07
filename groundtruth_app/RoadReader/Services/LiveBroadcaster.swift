import Foundation
import Combine
import CoreLocation

/// Live Broadcasting: streams every reading of the running session straight into the Supabase table,
/// about once a second, while the CSV file and the computer stream carry on as usual.
///
/// Rows are queued on the main thread as they arrive (200 Hz) and inserted in batches through
/// `SupabaseUploader.insert`. A failed batch stays at the head of the queue and is retried with backoff,
/// so a Wi-Fi or cellular hiccup delays rows instead of losing them; only a queue overflow (roughly five
/// minutes offline) drops rows. When the session stops, a final drain runs and, if every row made it,
/// a `Receipt` is returned so Sessions can show the recording as already in Supabase.
///
/// Call `start`, `enqueue` and `stop` on the main thread.
final class LiveBroadcaster: ObservableObject {
    enum Status: Equatable {
        case off
        case starting
        case live
        case retrying(String)
        case draining

        var title: String {
            switch self {
            case .off: return "Off"
            case .starting: return "Opening live session…"
            case .live: return "LIVE"
            case .retrying: return "Reconnecting…"
            case .draining: return "Sending last rows…"
            }
        }
    }

    @Published private(set) var status: Status = .off
    @Published private(set) var sessionDate = ""
    @Published private(set) var sessionNumber = 0
    @Published private(set) var lastError: String?

    /// Rows confirmed inserted. Polled by the UI timer rather than published, to avoid 200 Hz re-renders.
    private(set) var sentRows = 0
    /// Rows thrown away because the queue overflowed while offline.
    private(set) var droppedRows = 0
    /// Rows still queued when the last session stopped that could not be delivered.
    private(set) var undeliveredRows = 0
    private(set) var retryCount = 0
    private(set) var isActive = false
    var pendingRows: Int { queue.count }

    private struct PendingRow {
        let recordedAt: String
        let ax: Double, ay: Double, az: Double, gx: Double, gy: Double, gz: Double
        let latitude: Double?, longitude: Double?
        let anomaly: Int
    }

    private var queue: [PendingRow] = []
    private var credentials: SupabaseUploader.Credentials?
    private let uploader = SupabaseUploader()
    private var flushTimer: Timer?
    private var inFlight = false
    private var backoff: TimeInterval = 1
    private var nextAttempt = Date.distantPast
    private var openTask: Task<Void, Never>?

    private let flushInterval: TimeInterval = 1.0
    private let maxBatch = 1000
    private let maxQueue = 60_000
    private let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()

    // MARK: - Lifecycle

    /// Opens a live session: picks the next session number for `sessionDate` (retrying until it works)
    /// and starts the once-a-second flush. Rows queued before the number is known are sent once it is.
    func start(credentials: SupabaseUploader.Credentials, sessionDate: String, minimumNumber: Int) {
        guard status != .draining else { return }
        reset()
        self.credentials = credentials
        self.sessionDate = sessionDate
        isActive = true
        status = .starting

        openTask = Task { @MainActor [weak self] in
            var attempt = 0
            while true {
                guard let self, self.isActive, self.sessionNumber == 0 else { return }
                do {
                    let next = try await self.uploader.nextSessionNumber(date: sessionDate, credentials: credentials)
                    self.sessionNumber = max(next, minimumNumber)
                    self.status = .live
                    self.lastError = nil
                    self.flush()
                } catch {
                    attempt += 1
                    self.lastError = error.localizedDescription
                    self.status = .retrying("Could not open live session: \(error.localizedDescription)")
                    let delay = min(10.0, pow(2.0, Double(attempt)))
                    try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                }
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
        queue.append(PendingRow(recordedAt: iso.string(from: s.receivedAt),
                                ax: s.ax, ay: s.ay, az: s.az, gx: s.gx, gy: s.gy, gz: s.gz,
                                latitude: lat, longitude: lon,
                                anomaly: s.obstructionDetected ? 1 : 0))
        if queue.count > maxQueue {
            // Drop the oldest rows that are not part of an in-flight batch (the first `maxBatch`).
            let excess = queue.count - maxQueue
            let dropStart = inFlight ? min(maxBatch, queue.count - excess) : 0
            queue.removeSubrange(dropStart..<(dropStart + excess))
            droppedRows += excess
        }
        if queue.count >= maxBatch { flush() }
    }

    /// Stops accepting rows and tries to deliver what is still queued. Returns a receipt only if every
    /// row of the session reached Supabase.
    @MainActor
    func stop() async -> SupabaseUploader.Receipt? {
        guard isActive else { return nil }
        isActive = false
        openTask?.cancel()
        openTask = nil
        flushTimer?.invalidate()
        flushTimer = nil
        status = .draining

        // Let an in-flight batch finish (URLSession times out after 60 s at most).
        var waited = 0
        while inFlight && waited < 320 {
            try? await Task.sleep(nanoseconds: 200_000_000)
            waited += 1
        }

        var attempts = 0
        while !queue.isEmpty, sessionNumber > 0, let creds = credentials, attempts < 5 {
            let n = min(maxBatch, queue.count)
            let readings = queue.prefix(n).map(makeReading)
            do {
                try await uploader.insert(readings, credentials: creds)
                queue.removeFirst(n)
                sentRows += n
            } catch {
                attempts += 1
                lastError = error.localizedDescription
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
        }

        undeliveredRows = queue.count
        let complete = queue.isEmpty && droppedRows == 0 && sentRows > 0 && sessionNumber > 0
        let receipt = complete
            ? SupabaseUploader.Receipt(sessionDate: sessionDate, sessionNumber: sessionNumber,
                                       rows: sentRows, uploadedAt: Date())
            : nil
        queue.removeAll()
        credentials = nil
        status = .off
        return receipt
    }

    // MARK: - Internals

    private func reset() {
        openTask?.cancel()
        flushTimer?.invalidate()
        flushTimer = nil
        queue.removeAll()
        credentials = nil
        sessionNumber = 0
        sentRows = 0
        droppedRows = 0
        undeliveredRows = 0
        retryCount = 0
        inFlight = false
        backoff = 1
        nextAttempt = .distantPast
        lastError = nil
    }

    private func flush() {
        guard !inFlight, sessionNumber > 0, let creds = credentials, !queue.isEmpty,
              Date() >= nextAttempt else { return }
        let n = min(maxBatch, queue.count)
        let readings = queue.prefix(n).map(makeReading)
        inFlight = true

        Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                try await self.uploader.insert(readings, credentials: creds)
                self.queue.removeFirst(n)
                self.sentRows += n
                self.backoff = 1
                self.lastError = nil
                if self.isActive { self.status = .live }
            } catch {
                self.retryCount += 1
                self.lastError = error.localizedDescription
                if self.isActive { self.status = .retrying(error.localizedDescription) }
                self.nextAttempt = Date().addingTimeInterval(self.backoff)
                self.backoff = min(self.backoff * 2, 15)
            }
            self.inFlight = false
            if self.isActive, self.queue.count >= self.maxBatch { self.flush() }
        }
    }

    private func makeReading(_ p: PendingRow) -> SupabaseUploader.Reading {
        SupabaseUploader.Reading(sessionDate: sessionDate, sessionNumber: sessionNumber, recordedAt: p.recordedAt,
                                 ax: p.ax, ay: p.ay, az: p.az, gx: p.gx, gy: p.gy, gz: p.gz,
                                 latitude: p.latitude, longitude: p.longitude, anomalyDetected: p.anomaly)
    }
}
