mod classifier;
mod conntrack;
mod db;

use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        State,
    },
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use classifier::{EnsembleCoordinator, ModelVote};
use conntrack::AttributionEngine;
use db::RollingDb;
use std::{net::Ipv4Addr, net::SocketAddr, sync::Arc, time::Duration};
use tokio::sync::{broadcast, Mutex};
use tower_http::cors::CorsLayer;

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct TrafficRecord {
    pub timestamp: String,
    pub client_mac: String,
    pub client_ip: String,
    pub client_name: String,
    pub process: String,
    pub domain: String,
    pub protocol: String,
    pub bandwidth_bps: u64, // Instantaneous throughput speed (bits per second)
    pub total_bytes: u64,   // Total volume transferred on connection (bytes)
    pub category: String,
    pub risk_level: String, // "safe", "low", "warning", "danger"
    pub risk_score: u8,
    pub threat_tags: Vec<String>,
    pub team_verdicts: Vec<ModelVote>,
}

#[derive(Debug, serde::Deserialize)]
pub struct BlockRequest {
    pub mac: String,
}

struct AppState {
    tx: broadcast::Sender<String>,
    _engine: Arc<Mutex<AttributionEngine>>,
    _db: Arc<RollingDb>,
}

#[tokio::main]
async fn main() {
    let (tx, _rx) = broadcast::channel::<String>(2000);
    let engine = Arc::new(Mutex::new(AttributionEngine::new()));
    let db = Arc::new(
        RollingDb::new("/tmp/hotspot_traffic.db").expect("Failed to initialize SQLite rolling database"),
    );

    let state = Arc::new(AppState {
        tx: tx.clone(),
        _engine: engine.clone(),
        _db: db.clone(),
    });

    // 1. Spawning RustNet supervisor in background
    let tx_clone = tx.clone();
    let engine_clone = engine.clone();
    let db_clone = db.clone();
    tokio::spawn(async move {
        run_rustnet_supervisor(tx_clone, engine_clone, db_clone).await;
    });

    // 2. Background database pruning task (every 5 minutes)
    let db_prune = db.clone();
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_secs(300)).await;
            db_prune.prune_older_than_1h();
        }
    });

    // 3. API & WebSocket Router
    let app = Router::new()
        .route("/", get(|| async { axum::response::Html(include_str!("../../frontend/index.html")) }))
        .route("/ws", get(ws_handler))
        .route("/api/export/pcapng", post(export_pcapng))
        .route("/api/clients/block", post(block_client))
        .route("/api/health", get(|| async { Json(serde_json::json!({"status": "ok", "service": "rustnet-hub"})) }))
        .layer(CorsLayer::permissive())
        .with_state(state);

    let addr = SocketAddr::from(([0, 0, 0, 0], 9090));
    println!("🚀 RustNet Hub Bridge running at http://{}", addr);
    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

async fn ws_handler(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    ws.on_upgrade(|socket| handle_socket(socket, state))
}

async fn handle_socket(mut socket: WebSocket, state: Arc<AppState>) {
    let mut rx = state.tx.subscribe();
    while let Ok(msg) = rx.recv().await {
        if socket.send(Message::Text(msg.into())).await.is_err() {
            break;
        }
    }
}

