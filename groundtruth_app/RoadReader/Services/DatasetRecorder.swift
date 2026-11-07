import Foundation
import Combine

/// Appends labelled rows to a CSV file in the app's Documents folder (visible in the Files app).
final class DatasetRecorder: ObservableObject {
    @Published private(set) var currentFileURL: URL?

    private(set) var rowCount = 0
    private(set) var labeledCount = 0

    private var handle: FileHandle?
    private var buffer = Data()
    private let flushThreshold = 8 * 1024

    static var directory: URL {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let dir = docs.appendingPathComponent("RoadReader", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    @discardableResult
    func startSession() throws -> URL {
        endSession()
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd_HHmmss"
        let url = DatasetRecorder.directory.appendingPathComponent("session_\(formatter.string(from: Date())).csv")
        try (IMUSample.csvHeader + "\n").write(to: url, atomically: true, encoding: .utf8)
        handle = try FileHandle(forWritingTo: url)
        handle?.seekToEndOfFile()
        currentFileURL = url
        rowCount = 0
        labeledCount = 0
        return url
    }

    func append(_ sample: IMUSample) {
        guard handle != nil else { return }
        if let data = (sample.csvRow + "\n").data(using: .utf8) {
            buffer.append(data)
        }
        rowCount += 1
        if sample.obstructionDetected { labeledCount += 1 }
        if buffer.count >= flushThreshold { flush() }
    }

    func flush() {
        guard let handle, !buffer.isEmpty else { return }
        handle.write(buffer)
        buffer.removeAll(keepingCapacity: true)
    }

    func endSession() {
        flush()
        try? handle?.close()
        handle = nil
    }

    static func listSessions() -> [URL] {
        let urls = (try? FileManager.default.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: [.contentModificationDateKey, .fileSizeKey]
        )) ?? []
        return urls
            .filter { $0.pathExtension == "csv" }
            .sorted { a, b in
                let da = (try? a.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                let db = (try? b.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                return da > db
            }
    }

    static func delete(_ url: URL) {
        try? FileManager.default.removeItem(at: url)
    }
}
