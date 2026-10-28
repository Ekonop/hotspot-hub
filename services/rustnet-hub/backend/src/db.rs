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
                bandwidth_bps INTEGER NOT NULL
            )",
            [],
        )?;

        // Index for fast lookups
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_traffic_timestamp ON traffic_history(timestamp)",
            [],
        )?;

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
    ) {
        if let Ok(conn) = self.conn.lock() {
            let _ = conn.execute(
                "INSERT INTO traffic_history (timestamp, client_mac, client_ip, client_name, process, domain, protocol, bandwidth_bps)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                params![timestamp, client_mac, client_ip, client_name, process, domain, protocol, bandwidth_bps as i64],
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
}
