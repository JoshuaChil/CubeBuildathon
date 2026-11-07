import Foundation

/// Supabase project credentials.
///
/// >>> PUT YOUR KEYS HERE <<<
/// Supabase dashboard → Project Settings → API:
///   - `url`     = "Project URL"            e.g. https://abcdefghijklmnop.supabase.co
///   - `anonKey` = the "publishable" key (sb_publishable_…) or legacy "anon public" key.
///                 NOT the secret/service_role key — that one must never ship in an app.
///   - `table`   = the table that receives one row per IMU reading (SQL in ../../supabase/schema.sql)
///
/// The anon key is designed to be embedded in client apps; row-level security on the table decides what it
/// may do. Values typed into the app under Settings > Supabase override these defaults on that phone.
enum SupabaseConfig {
    static let url = "https://vycrslqwsirgajgatatl.supabase.co"
    static let anonKey = "sb_publishable_fhuQUhehrMS1YeC3O95OJA_1ntsPe-e"
    static let table = "readings"

    /// True while the values above (or the ones passed in) are still the placeholders.
    static func isPlaceholder(url: String = url, anonKey: String = anonKey) -> Bool {
        url.contains("YOUR_PROJECT_REF") || anonKey.hasPrefix("YOUR_") || url.isEmpty || anonKey.isEmpty
    }
}
