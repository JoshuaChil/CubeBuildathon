import SwiftUI
import MapKit

// MARK: - Live card (Readings screen)

/// Live road condition while recording, polled from listener.py --analyze on the computer.
struct RoadAnalysisCard: View {
    @EnvironmentObject private var session: SessionViewModel

    private var client: AnalysisClient { session.analysis }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "road.lanes").foregroundStyle(.tint)
                Text("Road analysis").font(.subheadline.weight(.semibold))
                Spacer()
                connectionBadge
            }
            if let live = client.latest, live.status == "ok" {
                HStack(spacing: 8) {
                    ConditionPill(condition: live.current?.condition, iri: live.current?.iriMPerKm)
                    metric("Potholes", "\(live.potholes ?? 0)", tint: .red)
                    metric("Bumps", "\(live.bumps ?? 0)", tint: .blue)
                    metric("km", String(format: "%.2f", (live.distanceM ?? 0) / 1000))
                }
                if let e = live.events?.last {
                    Label("Last: \(e.title.lowercased()) \(Int(abs(e.magnitudeMm).rounded())) mm at \(Int(e.sM)) m",
                          systemImage: e.isDip ? "arrowtriangle.down.fill" : "arrowtriangle.up.fill")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if let note = live.notes?.first {
                    Text(note).font(.caption2).foregroundStyle(.orange).lineLimit(3)
                }
            } else {
                HStack(spacing: 8) {
                    if client.connection == .connecting || client.latest?.status == "collecting" {
                        ProgressView().controlSize(.small)
                    }
                    Text(statusText)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(3)
                }
            }
        }
        .padding(12)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))
    }

    private var statusText: String {
        switch client.connection {
        case .unreachable:
            return "Can't reach \(session.settings.forwardHost):\(session.settings.analysisPort). Run `python3 listener.py --analyze` on the computer."
        case .connecting:
            return "Connecting to the analysis…"
        default:
            return client.latest?.message ?? "Waiting for data…"
        }
    }

    @ViewBuilder
    private var connectionBadge: some View {
        switch client.connection {
        case .ok:
            Text(client.latest?.processingMs.map { "live · \($0) ms" } ?? "live")
                .font(.caption2).foregroundStyle(.green)
        case .unreachable:
            Text("offline").font(.caption2).foregroundStyle(.orange)
        case .connecting:
            Text("connecting").font(.caption2).foregroundStyle(.secondary)
        case .off:
            EmptyView()
        }
    }

    private func metric(_ title: String, _ value: String, tint: Color = .primary) -> some View {
        VStack(spacing: 1) {
            Text(value).font(.headline.monospacedDigit()).foregroundStyle(tint)
            Text(title).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }
}

/// "Poor · IRI 5.2" badge coloured by condition.
struct ConditionPill: View {
    let condition: String?
    let iri: Double?

