import SwiftUI

/// Connected state: Start Readings → live rows + Detect Bump.
struct ReadingsView: View {
    @EnvironmentObject private var session: SessionViewModel

    var body: some View {
        VStack(spacing: 12) {
            StatusCard()

            if session.isReading {
                StatsRow()
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
                    Text("Readings are saved to a CSV on this phone and streamed to \(session.settings.forwardingEnabled ? "\(session.settings.forwardHost):\(session.settings.forwardPort)" : "nowhere (forwarding off)").")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 24)
                }
                Spacer()
                Button {
                    session.startReadings()
                } label: {
                    Label("Start Readings", systemImage: "play.fill")
                        .font(.title3.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 6)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(!session.bluetooth.canStream)

                if !session.bluetooth.canStream {
                    Text("Waiting for the device to expose a data characteristic…")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Button("Disconnect", role: .destructive) {
                    session.disconnect()
                }
                .font(.footnote)
                .padding(.top, 4)
            }
        }
        .padding()
    }
}

// MARK: - Status card

private struct StatusCard: View {
    @EnvironmentObject private var session: SessionViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Circle().fill(.green).frame(width: 10, height: 10)
                Text(session.bluetooth.state.deviceName ?? "Device")
                    .font(.headline)
                Spacer()
                if session.isReading {
                    Text(session.bluetooth.isSubscribed ? "Streaming" : "Subscribing…")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            HStack(spacing: 16) {
                statusItem(icon: "location.fill", text: gpsText, ok: session.location.hasFix)
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
