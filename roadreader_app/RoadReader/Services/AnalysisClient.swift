import Foundation
import Combine

/// Polls the computer's live road analysis (listener.py --analyze) while a ride is recording.
final class AnalysisClient: ObservableObject {
    enum Connection: Equatable {
        case off
        case connecting
        case ok
        case unreachable(String)
    }

    @Published private(set) var latest: LiveAnalysis?
    @Published private(set) var connection: Connection = .off

    private var timer: Timer?
    private var url: URL?
    private var inFlight = false
    private let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 3
        return URLSession(configuration: config)
    }()

    /// Starts polling http://host:port/live every `interval` seconds.
    func start(host: String, port: Int, vehicle: VehicleType, interval: TimeInterval = 2.0) {
        stop()
        var comps = URLComponents()
        comps.scheme = "http"
        comps.host = host.trimmingCharacters(in: .whitespaces)
        comps.port = port
        comps.path = "/live"
        comps.queryItems = [URLQueryItem(name: "vehicle", value: vehicle.rawValue)]
        guard let url = comps.url else {
            connection = .unreachable("Bad host")
            return
        }
        self.url = url
        latest = nil
        connection = .connecting
        poll()
        let t = Timer(timeInterval: interval, repeats: true) { [weak self] _ in self?.poll() }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    /// Stops polling but keeps the last result on screen.
    func stop() {
        timer?.invalidate()
        timer = nil
        url = nil
        if connection != .off { connection = .off }
    }

    private func poll() {
        guard let url, !inFlight else { return }
        inFlight = true
        session.dataTask(with: url) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self else { return }
                self.inFlight = false
                guard self.url != nil else { return } // stopped meanwhile
                if let error {
                    self.connection = .unreachable(error.localizedDescription)
                    return
                }
                guard let data, (response as? HTTPURLResponse)?.statusCode == 200,
                      let live = try? RoadAnalysisJSON.decoder.decode(LiveAnalysis.self, from: data) else {
                    self.connection = .unreachable("Unexpected response")
                    return
                }
                self.latest = live
                self.connection = .ok
            }
        }.resume()
    }
}
