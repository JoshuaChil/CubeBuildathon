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
    var isUploaded: Bool

    var id: String { name }
}

/// Lists saved sessions, computes their stats off the main thread, and uploads them.
final class SessionStore: ObservableObject {
    @Published private(set) var sessions: [SessionInfo] = []
    @Published private(set) var isLoading = false
    @Published private(set) var uploading: Set<String> = []
    @Published var errorMessage: String?
    /// Road analysis returned by the server for each uploaded session (by session name).
    @Published private(set) var analyses: [String: UploadAnalysis] = SessionStore.loadAnalyses()

    private let uploadedKey = "uploadedSessionNames"
    private static let analysesKey = "sessionAnalyses"

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
    private var cache: [String: SessionInfo] = [:]

    var pendingUploads: [SessionInfo] { sessions.filter { !$0.isUploaded } }

    // MARK: - Loading

    func reload() {
        let urls = DatasetRecorder.listSessions()
        let uploaded = uploadedNames
        let cached = cache
        isLoading = true

        DispatchQueue.global(qos: .userInitiated).async {
            var result: [SessionInfo] = []
            for url in urls {
                let values = try? url.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey])
                let bytes = values?.fileSize ?? 0
                let modified = values?.contentModificationDate ?? .distantPast
                let name = url.lastPathComponent

                // Reuse stats if the file has not changed since we last scanned it.
                if let c = cached[name], c.bytes == bytes, c.modified == modified {
                    var info = c
                    info.isUploaded = uploaded.contains(name)
                    result.append(info)
                    continue
                }

                let stats = SessionStore.scan(url)
                result.append(SessionInfo(url: url, name: name, bytes: bytes, modified: modified,
                                          rows: stats.rows, flagged: stats.flagged,
                                          duration: stats.duration, startedAt: stats.startedAt,
                                          isUploaded: uploaded.contains(name)))
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

    // MARK: - Upload bookkeeping

    private var uploadedNames: Set<String> {
        get { Set(UserDefaults.standard.stringArray(forKey: uploadedKey) ?? []) }
        set { UserDefaults.standard.set(Array(newValue), forKey: uploadedKey) }
    }

    private func markUploaded(_ name: String, _ value: Bool) {
        var names = uploadedNames
        if value { names.insert(name) } else { names.remove(name) }
        uploadedNames = names
        if let i = sessions.firstIndex(where: { $0.name == name }) {
            sessions[i].isUploaded = value
            cache[name] = sessions[i]
        }
    }

    func delete(_ info: SessionInfo) {
        DatasetRecorder.delete(info.url)
        markUploaded(info.name, false)
        setAnalysis(nil, for: info.name)
        cache[info.name] = nil
        sessions.removeAll { $0.name == info.name }
    }

    // MARK: - Uploading

    func upload(_ info: SessionInfo, settings: AppSettings) {
        guard !uploading.contains(info.name) else { return }
        guard let endpoint = URL(string: settings.uploadURL.trimmingCharacters(in: .whitespaces)),
              endpoint.scheme?.hasPrefix("http") == true else {
            errorMessage = "Set a valid upload URL in Settings first."
            return
        }
        uploading.insert(info.name)
        Task {
            do {
                let analysis = try await uploader.upload(info, to: endpoint, apiKey: settings.uploadAPIKey,
                                                         deviceID: settings.deviceID, vehicle: settings.vehicleType)
                await MainActor.run {
                    self.markUploaded(info.name, true)
                    self.setAnalysis(analysis, for: info.name)
                    self.uploading.remove(info.name)
                }
            } catch {
                await MainActor.run {
                    self.errorMessage = "\(info.name): \(error.localizedDescription)"
                    self.uploading.remove(info.name)
                }
            }
        }
    }

    func uploadAllPending(settings: AppSettings) {
        for info in pendingUploads { upload(info, settings: settings) }
    }
}
