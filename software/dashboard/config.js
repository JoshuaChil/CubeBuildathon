// GroundTruth dashboard configuration.
// The page is served by server.py, which also receives the phone's live stream (POST /ingest) and pushes it
// to the browser over Server-Sent Events (GET /stream). Leave serverUrl blank to use the server the page was
// loaded from; set it (e.g. "http://192.168.1.20:8090") only when the page is opened from the file system or
// hosted somewhere else. Anything saved in the dashboard's Settings dialog overrides these values in that
// browser (localStorage).
window.GROUNDTRUTH_CONFIG = {
  serverUrl: "",
  windowSeconds: 20,    // default rolling window shown on the charts
};
