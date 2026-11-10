import Foundation
import Combine

/// Summary of one recorded CSV session on the device.
struct SessionInfo: Identifiable, Hashable {
    let url: URL
    let name: String
    let bytes: Int
    let modified: Date
    let rows: Int
    let flagged: Int
    let duration: TimeInterval
    let startedAt: Date?
    /// Sent to the custom upload server (Settings > Web database).
    var uploadedToServer: Bool
    /// Inserted into Supabase (Settings > Supabase); nil until it is.
    var supabase: SupabaseUploader.Receipt?
    /// True once every destination configured in Settings has this session.
    var isUploaded: Bool

    var id: String { name }
}

/// Lists saved sessions, computes their stats off the main thread, and uploads them.
///
/// Two upload destinations, each tracked separately so a retry only sends what is still missing:
///   - Supabase (one table row per reading)        -> SupabaseUploader
///   - the multipart upload server (CSV + analysis) -> SessionUploader
final class SessionStore: ObservableObject {
    /// Where "Upload" sends sessions, derived from AppSettings.
    struct Destinations: Equatable {
        let server: URL?
        let supabase: SupabaseUploader.Credentials?

        init() { server = nil; supabase = nil }

        init(settings: AppSettings) {
            let text = settings.uploadURL.trimmingCharacters(in: .whitespaces)
            if let url = URL(string: text), url.scheme?.hasPrefix("http") == true { server = url } else { server = nil }
            supabase = settings.supabaseCredentials
        }

        var isEmpty: Bool { server == nil && supabase == nil }

        func isComplete(_ info: SessionInfo) -> Bool {
            (server == nil || info.uploadedToServer) && (supabase == nil || info.supabase != nil)
        }
    }

    @Published private(set) var sessions: [SessionInfo] = []
    @Published private(set) var isLoading = false
    @Published private(set) var uploading: Set<String> = []
    /// Fraction of rows inserted into Supabase so far, per session being uploaded.
    @Published private(set) var progress: [String: Double] = [:]
    @Published var errorMessage: String?
    /// Road analysis returned by the upload server for each uploaded session (by session name).
    @Published private(set) var analyses: [String: UploadAnalysis] = SessionStore.loadAnalyses()
    @Published private(set) var destinations = Destinations()

    private let uploadedKey = "uploadedSessionNames"
    private static let analysesKey = "sessionAnalyses"
    private static let receiptsKey = "supabaseReceipts"

    private static func loadAnalyses() -> [String: UploadAnalysis] {
        guard let data = UserDefaults.standard.data(forKey: analysesKey) else { return [:] }
        return (try? JSONDecoder().decode([String: UploadAnalysis].self, from: data)) ?? [:]
    }

    private func setAnalysis(_ analysis: UploadAnalysis?, for name: String) {
        analyses[name] = analysis
        if let data = try? JSONEncoder().encode(analyses) {
            UserDefaults.standard.set(data, forKey: Self.analysesKey)
        }
    }

    private let uploader = SessionUploader()
    private let supabaseUploader = SupabaseUploader()
    private var cache: [String: SessionInfo] = [:]

    var pendingUploads: [SessionInfo] { sessions.filter { !$0.isUploaded } }

    // MARK: - Loading

