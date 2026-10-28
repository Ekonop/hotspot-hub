use std::collections::HashMap;

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ModelVote {
    pub model_name: String,
    pub verdict: String,
    pub confidence: f32, // 0.0 to 1.0
    pub is_risk: bool,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct EnsembleReport {
    pub category: String,
    pub risk_level: String, // "safe", "low", "medium", "high", "critical"
    pub risk_score: u8,     // 0 to 100
    pub threat_tags: Vec<String>,
    pub team_verdicts: Vec<ModelVote>,
}

// ── Model 1: Threat Intelligence & C2 Signature Model ────────────────
struct ThreatIntelModel;
impl ThreatIntelModel {
    fn evaluate(domain: &str, _port: u16) -> ModelVote {
        let d = domain.to_lowercase();

        // Crypto mining
        if d.contains("nicehash")
            || d.contains("nanopool")
            || d.contains("ethermine")
            || d.contains("monero")
            || d.contains("cryptonight")
            || d.contains("supportxmr")
            || d.contains("pool.mine")
        {
            return ModelVote {
                model_name: "ThreatIntel".into(),
                verdict: "Crypto Mining Activity".into(),
                confidence: 0.98,
                is_risk: true,
            };
        }

        // Dynamic DNS / Evasion Tunnels
        if d.contains("duckdns.org")
            || d.contains("ngrok.io")
            || d.contains("localtunnel.me")
            || d.contains("pagekite.me")
            || d.contains("serveo.net")
        {
            return ModelVote {
                model_name: "ThreatIntel".into(),
                verdict: "Dynamic DNS / Tunneling Evasion".into(),
                confidence: 0.82,
                is_risk: true,
            };
        }

        // Suspicious Top Level Domains
        let suspicious_tlds = [".top", ".xyz", ".onion", ".click", ".win", ".bid", ".cc"];
        for tld in &suspicious_tlds {
            if d.ends_with(tld) || d.contains(&format!("{}:", tld)) {
                return ModelVote {
                    model_name: "ThreatIntel".into(),
                    verdict: format!("High-Risk TLD ({})", tld),
                    confidence: 0.65,
                    is_risk: true,
                };
            }
        }

        ModelVote {
            model_name: "ThreatIntel".into(),
            verdict: "Clean Reputation".into(),
            confidence: 0.90,
            is_risk: false,
        }
    }
}

// ── Model 2: Protocol & Port Behavioral Anomaly Model ────────────────
struct ProtocolAnomalyModel;
impl ProtocolAnomalyModel {
    fn evaluate(domain_or_ip: &str, port: u16) -> ModelVote {
        // Critical / Vulnerable Ports
        if matches!(port, 23 | 445 | 135 | 139 | 4444 | 6667 | 8333 | 9001 | 9050 | 9051 | 3389) {
            let desc = match port {
                23 => "Insecure Telnet Port 23",
                445 => "Exposed SMB Port 445 (WannaCry/EternalBlue risk)",
                4444 => "Metasploit / Reverse Shell Default Port 4444",
                9050 | 9051 => "Tor SOCKS Proxy Port",
                _ => "High-Risk Non-Standard Service Port",
            };
            return ModelVote {
                model_name: "ProtocolAnomaly".into(),
                verdict: desc.into(),
                confidence: 0.95,
                is_risk: true,
            };
        }

        // Direct IP without DNS resolution to non-web port
        let bare_ip = domain_or_ip.split(':').next().unwrap_or(domain_or_ip);
        if bare_ip.chars().all(|c| c.is_ascii_digit() || c == '.') && bare_ip.contains('.') {
            // Ignore local hotspot subnet traffic (e.g. 10.42.0.1 DNS/gateway services)
            if bare_ip.starts_with("10.42.0.") {
                return ModelVote {
                    model_name: "ProtocolAnomaly".into(),
                    verdict: "Internal Subnet Service".into(),
                    confidence: 0.98,
                    is_risk: false,
                };
            }
            if port != 80 && port != 443 && port != 8080 && port != 8443 && port != 53 && port != 5353 && port != 67 && port != 68 {
                return ModelVote {
                    model_name: "ProtocolAnomaly".into(),
                    verdict: format!("Unresolved Direct IP on Port {}", port),
                    confidence: 0.75,
                    is_risk: true,
                };
            }
        }

        ModelVote {
            model_name: "ProtocolAnomaly".into(),
            verdict: "Standard Port Compliance".into(),
            confidence: 0.92,
            is_risk: false,
        }
    }
}

// ── Model 3: Privacy & Telemetry Harvester Model ─────────────────────
struct PrivacyTrackerModel;
impl PrivacyTrackerModel {
    fn evaluate(domain: &str) -> ModelVote {
        let d = domain.to_lowercase();
        let tracker_signatures = [
            ("branch.io", "Deep Link / Ad Attribution Tracker"),
            ("adjust.com", "Mobile Attribution Tracker"),
            ("appsflyer", "Mobile Ad Campaign Tracker"),
            ("crashlytics", "Application Diagnostics / Crash Reports"),
            ("doubleclick", "Google Ad Network / Tracking"),
            ("app-analytics", "App Analytics Harvester"),
            ("telemetry", "Device Telemetry Collector"),
            ("analytics", "Analytics & Metrics Engine"),
            ("amplitude", "Product Behavioral Analytics"),
            ("sentry.io", "Error Telemetry Ingestion"),
            ("mixpanel", "Event Tracking Collector"),
            ("facebook.net", "Meta Pixel / Tracking Network"),
            ("scorecardresearch", "Web Audience Profiler"),
            ("criteo", "Retargeting Ad Beacon"),
        ];

        for (sig, label) in &tracker_signatures {
            if d.contains(sig) {
                return ModelVote {
                    model_name: "PrivacyTracker".into(),
                    verdict: (*label).into(),
                    confidence: 0.94,
                    is_risk: true,
                };
            }
        }

        ModelVote {
            model_name: "PrivacyTracker".into(),
            verdict: "No Tracking Signatures".into(),
            confidence: 0.88,
            is_risk: false,
        }
    }
}

// ── Model 4: Content & Intent Category Model ─────────────────────────
struct CategoryModel;
impl CategoryModel {
    fn evaluate(domain: &str) -> (String, f32) {
        let d = domain.to_lowercase();

        if d.contains("spotify")
            || d.contains("netflix")
            || d.contains("youtube")
            || d.contains("googlevideo")
            || d.contains("itunes")
            || d.contains("apple-cloudkit")
            || d.contains("primevideo")
            || d.contains("twitch")
            || d.contains("soundcloud")
            || d.contains("disney")
            || d.contains("audio-ak")
        {
            return ("Streaming & Media".into(), 0.95);
        }

        if d.contains("whatsapp")
            || d.contains("telegram")
            || d.contains("instagram")
            || d.contains("discord")
            || d.contains("tiktok")
            || d.contains("twitter")
            || d.contains("x.com")
            || d.contains("reddit")
            || d.contains("slack")
        {
            return ("Social & Chat".into(), 0.92);
        }

        if d.contains("steam")
            || d.contains("epicgames")
            || d.contains("riotgames")
            || d.contains("blizzard")
            || d.contains("playstation")
            || d.contains("xbox")
        {
            return ("Gaming & Entertainment".into(), 0.90);
        }

        if d.contains("cloudflare")
            || d.contains("fastly")
            || d.contains("akamai")
            || d.contains("amazonaws")
            || d.contains("gvt1.com")
            || d.contains("azure")
            || d.contains("cloudfront")
            || d.contains("1e100.net")
        {
            return ("Cloud & CDN".into(), 0.91);
        }

        if d.contains("google")
            || d.contains("apple")
            || d.contains("microsoft")
            || d.contains("archlinux")
            || d.contains("github")
        {
            return ("Web & Updates".into(), 0.85);
        }

        ("General Web".into(), 0.70)
    }
}

// ── Model 5: Entropy & DGA (Domain Generation Algorithm) Model ───────
struct EntropyDgaModel;
impl EntropyDgaModel {
    fn evaluate(domain: &str) -> ModelVote {
        let label = domain.split('.').next().unwrap_or(domain);
        if label.len() >= 12 {
            let entropy = Self::shannon_entropy(label);
            if entropy > 3.85 {
                return ModelVote {
                    model_name: "EntropyDGA".into(),
                    verdict: format!("High Entropy ({:.2}) DGA Suspected", entropy),
                    confidence: 0.80,
                    is_risk: true,
                };
            }
        }

        ModelVote {
            model_name: "EntropyDGA".into(),
            verdict: "Natural Domain Distribution".into(),
            confidence: 0.90,
            is_risk: false,
        }
    }

    fn shannon_entropy(s: &str) -> f64 {
        let mut map = HashMap::new();
        for c in s.chars() {
            *map.entry(c).or_insert(0) += 1;
        }
        let total = s.len() as f64;
        if total <= 1.0 {
            return 0.0;
        }
        let mut entropy = 0.0;
        for &count in map.values() {
            let p = count as f64 / total;
            entropy -= p * p.log2();
        }
        entropy
    }
}

// ── Ensemble Coordinator: Team of Classifiers ───────────────────────
pub struct EnsembleCoordinator;

impl EnsembleCoordinator {
    pub fn classify(domain: &str, port: u16, _proto: &str) -> EnsembleReport {
        let vote_threat = ThreatIntelModel::evaluate(domain, port);
        let vote_anomaly = ProtocolAnomalyModel::evaluate(domain, port);
        let vote_privacy = PrivacyTrackerModel::evaluate(domain);
        let vote_entropy = EntropyDgaModel::evaluate(domain);
        let (mut category, _) = CategoryModel::evaluate(domain);

        let mut tags = Vec::new();
        let mut risk_score = 0u8;

        if vote_threat.is_risk {
            tags.push(vote_threat.verdict.clone());
            risk_score = risk_score.saturating_add(60);
        }
        if vote_anomaly.is_risk {
            tags.push(vote_anomaly.verdict.clone());
            risk_score = risk_score.saturating_add(35);
        }
        if vote_entropy.is_risk {
            tags.push(vote_entropy.verdict.clone());
            risk_score = risk_score.saturating_add(30);
        }
        if vote_privacy.is_risk {
            tags.push(vote_privacy.verdict.clone());
            risk_score = risk_score.saturating_add(15);
            if category == "General Web" {
                category = "Telemetry & Ads".into();
            }
        }

        let risk_level = if risk_score >= 60 {
            "danger"
        } else if risk_score >= 25 {
            "warning"
        } else if risk_score > 0 {
            "low"
        } else {
            "safe"
        };

        let verdicts = vec![vote_threat, vote_anomaly, vote_privacy, vote_entropy];

        EnsembleReport {
            category,
            risk_level: risk_level.into(),
            risk_score,
            threat_tags: tags,
            team_verdicts: verdicts,
        }
    }
}
