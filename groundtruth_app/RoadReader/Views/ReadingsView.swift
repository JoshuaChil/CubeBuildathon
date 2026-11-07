import SwiftUI

/// Connected state: Start Readings → live rows + Detect Bump.
struct ReadingsView: View {
    @EnvironmentObject private var session: SessionViewModel

    var body: some View {
        VStack(spacing: 12) {
            StatusCard()

            if session.isReading {
                StatsRow()
                if session.broadcaster.status != .off {
                    LiveBroadcastBar()
                }
                if session.settings.forwardingEnabled && session.settings.liveAnalysisEnabled {
                    RoadAnalysisCard()
                }
                LiveTable()
                DetectBumpButton(isActive: session.isMarking,
                                 onPress: session.beginMark,
                                 onRelease: session.endMark)
                Button(role: .destructive) {
                    session.stopReadings()
                } label: {
                    Label("Stop Readings", systemImage: "stop.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
            } else {
                Spacer()
                VStack(spacing: 16) {
                    Image(systemName: "waveform.path.ecg")
                        .font(.system(size: 56))
                        .foregroundStyle(.tint)
                    Text("Ready to record")
                        .font(.title3.weight(.semibold))
                    Text(readyDescription)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 24)
                }
                Spacer()

                if session.settings.supabaseCredentials != nil {
                    Toggle(isOn: Binding(
                        get: { session.settings.liveBroadcastEnabled },
                        set: { session.settings.liveBroadcastEnabled = $0 }
                    )) {
                        Label {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Live Broadcasting").font(.body.weight(.medium))
                                Text("Stream rows to Supabase in real time while recording")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        } icon: {
                            Image(systemName: "dot.radiowaves.left.and.right")
                                .foregroundStyle(session.settings.liveBroadcastEnabled ? .red : .secondary)
                        }
                    }
                    .tint(.red)
                    .padding(12)
                    .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
                }

                Button {
                    session.startReadings()
                } label: {
                    Label(session.settings.canBroadcastLive ? "Start Live Session" : "Start Readings",
                          systemImage: session.settings.canBroadcastLive ? "dot.radiowaves.left.and.right" : "play.fill")
                        .font(.title3.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 6)
                }
                .buttonStyle(.borderedProminent)
                .tint(session.settings.canBroadcastLive ? .red : .accentColor)
                .controlSize(.large)
                .disabled((!session.isDemo && !session.bluetooth.canStream) || !session.canStartReadings)

                if session.broadcaster.status == .draining {
                    Text("Finishing the previous live session…")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                if !session.isDemo && !session.bluetooth.canStream {
                    Text("Waiting for the device to expose a data characteristic…")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                if session.isDemo {
                    Button("Exit demo", role: .destructive) {
                        session.exitDemo()
                    }
                    .font(.footnote)
                    .padding(.top, 4)
                } else {
                    Button("Disconnect", role: .destructive) {
                        session.disconnect()
                    }
                    .font(.footnote)
                    .padding(.top, 4)
                }
            }
        }
        .padding()
    }

    private var readyDescription: String {
        var parts = ["Readings are saved to a CSV on this phone"]
        if session.settings.forwardingEnabled {
            parts.append("streamed to \(session.settings.forwardHost):\(session.settings.forwardPort)")
        }
        if session.settings.canBroadcastLive {
            parts.append("broadcast live to Supabase")
        }
        return parts.joined(separator: ", ") + "."
    }
}

// MARK: - Live broadcast bar

private struct LiveBroadcastBar: View {
    @EnvironmentObject private var session: SessionViewModel
    @State private var pulse = false

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(color)
                .frame(width: 10, height: 10)
                .opacity(pulse && session.broadcaster.status == .live ? 0.25 : 1)
                .animation(.easeInOut(duration: 0.7).repeatForever(autoreverses: true), value: pulse)
                .onAppear { pulse = true }
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(session.broadcaster.status.title)
                        .font(.caption.weight(.bold))
                        .foregroundStyle(color)
                    if session.broadcaster.sessionNumber > 0 {
                        Text("Session \(session.broadcaster.sessionDate) #\(session.broadcaster.sessionNumber)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Text(detail)
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            Spacer()
            Image(systemName: "dot.radiowaves.left.and.right")
                .foregroundStyle(color)
        }
        .padding(10)
        .background(color.opacity(0.1), in: RoundedRectangle(cornerRadius: 10))
    }

    private var color: Color {
        switch session.broadcaster.status {
        case .live: return .red
        case .retrying: return .orange
        case .off: return .gray
        case .starting, .draining: return .blue
        }
    }

    private var detail: String {
        var parts = ["\(session.broadcastSent.formatted()) rows in Supabase"]
        if session.broadcastPending > 0 { parts.append("\(session.broadcastPending.formatted()) queued") }
        if session.broadcastDropped > 0 { parts.append("\(session.broadcastDropped.formatted()) dropped") }
        if case .retrying(let message) = session.broadcaster.status { parts.append(message) }
        return parts.joined(separator: " · ")
    }
}

// MARK: - Status card

private struct StatusCard: View {
    @EnvironmentObject private var session: SessionViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Circle().fill(session.isDemo ? .purple : .green).frame(width: 10, height: 10)
                Text(session.isDemo ? "Demo ride (recorded sensor data)" : (session.bluetooth.state.deviceName ?? "Device"))
                    .font(.headline)
                Spacer()
                if session.isReading {
                    Text(session.isDemo ? "Playing \(Int(session.demo.progress * 100))%" : (session.bluetooth.isSubscribed ? "Streaming" : "Subscribing…"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            HStack(spacing: 16) {
                statusItem(icon: "location.fill", text: session.isDemo ? "GPS: recorded route" : gpsText, ok: session.isDemo || session.location.hasFix)
                statusItem(icon: "wifi", text: forwardText, ok: session.forwarder.status == .ready)
            }
            .font(.caption)
        }
        .padding()
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))
    }

    private var gpsText: String {
        if !session.location.isAuthorized { return "GPS: no permission" }
        guard let l = session.location.latest, session.location.hasFix else { return "GPS: acquiring…" }
        return String(format: "GPS ±%.0f m", l.horizontalAccuracy)
    }

    private var forwardText: String {
        guard session.settings.forwardingEnabled else { return "Forwarding off" }
        if !session.isReading { return "→ \(session.settings.forwardHost)" }
        return session.forwarder.status.title
    }

    private func statusItem(icon: String, text: String, ok: Bool) -> some View {
        HStack(spacing: 4) {
            Image(systemName: icon).foregroundStyle(ok ? .green : .secondary)
            Text(text).lineLimit(1)
        }
    }
}

// MARK: - Stats

private struct StatsRow: View {
    @EnvironmentObject private var session: SessionViewModel

    var body: some View {
        HStack(spacing: 8) {
            stat("Rows", "\(session.rowCount)")
            stat("Hz", String(format: "%.0f", session.rowsPerSecond))
            stat("Flagged", "\(session.labeledCount)", tint: .red)
            stat("Sent", "\(session.forwardedCount)")
        }
    }

    private func stat(_ title: String, _ value: String, tint: Color = .primary) -> some View {
        VStack(spacing: 2) {
            Text(value)
                .font(.title3.monospacedDigit().weight(.semibold))
                .foregroundStyle(tint)
            Text(title).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 10))
    }
}

// MARK: - Live table

private struct LiveTable: View {
    @EnvironmentObject private var session: SessionViewModel
    private let columns = ["ax", "ay", "az", "gx", "gy", "gz", "Bump"]

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                ForEach(columns, id: \.self) { c in
                    Text(c)
                        .frame(maxWidth: .infinity)
                        .foregroundStyle(.secondary)
                }
            }
            .font(.caption2.weight(.semibold))
            .padding(.vertical, 6)
            .background(Color(.tertiarySystemBackground))