    var body: some View {
        VStack(spacing: 1) {
            Text(RoadCondition.title(condition))
                .font(.subheadline.weight(.bold))
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            Text(iri.map { String(format: "IRI %.1f", $0) } ?? "IRI –")
                .font(.caption2.monospacedDigit())
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .frame(minWidth: 84)
        .background(RoadCondition.color(condition), in: RoundedRectangle(cornerRadius: 10))
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Session results (Sessions screen)

/// Map + summary + detected events for one uploaded session (GET /results/<device>/<session>).
struct SessionResultsView: View {
    let info: SessionInfo
    let analysis: UploadAnalysis?
    @EnvironmentObject private var session: SessionViewModel

    @State private var results: SessionResults?
    @State private var error: String?
    @State private var loading = false

    private var settings: AppSettings { session.settings }

    var body: some View {
        Group {
            if let results {
                List {
                    Section { map(results).frame(height: 300).listRowInsets(EdgeInsets()) }
                    Section("Summary") { summary(results) }
                    if !results.notes.isEmpty {
                        Section("Notes") {
                            ForEach(results.notes, id: \.self) { Text($0).font(.footnote) }
                        }
                    }
                    Section("Detected (\(results.events.count))") {
                        if results.events.isEmpty {
                            Text("No potholes or bumps over 20 mm.").foregroundStyle(.secondary)
                        }
                        ForEach(results.events) { e in eventRow(e) }
                    }
                    if let url = settings.analysisURL(kind: "report", session: info.name) {
                        Section("Full report") {
                            AsyncImage(url: url) { image in
                                image.resizable().scaledToFit()
                            } placeholder: {
                                ProgressView().frame(maxWidth: .infinity, minHeight: 80)
                            }
                        }
                    }
                }
            } else if loading {
                ProgressView("Loading analysis…")
            } else if !info.uploadedToServer {
                ContentUnavailableView("Not analysed yet", systemImage: "icloud.and.arrow.up",
                                       description: Text("Upload this session; the server analyses it and the results appear here."))
            } else {
                ContentUnavailableView("No analysis", systemImage: "exclamationmark.triangle",
                                       description: Text(error ?? analysis?.error ?? "The server has no analysis for this session."))
            }
        }
        .navigationTitle((info.startedAt ?? info.modified).formatted(date: .abbreviated, time: .shortened))
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        guard info.uploadedToServer, let url = settings.analysisURL(kind: "results", session: info.name) else { return }
        loading = true
        defer { loading = false }
        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else {
                error = "Server returned \((response as? HTTPURLResponse)?.statusCode ?? 0)."
                return
            }
            results = try RoadAnalysisJSON.decoder.decode(SessionResults.self, from: data)
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func map(_ r: SessionResults) -> some View {
        Map(initialPosition: .automatic) {
            ForEach(r.segments) { seg in
                let coords = r.coordinates(for: seg)
                if coords.count > 1 {
                    MapPolyline(coordinates: coords)
                        .stroke(RoadCondition.color(seg.condition), lineWidth: 6)
                }
            }
            ForEach(r.events) { e in
                if let c = e.coordinate {
                    Annotation(e.title, coordinate: c) {
                        Image(systemName: e.isDip ? "arrowtriangle.down.circle.fill" : "arrowtriangle.up.circle.fill")
                            .font(.title3)
                            .foregroundStyle(.white, e.isDip ? Color.red : Color.blue)
                    }
                }
            }
        }
    }

    private func summary(_ r: SessionResults) -> some View {
        let s = r.summary
        let condition = analysis?.condition
        return VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                ConditionPill(condition: condition, iri: s.meanIriMPerKm)
                stat("Distance", String(format: "%.2f km", (s.distanceM ?? 0) / 1000))
                stat("Potholes", "\(s.potholes ?? 0)")
                stat("Bumps", "\(s.bumps ?? 0)")
            }
            HStack(spacing: 4) {
                ForEach(["good", "fair", "poor", "very poor"], id: \.self) { c in
                    let share = r.segments.isEmpty ? 0 : Double(r.segments.filter { $0.condition == c }.count) / Double(r.segments.count)
                    VStack(spacing: 2) {
                        RoundedRectangle(cornerRadius: 3).fill(RoadCondition.color(c)).frame(height: 6)
                        Text("\(RoadCondition.title(c)) \(Int((share * 100).rounded()))%").font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
            Text("IRI = International Roughness Index (m/km), per 20 m. Good < 2, fair < 4, poor < 6.")
                .font(.caption2).foregroundStyle(.secondary)
        }
    }

    private func stat(_ title: String, _ value: String) -> some View {
        VStack(spacing: 1) {
            Text(value).font(.subheadline.weight(.semibold).monospacedDigit())
            Text(title).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }

    private func eventRow(_ e: RoadEvent) -> some View {
        HStack {
            Image(systemName: e.isDip ? "arrowtriangle.down.fill" : "arrowtriangle.up.fill")
                .foregroundStyle(e.isDip ? .red : .blue)
            VStack(alignment: .leading, spacing: 2) {
                Text("\(e.title) · \(Int(abs(e.magnitudeMm).rounded())) mm").font(.subheadline)
                Text(String(format: "at %.0f m%@", e.sM, e.lengthM.map { String(format: " · %.1f m long", $0) } ?? ""))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Gauge(value: e.severity) { EmptyView() }
                .gaugeStyle(.accessoryLinearCapacity)
                .tint(e.severity > 0.6 ? .red : (e.severity > 0.3 ? .orange : .yellow))
                .frame(width: 60)
        }
    }
}
