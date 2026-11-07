import SwiftUI
import UIKit

/// Shown on launch until a Bluetooth device is connected.
struct ScanView: View {
    @EnvironmentObject private var session: SessionViewModel

    private var bluetooth: BluetoothManager { session.bluetooth }

    var body: some View {
        VStack(spacing: 20) {
            header
                .padding(.top, 24)

            if bluetooth.devices.isEmpty {
                Spacer()
                emptyState
                Spacer()
            } else {
                List {
                    Section("Nearby devices") {
                        ForEach(bluetooth.devices) { device in
                            Button { bluetooth.connect(to: device) } label: {
                                deviceRow(device)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                .listStyle(.insetGrouped)
            }

            footer
        }
        .padding(.bottom)
    }

    private var header: some View {
        VStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(iconColor.opacity(0.15))
                    .frame(width: 96, height: 96)
                Image(systemName: iconName)
                    .font(.system(size: 40))
                    .foregroundStyle(iconColor)
            }
            HStack(spacing: 8) {
                if isBusy { ProgressView() }
                Text(bluetooth.state.title)
                    .font(.headline)
            }
            if let err = bluetooth.lastError {
                Text(err).font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal)
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            switch bluetooth.state {
            case .poweredOff:
                Text("Turn on Bluetooth to find the Road Reader sensor.")
            case .unauthorized:
                Text("Road Reader needs Bluetooth permission.")
                Button("Open Settings") {
                    if let url = URL(string: UIApplication.openSettingsURLString) {
                        UIApplication.shared.open(url)
                    }
                }
            case .unsupported:
                Text("This device does not support Bluetooth LE.")
            default:
                Text("Power on the ESP32. The app connects automatically to a device named “\(session.settings.deviceNamePrefix)…” or one advertising the UART service.")
                    .multilineTextAlignment(.center)
            }
        }
        .font(.subheadline)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 32)
    }

    private var footer: some View {
        Button {
            bluetooth.startScanning()
        } label: {
            Label("Scan Again", systemImage: "arrow.clockwise")
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.bordered)
        .padding(.horizontal)
        .disabled(!canScan)
    }

    private func deviceRow(_ device: DiscoveredDevice) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(device.name).font(.body.weight(.medium))
                Text(device.advertisesService ? "Advertises data service" : "Tap to connect")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Text("\(device.rssi) dBm")
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
            if case .connecting = bluetooth.state, bluetooth.state.deviceName == device.name {
                ProgressView()
            }
        }
        .contentShape(Rectangle())
    }

    private var isBusy: Bool {
        switch bluetooth.state {
        case .scanning, .connecting, .unknown: return true
        default: return false
        }
    }

    private var canScan: Bool {
        switch bluetooth.state {
        case .poweredOff, .unauthorized, .unsupported, .unknown: return false
        default: return true
        }
    }

    private var iconName: String {
        switch bluetooth.state {
        case .poweredOff, .unauthorized, .unsupported: return "antenna.radiowaves.left.and.right.slash"
        default: return "antenna.radiowaves.left.and.right"
        }
    }

    private var iconColor: Color {
        switch bluetooth.state {
        case .poweredOff, .unauthorized, .unsupported: return .red
        case .connecting: return .orange
        default: return .accentColor
        }
    }
}