            Divider()

            if session.recentSamples.isEmpty {
                VStack(spacing: 6) {
                    ProgressView()
                    Text(session.bluetooth.isSubscribed ? "Waiting for data from the sensor…" : "Subscribing to sensor…")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if session.parseErrors > 0 {
                        Text("Unparsed lines: \(session.parseErrors). Last: “\(session.lastRawLine)”")
                            .font(.caption2)
                            .foregroundStyle(.orange)
                            .lineLimit(2)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(Array(session.recentSamples.suffix(20).reversed())) { s in
                            row(s)
                        }
                    }
                }
                .scrollDisabled(true)
            }
        }
        .font(.system(.caption, design: .monospaced))
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Color(.separator)))
    }

    private func row(_ s: IMUSample) -> some View {
        HStack(spacing: 0) {
            ForEach([s.ax, s.ay, s.az, s.gx, s.gy, s.gz], id: \.self) { v in
                Text(String(format: "%.2f", v))
                    .frame(maxWidth: .infinity)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
            Text(s.obstructionDetected ? "1" : "0")
                .fontWeight(.bold)
                .foregroundStyle(s.obstructionDetected ? .white : .secondary)
                .frame(maxWidth: .infinity)
        }
        .padding(.vertical, 3)
        .background(s.obstructionDetected ? Color.red.opacity(0.75) : Color.clear)
    }
}

// MARK: - Detect Bump

/// Press-and-hold button. Rows are labelled 1 while held; a quick tap labels a short burst.
struct DetectBumpButton: View {
    let isActive: Bool
    let onPress: () -> Void
    let onRelease: () -> Void

    @State private var pressing = false

    var body: some View {
        RoundedRectangle(cornerRadius: 20)
            .fill(isActive ? Color.red : Color.orange)
            .frame(height: 120)
            .overlay(
                VStack(spacing: 4) {
                    Image(systemName: isActive ? "exclamationmark.triangle.fill" : "hand.tap.fill")
                        .font(.title)
                    Text(isActive ? "MARKING…" : "Detect Bump")
                        .font(.title2.weight(.bold))
                    Text("Tap or hold while crossing a crack or pothole")
                        .font(.caption)
                        .opacity(0.9)
                }
                .foregroundStyle(.white)
            )
            .scaleEffect(pressing ? 0.97 : 1)
            .animation(.easeOut(duration: 0.1), value: pressing)
            .animation(.easeOut(duration: 0.1), value: isActive)
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { _ in
                        if !pressing {
                            pressing = true
                            onPress()
                        }
                    }
                    .onEnded { _ in
                        pressing = false
                        onRelease()
                    }
            )
            .accessibilityLabel("Detect bump")
            .accessibilityHint("Hold while crossing an obstruction")
    }
}
