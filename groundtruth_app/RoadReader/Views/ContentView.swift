import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var session: SessionViewModel
    @State private var showSettings = false
    @State private var showSessions = false

    var body: some View {
        NavigationStack {
            Group {
                if session.bluetooth.state.isConnected || session.isDemo {
                    ReadingsView()
                } else {
                    ScanView()
                }
            }
            .navigationTitle("Road Reader")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { showSessions = true } label: {
                        Image(systemName: "doc.text")
                    }
                    .accessibilityLabel("Saved sessions")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: {
                        Image(systemName: "gearshape")
                    }
                    .accessibilityLabel("Settings")
                    .disabled(session.isReading)
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsView(settings: session.settings)
            }
            .sheet(isPresented: $showSessions) {
                SessionsView()
            }
            .alert("Error", isPresented: Binding(
                get: { session.errorMessage != nil },
                set: { if !$0 { session.errorMessage = nil } }
            )) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(session.errorMessage ?? "")
            }
        }
    }
}

#Preview {
    ContentView().environmentObject(SessionViewModel())
}
