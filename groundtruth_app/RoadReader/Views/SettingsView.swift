import SwiftUI

struct SettingsView: View {
    @ObservedObject var settings: AppSettings
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Device name prefix", text: $settings.deviceNamePrefix)
                        .autocorrectionDisabled()
                    TextField("Service UUID", text: $settings.serviceUUID)
                        .font(.footnote.monospaced())
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                } header: {
                    Text("Bluetooth")
                } footer: {
                    Text("The app auto-connects to a device whose name starts with the prefix, or that advertises this service. Default is the Nordic UART Service used by most ESP32 sketches.")
                }

                Section {
                    Toggle("Stream to computer", isOn: $settings.forwardingEnabled)
                    TextField("Host / IP", text: $settings.forwardHost)
                        .keyboardType(.numbersAndPunctuation)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                    TextField("Port", value: $settings.forwardPort, format: .number.grouping(.never))
                        .keyboardType(.numberPad)
                    Picker("Protocol", selection: $settings.forwardProtocol) {
                        ForEach(ForwardProtocol.allCases) { p in
                            Text(p.label).tag(p)
                        }
                    }
                    .pickerStyle(.segmented)
                } header: {
                    Text("Forwarding")
                } footer: {
                    Text("Run listener/listener.py on the computer. Phone and computer must be on the same Wi-Fi (or the phone's hotspot). UDP is lowest latency; TCP guarantees delivery.")
                }

                Section {
                    Picker("Vehicle", selection: $settings.vehicleType) {
                        ForEach(VehicleType.allCases) { v in
                            Text(v.label).tag(v)
                        }
                    }
                    .pickerStyle(.segmented)
                    Toggle("Live analysis while riding", isOn: $settings.liveAnalysisEnabled)
                    TextField("Analysis port", value: $settings.analysisPort, format: .number.grouping(.never))
                        .keyboardType(.numberPad)
                } header: {
                    Text("Road analysis")
                } footer: {
                    Text("Run `python3 listener.py --analyze` on the forwarding host: it turns the stream into road roughness and potholes, and this app polls it on the analysis port. Uploaded sessions are analysed by upload_server.py; tap a session to see its map.")
                }

                Section {
                    HStack {
                        Text("Minimum mark duration")
                        Spacer()
                        Text(String(format: "%.1f s", settings.minMarkDuration))
                            .foregroundStyle(.secondary)
                            .monospacedDigit()
                    }
                    Slider(value: $settings.minMarkDuration, in: 0.1...3.0, step: 0.1)
                } header: {
                    Text("Detect Bump")
                } footer: {
                    Text("Holding the button labels rows for as long as you hold. A quick tap labels rows for at least this long.")
                }

                Section {
                    Toggle("Live Broadcasting while recording", isOn: $settings.liveBroadcastEnabled)
                        .disabled(settings.dashboardEndpoint == nil)
                    TextField("Dashboard URL (http://192.168.1.100:8090)", text: $settings.dashboardURL)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                    if let url = settings.dashboardEndpoint {
                        Label("Rows go to \(url.absoluteString)/ingest", systemImage: "checkmark.circle.fill")
                            .font(.footnote)
                            .foregroundStyle(.green)
                    } else {
                        Label("Enter the dashboard server address (or set a forwarding host)", systemImage: "exclamationmark.triangle")
                            .font(.footnote)
                            .foregroundStyle(.orange)
                    }
                } header: {
                    Text("Live dashboard")
                } footer: {
                    Text("Run software/dashboard/server.py on a computer on the same Wi-Fi and open its page in a browser. Leave the URL blank to use the forwarding host on port 8090. While recording, readings are sent to the server about four times a second and every open browser draws them within a second; nothing is written to a database. Rows are queued and retried if the connection drops.")
                }

                Section {
                    TextField("Project URL (https://xxxx.supabase.co)", text: $settings.supabaseURL)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                    SecureField("anon public API key", text: $settings.supabaseKey)
                    TextField("Table", text: $settings.supabaseTable)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                    if settings.supabaseCredentials != nil {
                        Label("Ready: uploads go to \(settings.supabaseTable)", systemImage: "checkmark.circle.fill")
                            .font(.footnote)
                            .foregroundStyle(.green)
                    } else {
                        Label("Not configured: fill in Config/SupabaseConfig.swift or the fields above", systemImage: "exclamationmark.triangle")
                            .font(.footnote)
                            .foregroundStyle(.orange)
                    }
                } header: {
                    Text("Supabase")
                } footer: {
                    Text("Sessions > Upload inserts one row per reading: session_date, session_number, recorded_at, ax–gz, latitude, longitude, anomaly_detected. Create the table with supabase/schema.sql. The anon key is from Project Settings > API. Uploads happen only when you ask for them; the live stream never touches Supabase.")
                }

                Section {
                    TextField("Upload URL (https://…/upload)", text: $settings.uploadURL)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                    SecureField("API key (optional)", text: $settings.uploadAPIKey)
                    HStack {
                        Text("Device ID")
                        Spacer()
                        Text(settings.deviceID.prefix(8) + "…")
                            .font(.footnote.monospaced())
                            .foregroundStyle(.secondary)
                    }
                } header: {
                    Text("Upload server (optional)")
                } footer: {
                    Text("Leave blank to upload to Supabase only. Sessions are uploaded as a multipart POST with the CSV in a \"file\" field plus session_name, device_id, rows, flagged, duration_s and started_at. The API key is sent as a Bearer token. Test locally with listener/upload_server.py.")
                }

                Section("Dataset columns") {
                    Text(IMUSample.csvHeader.replacingOccurrences(of: ",", with: ", "))
                        .font(.footnote.monospaced())
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