/// Spawns and supervises `sudo -n rustnet --headless --output jsonl -i ap0 --no-localhost -r 500`
async fn run_rustnet_supervisor(
    tx: broadcast::Sender<String>,
    engine: Arc<Mutex<AttributionEngine>>,
    db: Arc<RollingDb>,
) {
    loop {
        println!("📡 Launching supervised RustNet daemon on interface ap0...");
        let child = tokio::process::Command::new("sudo")
            .args([
                "-n",
                "rustnet",
                "--headless",
                "--output",
                "jsonl",
                "-i",
                "ap0",
                "--no-localhost",
                "-r",
                "500",
            ])
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .spawn();

        match child {
            Ok(mut proc) => {
                use tokio::io::{AsyncBufReadExt, BufReader};
                let mut conn_rate_map: std::collections::HashMap<String, (u64, std::time::Instant)> = std::collections::HashMap::new();

                if let Some(stdout) = proc.stdout.take() {
                    let mut reader = BufReader::new(stdout).lines();
                    while let Ok(Some(line)) = reader.next_line().await {
                        // Parse JSON snapshot from RustNet
                        if let Ok(val) = serde_json::from_str::<serde_json::Value>(&line) {
                            if let Some(conns) = val.get("connections").and_then(|c| c.as_array()) {
                                let mut eng = engine.lock().await;
                                eng.refresh_leases();

                                for conn in conns {
                                    let conn_id = conn.get("id").and_then(|i| i.as_str()).unwrap_or("").to_string();
                                    let protocol = conn.get("protocol").and_then(|p| p.as_str()).unwrap_or("TCP");
                                    let local_ip_str = conn.get("local").and_then(|l| l.get("ip")).and_then(|i| i.as_str()).unwrap_or("");
                                    let remote_ip_str = conn.get("remote").and_then(|r| r.get("ip")).and_then(|i| i.as_str()).unwrap_or("");
                                    let local_port = conn.get("local").and_then(|l| l.get("port")).and_then(|p| p.as_u64()).unwrap_or(0);
                                    let remote_port = conn.get("remote").and_then(|r| r.get("port")).and_then(|p| p.as_u64()).unwrap_or(0);
                                    let service = conn.get("service").and_then(|s| s.as_str()).unwrap_or("");
                                    let app = conn.get("application").and_then(|a| a.as_str()).unwrap_or("");

                                    // Identify if local or remote is the hotspot client (10.42.0.x)
                                    let (client_ip_str, dst_str, dst_port) = if local_ip_str.starts_with("10.42.0.") {
                                        (local_ip_str, format!("{}:{}", remote_ip_str, remote_port), remote_port)
                                    } else if remote_ip_str.starts_with("10.42.0.") {
                                        (remote_ip_str, format!("{}:{}", local_ip_str, local_port), local_port)
                                    } else {
                                        continue;
                                    };

                                    let (mac, name) = if let Ok(ip) = client_ip_str.parse::<Ipv4Addr>() {
                                        eng.lookup(&ip)
                                    } else {
                                        ("Unknown MAC".into(), "Client".into())
                                    };

                                    let bytes_sent = conn.get("traffic").and_then(|t| t.get("bytes_sent")).and_then(|b| b.as_u64()).unwrap_or(0);
                                    let bytes_recv = conn.get("traffic").and_then(|t| t.get("bytes_received")).and_then(|b| b.as_u64()).unwrap_or(0);
                                    let total_bytes = bytes_sent + bytes_recv;

                                    let out_rate = conn.get("traffic").and_then(|t| t.get("outgoing_bytes_per_second")).and_then(|r| r.as_f64()).unwrap_or(0.0);
                                    let in_rate = conn.get("traffic").and_then(|t| t.get("incoming_bytes_per_second")).and_then(|r| r.as_f64()).unwrap_or(0.0);

                                    let now_inst = std::time::Instant::now();
                                    let rate_bytes_sec = if (out_rate + in_rate) > 0.0 {
                                        out_rate + in_rate
                                    } else if let Some((prev_b, prev_t)) = conn_rate_map.get(&conn_id) {
                                        let dt = now_inst.duration_since(*prev_t).as_secs_f64();
                                        if dt > 0.0 && total_bytes >= *prev_b {
                                            (total_bytes - *prev_b) as f64 / dt
                                        } else {
                                            0.0
                                        }
                                    } else {
                                        total_bytes as f64 / 0.5
                                    };

                                    if !conn_id.is_empty() {
                                        conn_rate_map.insert(conn_id, (total_bytes, now_inst));
                                    }

                                    let process_name = if let Some(proc) = conn.get("process").and_then(|p| p.get("name")).and_then(|n| n.as_str()) {
                                        proc.to_string()
                                    } else if !app.is_empty() {
                                        app.to_string()
                                    } else if !service.is_empty() {
                                        service.to_string()
                                    } else {
                                        "NAT Forward".to_string()
                                    };

                                    let domain = if let Some(h) = conn.get("hostname").and_then(|h| h.as_str()) {
                                        h.to_string()
                                    } else {
                                        dst_str
                                    };

                                    // Run the Team of Classifiers on this flow!
                                    let ensemble = EnsembleCoordinator::classify(&domain, dst_port as u16, protocol);

                                    let now_str = chrono::Utc::now().to_rfc3339();
                                    let record = TrafficRecord {
                                        timestamp: now_str.clone(),
                                        client_mac: mac,
                                        client_ip: client_ip_str.to_string(),
                                        client_name: name,
                                        process: process_name,
                                        domain,
                                        protocol: protocol.to_string(),
                                        bandwidth_bps: (rate_bytes_sec * 8.0) as u64,
                                        total_bytes,
                                        category: ensemble.category,
                                        risk_level: ensemble.risk_level,
                                        risk_score: ensemble.risk_score,
                                        threat_tags: ensemble.threat_tags.clone(),
                                        team_verdicts: ensemble.team_verdicts,
                                    };

                                    let tags_joined = ensemble.threat_tags.join(", ");
                                    db.insert(
                                        &record.timestamp,
                                        &record.client_mac,
                                        &record.client_ip,
                                        &record.client_name,
                                        &record.process,
                                        &record.domain,
                                        &record.protocol,
                                        record.bandwidth_bps,
                                        &record.category,
                                        &record.risk_level,
                                        record.risk_score,
                                        &tags_joined,
                                    );

                                    if let Ok(json_str) = serde_json::to_string(&record) {
                                        let _ = tx.send(json_str);
                                    }
                                }
                            }
                        }
                    }
                }
                let _ = proc.wait().await;
            }
            Err(e) => {
                eprintln!("⚠️ Error executing rustnet via sudo: {}. Retrying in 3s...", e);
                tokio::time::sleep(Duration::from_secs(3)).await;
            }
        }
    }
}

