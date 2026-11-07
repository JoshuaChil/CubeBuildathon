import Foundation
import Network
import Combine

/// Sends each labelled CSV row to a computer on the local network over UDP or TCP.
final class StreamForwarder: ObservableObject {
    enum Status: Equatable {
        case idle
        case connecting
        case ready
        case failed(String)

        var title: String {
            switch self {
            case .idle: return "Off"
            case .connecting: return "Connecting…"
            case .ready: return "Sending"
            case .failed(let msg): return "Error: \(msg)"
            }
        }
    }

    @Published private(set) var status: Status = .idle
    @Published private(set) var destination = ""

    private var connection: NWConnection?
    private let queue = DispatchQueue(label: "roadreader.forwarder")
    private var sent = 0
    private var dropped = 0
    private var isActive = false
    private var config: (host: String, port: UInt16, proto: ForwardProtocol)?

    func start(host: String, port: Int, proto: ForwardProtocol) {
        stop()
        guard let nwPort = NWEndpoint.Port(rawValue: UInt16(clamping: port)), !host.isEmpty else {
            status = .failed("Invalid host or port")
            return
        }
        isActive = true
        config = (host, nwPort.rawValue, proto)
        destination = "\(proto.label) \(host):\(port)"
        openConnection(host: host, port: nwPort, proto: proto)
    }

    func stop() {
        isActive = false
        connection?.cancel()
        connection = nil
        status = .idle
    }

    /// Fire-and-forget; safe to call at IMU rates (100+ Hz).
    func send(_ line: String) {
        guard let conn = connection, let data = (line + "\n").data(using: .utf8) else { return }
        conn.send(content: data, completion: .contentProcessed { [weak self] error in
            // Completion runs on `queue`.
            if error == nil { self?.sent += 1 } else { self?.dropped += 1 }
        })
    }

    /// Thread-safe snapshot for the UI.
    func counters() -> (sent: Int, dropped: Int) {
        queue.sync { (sent, dropped) }
    }

    private func openConnection(host: String, port: NWEndpoint.Port, proto: ForwardProtocol) {
        let params: NWParameters = proto == .udp ? .udp : .tcp
        let conn = NWConnection(host: NWEndpoint.Host(host), port: port, using: params)
        connection = conn
        status = .connecting

        conn.stateUpdateHandler = { [weak self] newState in
            DispatchQueue.main.async {
                guard let self, self.connection === conn else { return }
                switch newState {
                case .ready:
                    self.status = .ready
                case .waiting(let err):
                    self.status = .failed(err.localizedDescription)
                case .failed(let err):
                    self.status = .failed(err.localizedDescription)
                    self.scheduleRetry()
                case .cancelled:
                    if self.isActive { self.scheduleRetry() }
                default:
                    break
                }
            }
        }
        conn.start(queue: queue)
    }

    private func scheduleRetry() {
        guard isActive, let cfg = config else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
            guard let self, self.isActive, self.config?.host == cfg.host else { return }
            if let port = NWEndpoint.Port(rawValue: cfg.port) {
                self.openConnection(host: cfg.host, port: port, proto: cfg.proto)
            }
        }
    }
}
