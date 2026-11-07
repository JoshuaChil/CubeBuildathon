import Foundation
import Combine
import CoreBluetooth
import UIKit

/// Glues Bluetooth → labelling → GPS tagging → CSV file + network forwarding.
/// Everything here runs on the main thread (CoreBluetooth and CoreLocation deliver on main).
final class SessionViewModel: ObservableObject {
    let settings = AppSettings()
    let bluetooth = BluetoothManager()
    let location = LocationManager()
    let forwarder = StreamForwarder()
    let recorder = DatasetRecorder()

    @Published private(set) var isReading = false
    /// True while rows should be labelled "Crack/Pothole Detected" = 1.
    @Published private(set) var isMarking = false
    @Published private(set) var recentSamples: [IMUSample] = []
    @Published private(set) var rowCount = 0
    @Published private(set) var labeledCount = 0
    @Published private(set) var rowsPerSecond = 0.0
    @Published private(set) var forwardedCount = 0
    @Published private(set) var droppedCount = 0
    @Published private(set) var parseErrors = 0
    @Published private(set) var lastRawLine = ""
    @Published var errorMessage: String?

    private var pending: [IMUSample] = []
    private var pendingParseErrors = 0
    private var pendingRawLine = ""
    private var countSinceTick = 0
    private var uiTimer: Timer?
    private let tickInterval = 0.1
    private let maxRecent = 60

    private var markStart: Date?
    private var markReleaseWorkItem: DispatchWorkItem?
    private var cancellables = Set<AnyCancellable>()

    init() {
        bluetooth.deviceNamePrefix = settings.deviceNamePrefix
        bluetooth.onLine = { [weak self] line in self?.handle(line: line) }
        location.requestAuthorization()

        // Re-publish child changes so views observing the session update too.
        for publisher in [bluetooth.objectWillChange.eraseToAnyPublisher(),
                          location.objectWillChange.eraseToAnyPublisher(),
                          forwarder.objectWillChange.eraseToAnyPublisher(),
                          recorder.objectWillChange.eraseToAnyPublisher(),
                          settings.objectWillChange.eraseToAnyPublisher()] {
            publisher
                .receive(on: RunLoop.main)
                .sink { [weak self] _ in self?.objectWillChange.send() }
                .store(in: &cancellables)
        }

        settings.$deviceNamePrefix
            .sink { [weak self] in self?.bluetooth.deviceNamePrefix = $0 }
            .store(in: &cancellables)
        settings.$serviceUUID
            .sink { [weak self] uuid in
                let trimmed = uuid.trimmingCharacters(in: .whitespaces)
                if UUID(uuidString: trimmed) != nil || trimmed.count == 4 {
                    self?.bluetooth.serviceUUID = CBUUID(string: trimmed)
                }
            }
            .store(in: &cancellables)
    }

    // MARK: - Reading session

    func startReadings() {
        guard !isReading else { return }
        do {
            try recorder.startSession()
        } catch {
            errorMessage = "Could not create dataset file: \(error.localizedDescription)"
            return
        }
        if settings.forwardingEnabled {
            forwarder.start(host: settings.forwardHost, port: settings.forwardPort, proto: settings.forwardProtocol)
        }
        location.requestAuthorization()
        location.start()
        bluetooth.startStreaming()

        recentSamples.removeAll()
        pending.removeAll()
        rowCount = 0
        labeledCount = 0
        forwardedCount = 0
        droppedCount = 0
        parseErrors = 0
        pendingParseErrors = 0
        rowsPerSecond = 0
        countSinceTick = 0
        isReading = true
        UIApplication.shared.isIdleTimerDisabled = true
        startTimer()
    }

    func stopReadings() {
        guard isReading else { return }
        bluetooth.stopStreaming()
        recorder.endSession()
        forwarder.stop()
        cancelMark()
        isReading = false
        UIApplication.shared.isIdleTimerDisabled = false
        stopTimer()
        tick()
        rowsPerSecond = 0
    }

    func disconnect() {
        stopReadings()
        bluetooth.disconnect()
    }

    // MARK: - Detect Bump (hold or tap)

    /// Finger down on "Detect Bump": label rows with 1 from now on.
    func beginMark() {
        markReleaseWorkItem?.cancel()
        markReleaseWorkItem = nil
        markStart = Date()
        isMarking = true
        UIImpactFeedbackGenerator(style: .heavy).impactOccurred()
    }

    /// Finger up: stop labelling, but keep a quick tap alive for `minMarkDuration`
    /// so a single hit still labels a sensible run of rows.
    func endMark() {
        guard let start = markStart else { return }
        markStart = nil
        let held = Date().timeIntervalSince(start)
        let remaining = settings.minMarkDuration - held
        if remaining > 0 {
            let item = DispatchWorkItem { [weak self] in self?.isMarking = false }
            markReleaseWorkItem = item
            DispatchQueue.main.asyncAfter(deadline: .now() + remaining, execute: item)
        } else {
            isMarking = false
        }
    }

    private func cancelMark() {
        markReleaseWorkItem?.cancel()
        markReleaseWorkItem = nil
        markStart = nil
        isMarking = false
    }

    // MARK: - Incoming data

    private func handle(line: String) {
        pendingRawLine = line
        guard isReading else { return }
        guard let sample = IMUSample.parse(line, location: location.latest, detected: isMarking) else {
            pendingParseErrors += 1
            return
        }
        recorder.append(sample)
        if settings.forwardingEnabled {
            forwarder.send(sample.csvRow)
        }
        pending.append(sample)
        countSinceTick += 1
    }

    // MARK: - UI refresh (10 Hz, so SwiftUI is not re-rendered per row)

    private func startTimer() {
        stopTimer()
        let timer = Timer(timeInterval: tickInterval, repeats: true) { [weak self] _ in self?.tick() }
        RunLoop.main.add(timer, forMode: .common)
        uiTimer = timer
    }

    private func stopTimer() {
        uiTimer?.invalidate()
        uiTimer = nil
    }

    private func tick() {
        if !pending.isEmpty {
            recentSamples.append(contentsOf: pending)
            if recentSamples.count > maxRecent {
                recentSamples.removeFirst(recentSamples.count - maxRecent)
            }
            pending.removeAll(keepingCapacity: true)
        }
        if pendingParseErrors != parseErrors { parseErrors = pendingParseErrors }
        if pendingRawLine != lastRawLine { lastRawLine = pendingRawLine }

        let instantaneous = Double(countSinceTick) / tickInterval
        countSinceTick = 0
        rowsPerSecond = rowsPerSecond == 0 ? instantaneous : (0.8 * rowsPerSecond + 0.2 * instantaneous)

        rowCount = recorder.rowCount
        labeledCount = recorder.labeledCount
        let c = forwarder.counters()
        forwardedCount = c.sent
        droppedCount = c.dropped
    }
}