/// Trigger an annotated PCAPNG capture via RustNet
async fn export_pcapng() -> Response {
    let filename = format!("/tmp/hotspot-capture-{}.pcapng", chrono::Utc::now().timestamp());
    let _ = tokio::process::Command::new("sudo")
        .args([
            "-n",
            "rustnet",
            "--pcapng-export",
            &filename,
            "--duration",
            "5",
            "-i",
            "ap0",
        ])
        .output()
        .await;

    Json(serde_json::json!({
        "status": "ready",
        "file": filename,
        "message": "Wireshark PCAPNG capture generated with metadata"
    })).into_response()
}

/// Instant MAC address blocking endpoint
async fn block_client(Json(req): Json<BlockRequest>) -> Response {
    let mac = req.mac.trim().to_lowercase();
    if mac.len() >= 17 {
        // 1. Kick client immediately via iw
        let _ = tokio::process::Command::new("sudo")
            .args(["-n", "iw", "dev", "ap0", "station", "del", &mac])
            .output()
            .await;

        // 2. Append to blocked_macs.txt in user config
        if let Ok(home) = std::env::var("HOME") {
            let path = format!("{}/.config/hotspot-hub/blocked_macs.txt", home);
            use std::io::Write;
            if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
                let _ = writeln!(f, "{}", mac);
            }
        }

        Json(serde_json::json!({"status": "ok", "message": format!("Blocked and kicked {}", mac)})).into_response()
    } else {
        Json(serde_json::json!({"status": "error", "message": "Invalid MAC address"})).into_response()
    }
}
