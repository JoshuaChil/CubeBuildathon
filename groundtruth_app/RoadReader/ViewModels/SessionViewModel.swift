import Foundation
import Combine
import CoreBluetooth
import CoreLocation
import UIKit

/// Glues Bluetooth → labelling → GPS tagging → CSV file + network forwarding.
/// Everything here runs on the main thread (CoreBluetooth and CoreLocation deliver on main).
final class SessionViewModel: ObservableObject {
    let settings = AppSettings()
    let bluetooth = BluetoothManager()
    let location = LocationManager()
    let forwarder = StreamForwarder()
    let recorder = DatasetRecorder()
    /// Live road analysis from the computer (listener.py --analyze).
    let analysis = AnalysisClient()
    /// Recorded ride played back instead of the sensor (no ESP32 needed).
    let demo = DemoRide()
    /// Live Broadcasting to the GroundTruth dashboard server (Settings > Live dashboard).
    let broadcaster = DashboardStreamer()

    @Published private(set) var isReading = false
    /// True while the app runs on the bundled demo ride instead of a Bluetooth sensor.
    @Published private(set) var isDemo = false
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
    @Published private(set) var broadcastSent = 0
    @Published private(set) var broadcastPending = 0
    @Published private(set) var broadcastDropped = 0
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
        demo.onLine = { [weak self] line, fix in self?.handle(line: line, location: fix) }
        demo.onFinished = { [weak self] in self?.stopReadings() }
        location.requestAuthorization()

        // Re-publish child changes so views observing the session update too.
        for publisher in [bluetooth.objectWillChange.eraseToAnyPublisher(),
                          location.objectWillChange.eraseToAnyPublisher(),
                          forwarder.objectWillChange.eraseToAnyPublisher(),
                          recorder.objectWillChange.eraseToAnyPublisher(),
                          analysis.objectWillChange.eraseToAnyPublisher(),
                          broadcaster.objectWillChange.eraseToAnyPublisher(),
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

    /// Start is blocked while the previous live session is still sending its last rows.
    var canStartReadings: Bool { !isReading && broadcaster.status != .draining }

    func startReadings() {
        guard canStartReadings else { return }
        do {
            try recorder.startSession()
        } catch {
            errorMessage = "Could not create dataset file: \(error.localizedDescription)"
            return
        }
        if settings.forwardingEnabled {
            forwarder.start(host: settings.forwardHost, port: settings.forwardPort, proto: settings.forwardProtocol)
            if settings.liveAnalysisEnabled {
                analysis.start(host: settings.forwardHost, port: settings.analysisPort, vehicle: settings.vehicleType)
            }
        }
        location.requestAuthorization()
        location.start()
        if isDemo {
            do {
                try demo.start()
            } catch {
                errorMessage = error.localizedDescription
                recorder.endSession()
                forwarder.stop()
                analysis.stop()
                return
            }
        } else {
            bluetooth.startStreaming()
        }

        if settings.canBroadcastLive, let url = settings.dashboardEndpoint {
            let name = recorder.currentFileURL?.deletingPathExtension().lastPathComponent ?? "session"
            broadcaster.start(baseURL: url, session: name, deviceID: settings.deviceID)
        }
        broadcastSent = 0
        broadcastPending = 0
        broadcastDropped = 0

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
        if isDemo {
            demo.stop()
        } else {
            bluetooth.stopStreaming()
        }
        recorder.endSession()
        forwarder.stop()
        analysis.stop()
        if broadcaster.isActive {
            Task { @MainActor [weak self] in
                guard let self else { return }
                let complete = await self.broadcaster.stop()
                if !complete {
                    let missing = self.broadcaster.undeliveredRows + self.broadcaster.droppedRows
                    if missing > 0 {
                        self.errorMessage = "Live broadcast ended with \(missing) rows not delivered to the dashboard. The full session is still saved on this phone."
                    }
                }
                self.broadcastSent = self.broadcaster.sentRows
                self.broadcastPending = 0
            }
        }
        cancelMark()
        isReading = false
        UIApplication.shared.isIdleTimerDisabled = false
        stopTimer()
        tick()
        rowsPerSecond = 0
    }

    /// Use the bundled demo ride instead of a sensor (Scan screen → Demo ride).
    func enterDemo() {
        guard !isReading else { return }
        isDemo = true
    }

    func exitDemo() {
        stopReadings()
        isDemo = false
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

    /// `location` overrides the phone's GPS (the demo ride carries its own recorded fixes).
    private func handle(line: String, location fix: CLLocation? = nil) {
        pendingRawLine = line
        guard isReading else { return }
        guard let sample = IMUSample.parse(line, location: fix ?? location.latest, detected: isMarking) else {
            pendingParseErrors += 1
            return
        }
        recorder.append(sample)
        if settings.forwardingEnabled {
            forwarder.send(sample.csvRow)
        }
        if broadcaster.isActive {
            broadcaster.enqueue(sample)
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
        if broadcaster.isActive {
            if broadcastSent != broadcaster.sentRows { broadcastSent = broadcaster.sentRows }
            if broadcastPending != broadcaster.pendingRows { broadcastPending = broadcaster.pendingRows }
            if broadcastDropped != broadcaster.droppedRows { broadcastDropped = broadcaster.droppedRows }
        }
    }
}
