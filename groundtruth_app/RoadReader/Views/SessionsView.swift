import SwiftUI

/// Saved sessions on this device: stats, share, delete, and upload to the web database.
struct SessionsView: View {
    @EnvironmentObject private var session: SessionViewModel
    @Environment(\.dismiss) private var dismiss
    @StateObject private var store = SessionStore()
    @State private var pendingDelete: SessionInfo?

    private var settings: AppSettings { session.settings }
    private var hasEndpoint: Bool { !SessionStore.Destinations(settings: settings).isEmpty }

    var body: some View {
        NavigationStack {
            Group {
                if store.sessions.isEmpty && !store.isLoading {
                    ContentUnavailableView("No sessions yet",
                                           systemImage: "doc.text",
                                           description: Text("Tap Start Readings to record. Sessions are saved here and in the Files app under Road Reader."))
                } else {
                    List {
                        summarySection
                        Section("On this device") {
                            ForEach(store.sessions) { info in
                                NavigationLink(value: info) {
                                    SessionRow(info: info,
                                               analysis: store.analyses[info.name],
                                               isUploading: store.uploading.contains(info.name),
                                               progress: store.progress[info.name],
                                               canUpload: hasEndpoint,
                                               onUpload: { store.upload(info, settings: settings) })
                                }
                                .swipeActions(edge: .trailing) {
                                    Button(role: .destructive) { pendingDelete = info } label: {
                                        Label("Delete", systemImage: "trash")
                                    }
                                }
                                .contextMenu {
                                    ShareLink(item: info.url) { Label("Share CSV", systemImage: "square.and.arrow.up") }
                                    if !info.isUploaded {
                                        Button { store.upload(info, settings: settings) } label: {
                                            Label("Upload", systemImage: "icloud.and.arrow.up")
                                        }
                                        .disabled(!hasEndpoint)
                                    }
                                    Button(role: .destructive) { pendingDelete = info } label: {
                                        Label("Delete", systemImage: "trash")
                                    }
                                }
                            }
                        }
                    }
                    .refreshable { store.reload(settings: settings) }
                    .navigationDestination(for: SessionInfo.self) { info in
                        SessionResultsView(info: info, analysis: store.analyses[info.name])
                    }
                }
            }
            .navigationTitle("Sessions")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        store.uploadAllPending(settings: settings)
                    } label: {
                        Label("Upload All", systemImage: "icloud.and.arrow.up")
                    }
                    .disabled(!hasEndpoint || store.pendingUploads.isEmpty || !store.uploading.isEmpty)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .onAppear { store.reload(settings: settings) }
            .alert("Upload failed", isPresented: Binding(
                get: { store.errorMessage != nil },
                set: { if !$0 { store.errorMessage = nil } }
            )) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(store.errorMessage ?? "")
            }
            .confirmationDialog("Delete \(pendingDelete?.name ?? "")?",
                                isPresented: Binding(get: { pendingDelete != nil },
                                                     set: { if !$0 { pendingDelete = nil } }),
                                titleVisibility: .visible) {
                Button("Delete", role: .destructive) {
                    if let info = pendingDelete { store.delete(info) }
                    pendingDelete = nil
                }
                Button("Cancel", role: .cancel) { pendingDelete = nil }
            } message: {
                Text(pendingDelete?.isUploaded == true
                     ? "This session was already uploaded."
                     : "This session has not been uploaded yet.")
            }
        }
    }

    private var summarySection: some View {
        Section {
            let total = store.sessions.count
            let uploaded = store.sessions.filter(\.isUploaded).count
            let rows = store.sessions.reduce(0) { $0 + $1.rows }
            let flagged = store.sessions.reduce(0) { $0 + $1.flagged }
            HStack {
                summaryItem("Sessions", "\(total)")
                summaryItem("Uploaded", "\(uploaded)/\(total)")
                summaryItem("Rows", rows.formatted())
                summaryItem("Flagged", flagged.formatted(), tint: .red)
            }
            if !hasEndpoint {
                Label("Set up Supabase (or an upload URL) in Settings to enable uploads.", systemImage: "info.circle")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func summaryItem(_ title: String, _ value: String, tint: Color = .primary) -> some View {
        VStack(spacing: 2) {
            Text(value).font(.headline.monospacedDigit()).foregroundStyle(tint)
            Text(title).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }
}

private struct SessionRow: View {
    let info: SessionInfo
    let analysis: UploadAnalysis?
    let isUploading: Bool
    let progress: Double?
    let canUpload: Bool
    let onUpload: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.body.weight(.medium))
                Text(statsLine)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                if let a = analysis {
                    analysisLine(a)
                }
                if let r = info.supabase {
                    Label("Supabase \(r.sessionDate) · session \(r.sessionNumber) · \(r.rows.formatted()) rows",
                          systemImage: "cylinder")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Text(info.name)
                    .font(.caption2.monospaced())
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
            }
            Spacer()
            trailing
        }
        .padding(.vertical, 2)
    }

    @ViewBuilder
    private var trailing: some View {
        if isUploading {
            if let progress {
                ProgressView(value: progress)
                    .progressViewStyle(.circular)
            } else {
                ProgressView()
            }
        } else if info.isUploaded {
            Label("Uploaded", systemImage: "checkmark.circle.fill")
                .labelStyle(.iconOnly)
                .foregroundStyle(.green)
                .font(.title3)
                .accessibilityLabel("Uploaded")
        } else {
            Button(action: onUpload) {
                Label("Upload", systemImage: "icloud.and.arrow.up")
                    .font(.caption.weight(.semibold))
            }
            .buttonStyle(.bordered) // non-plain style keeps it tappable on its own inside the NavigationLink row
            .controlSize(.small)
            .disabled(!canUpload)
        }
    }

    private var title: String {
        (info.startedAt ?? info.modified).formatted(date: .abbreviated, time: .shortened)
    }

    @ViewBuilder
    private func analysisLine(_ a: UploadAnalysis) -> some View {
        if a.ok {
            HStack(spacing: 4) {
                Circle().fill(RoadCondition.color(a.condition)).frame(width: 8, height: 8)
                Text("\(RoadCondition.title(a.condition)) · IRI \(a.meanIriMPerKm.map { String(format: "%.1f", $0) } ?? "–") · \(a.potholes ?? 0) potholes · \(String(format: "%.2f km", (a.distanceM ?? 0) / 1000))")
                    .font(.caption.weight(.medium))
            }
        } else {
            Text("Analysis failed: \(a.error ?? "unknown")")
                .font(.caption)
                .foregroundStyle(.orange)
                .lineLimit(1)
        }
    }

    private var statsLine: String {
        let size = ByteCountFormatter.string(fromByteCount: Int64(info.bytes), countStyle: .file)
        let dur = Duration.seconds(info.duration).formatted(.time(pattern: .minuteSecond))
        return "\(info.rows.formatted()) rows · \(info.flagged.formatted()) flagged · \(dur) · \(size)"
    }
}
