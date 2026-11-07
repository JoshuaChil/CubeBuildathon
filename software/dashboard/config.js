// GroundTruth dashboard configuration.
// Same Supabase project and publishable key the iPhone app uses (groundtruth_app/RoadReader/Config/SupabaseConfig.swift).
// The publishable key is safe to ship in a browser; row-level security on the table decides what it can read.
// Anything saved in the dashboard's Settings dialog overrides these values in that browser (localStorage).
window.GROUNDTRUTH_CONFIG = {
  supabaseUrl: "https://vycrslqwsirgajgatatl.supabase.co",
  supabaseKey: "sb_publishable_fhuQUhehrMS1YeC3O95OJA_1ntsPe-e",
  table: "readings",
  pollMs: 500,          // how often to ask Supabase for new rows
  windowSeconds: 20,    // default rolling window shown on the charts
};