    func reload(settings: AppSettings) {
        destinations = Destinations(settings: settings)
        let urls = DatasetRecorder.listSessions()
        let uploaded = uploadedNames
        let receipts = supabaseReceipts
        let dest = destinations
        let cached = cache
        isLoading = true

        DispatchQueue.global(qos: .userInitiated).async {
            var result: [SessionInfo] = []
            for url in urls {
                let values = try? url.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey])
                let bytes = values?.fileSize ?? 0
                let modified = values?.contentModificationDate ?? .distantPast
                let name = url.lastPathComponent

                var info: SessionInfo
                // Reuse stats if the file has not changed since we last scanned it.
                if let c = cached[name], c.bytes == bytes, c.modified == modified {
                    info = c
                } else {
                    let stats = SessionStore.scan(url)
                    info = SessionInfo(url: url, name: name, bytes: bytes, modified: modified,
                                       rows: stats.rows, flagged: stats.flagged,
                                       duration: stats.duration, startedAt: stats.startedAt,
                                       uploadedToServer: false, supabase: nil, isUploaded: false)
                }
                info.uploadedToServer = uploaded.contains(name)
                info.supabase = receipts[name]
                info.isUploaded = dest.isComplete(info)
                result.append(info)
            }
            DispatchQueue.main.async {
                self.cache = Dictionary(uniqueKeysWithValues: result.map { ($0.name, $0) })
                self.sessions = result
                self.isLoading = false
            }
        }
    }

    /// Counts rows and flagged rows, and derives duration from the phone_time_ms column.
    private static func scan(_ url: URL) -> (rows: Int, flagged: Int, duration: TimeInterval, startedAt: Date?) {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return (0, 0, 0, nil) }
        var rows = 0, flagged = 0
        var firstMs: Double?, lastMs: Double?
        var isHeader = true

        text.enumerateLines { line, _ in
            if isHeader { isHeader = false; return }
            if line.isEmpty { return }
            rows += 1
            let fields = line.split(separator: ",", omittingEmptySubsequences: false)
            if fields.count > 6, fields[6] == "1" { flagged += 1 }
            if fields.count > 7, let ms = Double(fields[7]) {
                if firstMs == nil { firstMs = ms }
                lastMs = ms
            }
        }

        let duration = (firstMs != nil && lastMs != nil) ? (lastMs! - firstMs!) / 1000 : 0
        let startedAt = firstMs.map { Date(timeIntervalSince1970: $0 / 1000) }
        return (rows, flagged, duration, startedAt)
    }

    // MARK: - Upload bookkeeping (persisted in UserDefaults)

    private var uploadedNames: Set<String> {
        get { Set(UserDefaults.standard.stringArray(forKey: uploadedKey) ?? []) }
        set { UserDefaults.standard.set(Array(newValue), forKey: uploadedKey) }
    }

    private var supabaseReceipts: [String: SupabaseUploader.Receipt] {
        get { Self.storedReceipts() }
        set { Self.storeReceipts(newValue) }
    }

    static func storedReceipts() -> [String: SupabaseUploader.Receipt] {
        guard let data = UserDefaults.standard.data(forKey: receiptsKey) else { return [:] }
        return (try? JSONDecoder().decode([String: SupabaseUploader.Receipt].self, from: data)) ?? [:]
    }

    private static func storeReceipts(_ receipts: [String: SupabaseUploader.Receipt]) {
        if let data = try? JSONEncoder().encode(receipts) {
            UserDefaults.standard.set(data, forKey: receiptsKey)
        }
    }

    /// Highest session number this phone has already used for `date` (0 if none).
    private func localMaxSessionNumber(date: String) -> Int {
        Self.storedReceipts().values.filter { $0.sessionDate == date }.map(\.sessionNumber).max() ?? 0
    }

    private func update(_ name: String, _ change: (inout SessionInfo) -> Void) {
        guard let i = sessions.firstIndex(where: { $0.name == name }) else { return }
        change(&sessions[i])
        sessions[i].isUploaded = destinations.isComplete(sessions[i])
        cache[name] = sessions[i]
    }

    private func markServerUploaded(_ name: String, _ value: Bool) {
        var names = uploadedNames
        if value { names.insert(name) } else { names.remove(name) }
        uploadedNames = names
        update(name) { $0.uploadedToServer = value }
    }

    private func setReceipt(_ receipt: SupabaseUploader.Receipt?, for name: String) {
        var receipts = supabaseReceipts
        receipts[name] = receipt
        supabaseReceipts = receipts
        update(name) { $0.supabase = receipt }
    }

    func delete(_ info: SessionInfo) {
        DatasetRecorder.delete(info.url)
        markServerUploaded(info.name, false)
        setReceipt(nil, for: info.name)
        setAnalysis(nil, for: info.name)
        cache[info.name] = nil
        sessions.removeAll { $0.name == info.name }
    }

    // MARK: - Uploading

    /// Sends the session to every configured destination it has not reached yet.
    func upload(_ info: SessionInfo, settings: AppSettings) {
        guard !uploading.contains(info.name) else { return }
        destinations = Destinations(settings: settings)
        let dest = destinations
        guard !dest.isEmpty else {
            errorMessage = "Set up Supabase or an upload URL in Settings first."
            return
        }
        let info = sessions.first { $0.name == info.name } ?? info
        guard !dest.isComplete(info) else {
            update(info.name) { _ in }
            return
        }

        let sessionDate = SupabaseUploader.sessionDate(for: info)
        let minimumNumber = localMaxSessionNumber(date: sessionDate) + 1
        uploading.insert(info.name)

        Task {
            do {
                if let creds = dest.supabase, info.supabase == nil {
                    let receipt = try await supabaseUploader.upload(
                        info, credentials: creds, sessionDate: sessionDate, minimumNumber: minimumNumber
                    ) { done, total in
                        Task { @MainActor in self.progress[info.name] = Double(done) / Double(max(total, 1)) }
                    }
                    await MainActor.run { self.setReceipt(receipt, for: info.name) }
                }
                if let endpoint = dest.server, !info.uploadedToServer {
                    let analysis = try await uploader.upload(info, to: endpoint, apiKey: settings.uploadAPIKey,
                                                             deviceID: settings.deviceID, vehicle: settings.vehicleType)
                    await MainActor.run {
                        self.markServerUploaded(info.name, true)
                        self.setAnalysis(analysis, for: info.name)
                    }
                }
            } catch {
                await MainActor.run { self.errorMessage = "\(info.name): \(error.localizedDescription)" }
            }
            await MainActor.run {
                self.uploading.remove(info.name)
                self.progress[info.name] = nil
            }
        }
    }

    func uploadAllPending(settings: AppSettings) {
        for info in pendingUploads { upload(info, settings: settings) }
    }
}
