use rusqlite::{params, Connection, Result};
use std::sync::{Arc, Mutex};

pub struct RollingDb {
    conn: Arc<Mutex<Connection>>,
}

impl RollingDb {
    pub fn new(path: &str) -> Result<Self> {
        let conn = Connection::open(path)?;
        conn.execute(
            "CREATE TABLE IF NOT EXISTS traffic_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                client_mac TEXT NOT NULL,
                client_ip TEXT NOT NULL,
                client_name TEXT NOT NULL,
                process TEXT NOT NULL,
                domain TEXT NOT NULL,
                protocol TEXT NOT NULL,
                bandwidth_bps INTEGER NOT NULL,
                category TEXT NOT NULL DEFAULT 'General Web',
                risk_level TEXT NOT NULL DEFAULT 'safe',
                risk_score INTEGER NOT NULL DEFAULT 0,
                threat_tags TEXT
            )",
            [],
        )?;

        // Ensure new columns exist if upgraded
        let _ = conn.execute("ALTER TABLE traffic_history ADD COLUMN category TEXT NOT NULL DEFAULT 'General Web'", []);
        let _ = conn.execute("ALTER TABLE traffic_history ADD COLUMN risk_level TEXT NOT NULL DEFAULT 'safe'", []);
        let _ = conn.execute("ALTER TABLE traffic_history ADD COLUMN risk_score INTEGER NOT NULL DEFAULT 0", []);
        let _ = conn.execute("ALTER TABLE traffic_history ADD COLUMN threat_tags TEXT", []);

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_traffic_timestamp ON traffic_history(timestamp)",
            [],
        )?;

        conn.execute(
            "CREATE TABLE IF NOT EXISTS adblock_stats (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                ads_restricted INTEGER NOT NULL DEFAULT 0,
                data_saved_bytes INTEGER NOT NULL DEFAULT 0
            )",
            [],
        )?;
        let _ = conn.execute(
            "INSERT OR IGNORE INTO adblock_stats (id, ads_restricted, data_saved_bytes) VALUES (1, 0, 0)",
            [],
        );

        Ok(Self {
            conn: Arc::new(Mutex::new(conn)),
        })
    }

    pub fn insert(
        &self,
        timestamp: &str,
        client_mac: &str,
        client_ip: &str,
        client_name: &str,
        process: &str,
        domain: &str,
        protocol: &str,
        bandwidth_bps: u64,
        category: &str,
        risk_level: &str,
        risk_score: u8,
        threat_tags: &str,
    ) {
        if let Ok(conn) = self.conn.lock() {
            let _ = conn.execute(
                "INSERT INTO traffic_history (timestamp, client_mac, client_ip, client_name, process, domain, protocol, bandwidth_bps, category, risk_level, risk_score, threat_tags)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
                params![
                    timestamp,
                    client_mac,
                    client_ip,
                    client_name,
                    process,
                    domain,
                    protocol,
                    bandwidth_bps as i64,
                    category,
                    risk_level,
                    risk_score as i32,
                    threat_tags,
                ],
            );
        }
    }

    /// Prune entries older than 1 hour to prevent disk bloat
    pub fn prune_older_than_1h(&self) {
        if let Ok(conn) = self.conn.lock() {
            let _ = conn.execute(
                "DELETE FROM traffic_history WHERE datetime(timestamp) < datetime('now', '-1 hour')",
                [],
            );
        }
    }

    /// Record an ad/tracker restriction and calculate prevented bandwidth
    pub fn record_ad_restriction(&self, is_video_ad: bool) -> (u64, u64) {
        let added_bytes = if is_video_ad { 1_500_000 } else { 150_000 };
        if let Ok(conn) = self.conn.lock() {
            let _ = conn.execute(
                "UPDATE adblock_stats SET ads_restricted = ads_restricted + 1, data_saved_bytes = data_saved_bytes + ?1 WHERE id = 1",
                params![added_bytes],
            );
            if let Ok(mut stmt) = conn.prepare("SELECT ads_restricted, data_saved_bytes FROM adblock_stats WHERE id = 1") {
                if let Ok(row) = stmt.query_row([], |r| Ok((r.get::<_, i64>(0).unwrap_or(0) as u64, r.get::<_, i64>(1).unwrap_or(0) as u64))) {
                    return row;
                }
            }
        }
        (0, 0)
    }

    /// Retrieve current adblock restrictions count and total data saved in bytes
    pub fn get_adblock_stats(&self) -> (u64, u64) {
        if let Ok(conn) = self.conn.lock() {
            if let Ok(mut stmt) = conn.prepare("SELECT ads_restricted, data_saved_bytes FROM adblock_stats WHERE id = 1") {
                if let Ok(row) = stmt.query_row([], |r| Ok((r.get::<_, i64>(0).unwrap_or(0) as u64, r.get::<_, i64>(1).unwrap_or(0) as u64))) {
                    return row;
                }
            }
        }
        (0, 0)
    }
}
