use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::net::Ipv4Addr;

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ClientDevice {
    pub ip: Ipv4Addr,
    pub mac: String,
    pub hostname: String,
}

#[allow(dead_code)]
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct NatFlow {
    pub client_ip: Ipv4Addr,
    pub client_mac: String,
    pub client_name: String,
    pub client_port: u16,
    pub dst_ip: Ipv4Addr,
    pub dst_port: u16,
    pub protocol: String,
    pub bytes: u64,
}

pub struct AttributionEngine {
    pub clients_by_ip: HashMap<Ipv4Addr, ClientDevice>,
}

impl AttributionEngine {
    pub fn new() -> Self {
        let mut engine = Self {
            clients_by_ip: HashMap::new(),
        };
        engine.refresh_leases();
        engine
    }

    /// Read active DHCP leases from NetworkManager / dnsmasq
    pub fn refresh_leases(&mut self) {
        let lease_paths = [
            "/var/lib/NetworkManager/dnsmasq-ap0.leases",
            "/var/lib/misc/dnsmasq.leases",
        ];

        for path in &lease_paths {
            if let Ok(file) = File::open(path) {
                let reader = BufReader::new(file);
                for line in reader.lines().flatten() {
                    let parts: Vec<&str> = line.split_whitespace().collect();
                    if parts.len() >= 4 {
                        let mac = parts[1].to_lowercase();
                        if let Ok(ip) = parts[2].parse::<Ipv4Addr>() {
                            let hostname = if parts[3] != "*" && !parts[3].is_empty() {
                                parts[3].to_string()
                            } else {
                                "Client Device".into()
                            };
                            self.clients_by_ip.insert(ip, ClientDevice { ip, mac, hostname });
                        }
                    }
                }
            }
        }
    }

    /// Look up client device by IP address
    pub fn lookup(&self, ip: &Ipv4Addr) -> (String, String) {
        match self.clients_by_ip.get(ip) {
            Some(dev) => (dev.mac.clone(), dev.hostname.clone()),
            None => ("Unknown MAC".into(), "Hotspot Client".into()),
        }
    }

    /// Parse /proc/net/nf_conntrack to attribute NAT flows to hotspot clients
    #[allow(dead_code)]
    pub fn parse_conntrack(&self) -> Vec<NatFlow> {
        let mut flows = Vec::new();
        let file = match File::open("/proc/net/nf_conntrack") {
            Ok(f) => f,
            Err(_) => return flows,
        };

        let reader = BufReader::new(file);
        for line in reader.lines().flatten() {
            // Hotspot subnet filter: 10.42.0.0/24
            if !line.contains("src=10.42.0.") {
                continue;
            }

            let mut client_ip = None;
            let mut client_port = None;
            let mut dst_ip = None;
            let mut dst_port = None;
            let mut protocol = "TCP".to_string();
            let mut bytes = 0u64;

            if line.contains("udp") {
                protocol = "UDP".to_string();
            }

            for token in line.split_whitespace() {
                if token.starts_with("src=") && client_ip.is_none() {
                    client_ip = token.strip_prefix("src=").and_then(|s| s.parse::<Ipv4Addr>().ok());
                } else if token.starts_with("sport=") && client_port.is_none() {
                    client_port = token.strip_prefix("sport=").and_then(|s| s.parse::<u16>().ok());
                } else if token.starts_with("dst=") && dst_ip.is_none() {
                    dst_ip = token.strip_prefix("dst=").and_then(|s| s.parse::<Ipv4Addr>().ok());
                } else if token.starts_with("dport=") && dst_port.is_none() {
                    dst_port = token.strip_prefix("dport=").and_then(|s| s.parse::<u16>().ok());
                } else if token.starts_with("bytes=") {
                    bytes += token.strip_prefix("bytes=").and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
                }
            }

            if let (Some(c_ip), Some(c_port), Some(d_ip), Some(d_port)) =
                (client_ip, client_port, dst_ip, dst_port)
            {
                let (mac, name) = self.lookup(&c_ip);

                flows.push(NatFlow {
                    client_ip: c_ip,
                    client_mac: mac,
                    client_name: name,
                    client_port: c_port,
                    dst_ip: d_ip,
                    dst_port: d_port,
                    protocol,
                    bytes,
                });
            }
        }
        flows
    }
}
