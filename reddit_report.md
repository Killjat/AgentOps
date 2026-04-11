# Mapping Proxy Infrastructure with Distributed Traceroute: What 258 Route Traces Reveal About VPN/Proxy Providers

**Posted to: r/netsec**

---

## TL;DR

I built a distributed network path intelligence platform with vantage points across the US, Hong Kong, mainland China (datacenter), and Android mobile devices on real Chinese carrier networks (China Mobile AS56046, China Telecom AS4134). After running 258 traceroute sessions against 109 proxy/VPN-related IPs sourced from FOFA, the routing data reveals a striking pattern: the majority of Chinese-market "airport" VPN providers share the same small pool of upstream ASNs, and their infrastructure is far more consolidated than their branding suggests.

Platform: **https://47.111.28.162:8443** (self-signed cert — click through the warning)

---

## Background

Most IP intelligence tools — Shodan, Censys, ipinfo — answer the question *"what is running on this IP?"* They don't answer *"where does this IP sit in the actual network topology, and who controls the upstream path to it?"*

Traceroute-based path analysis fills this gap. By running concurrent traceroutes from geographically diverse vantage points, you can reconstruct the routing infrastructure behind any IP — identifying upstream transit providers, detecting anycast/tunnel-based geolocation spoofing, and correlating seemingly unrelated IPs back to shared infrastructure.

The key differentiator of our approach: **Android mobile nodes on real carrier networks**. Most distributed measurement platforms use datacenter nodes exclusively. We include handsets on China Mobile (AS56046, Shanghai) and China Telecom (AS4134, Nanjing), which gives a ground-truth view of how traffic actually flows for end users — including GFW interference patterns that are invisible from outside China.

---

## Methodology

**Data collection:**
- Sourced 109 proxy/VPN IPs from FOFA using fingerprint queries targeting Shadowsocks endpoints, VMess servers, and exposed Clash dashboard panels
- Ran concurrent traceroutes from 5 vantage points:
  - US Linux (AS25820 region)
  - Hong Kong Linux (AS401701 region)
  - Alibaba Cloud Linux, mainland China
  - Android — China Mobile, Shanghai (AS56046)
  - Android — China Telecom, Nanjing (AS4134)
- All hop data stored in SQLite with geographic enrichment via ipinfo.io

**Dataset size:**
- 258 traceroute sessions
- 1,071 individual hop records
- 263 unique IP profiles
- 109 unique target IPs

**Threat cross-reference:** Each target IP was also queried against AbuseIPDB and VirusTotal for historical threat context.

---

## Key Findings

### Finding 1: AS7578 (Global Secure Layer) is the dominant upstream for proxy exit nodes

Out of 1,071 recorded hops, **AS7578 appeared 134 times in hops 8 and beyond** (near-target hops). This single ASN acts as the upstream transit for a disproportionate share of proxy exit nodes in our dataset.

Global Secure Layer operates a globally distributed anycast-capable network with PoPs in:

| GSL Node IP | Location | Hop Position |
|---|---|---|
| 206.148.24.92 | Hong Kong | hop 4–6 |
| 206.148.27.189 | Hong Kong | hop 5–7 |
| 206.148.25.78 | Chicago, US | hop 7 |
| 206.148.26.68 | Washington, US | hop 8–10 |
| 206.148.25.65 | Pittsburgh, US | hop 8 |
| 206.148.25.62 | Ashburn, US | hop 9 |
| 206.148.26.170 | Amsterdam, NL | hop 9–10 |
| 206.148.26.80 | Amsterdam, NL | hop 10–11 |
| 206.148.26.67 | Amsterdam, NL | hop 11 |
| 206.148.26.126 | Amsterdam, NL | hop 12 |
| 206.148.24.27 | Sydney, AU | hop 7–8 |
| 206.148.27.24 | Sydney, AU | hop 8–9 |

The same GSL infrastructure appears in the routing path of IPs from multiple ostensibly independent VPN providers. This strongly suggests that many "different" providers are actually purchasing IP capacity from the same upstream.

---

### Finding 2: Three ASNs account for the majority of datacenter proxy IPs

| ASN | Organization | Location | Seen Count |
|---|---|---|---|
| AS401701 | cognetcloud INC | Hong Kong, Tung Chung | 36 |
| AS25820 | IT7 Networks Inc | Netherlands, Amsterdam | 23 |
| AS400619 | AROSSCLOUD INC. | US, Los Angeles | 18 |

These are small ISPs specializing in selling IP blocks to proxy/VPN operators. Their repeated appearance across different "providers" confirms significant infrastructure consolidation.

---

### Finding 3: 7 genuine residential IPs, all Hong Kong Broadband Network (HKBN)

| IP | Location | ISP | ASN |
|---|---|---|---|
| 14.136.105.194 | Hong Kong | HKBN | AS9269 |
| 61.93.35.43 | Tin Shui Wai, HK | HKBN | AS9269 |
| 14.199.91.137 | Hong Kong | HKBN | AS9269 |
| 119.247.238.233 | Kwai Chung, HK | HKBN | AS9269 |
| 113.10.183.105 | Hong Kong | HKBN | AS9269 |
| 14.136.142.13 | Hong Kong | HKBN | AS9269 |
| 203.80.69.145 | Hong Kong | HKBN | AS9269 |

All 7 residential IPs belong to HKBN. Their presence in a proxy IP pool suggests either compromised home routers or a residential proxy network operating in HK. These IPs are significantly harder to detect as proxies because they carry genuine residential ASN attribution.

---

### Finding 4: Mobile carrier perspective reveals selective routing

The Android nodes on China Mobile and China Telecom showed different routing behavior compared to datacenter nodes for the same targets — consistent with partial GFW interference or carrier-level traffic shaping. This is only visible because we have genuine mobile carrier nodes.

---

## Infrastructure Consolidation Map

```
Chinese Users
     │
     ▼
[GFW / Carrier Network]
     │
     ▼
Exit Node IPs (cognetcloud / IT7 / AROSSCLOUD)
     │
     ▼
AS7578 Global Secure Layer (dominant transit)
     │
     ├── HK PoP (206.148.24.x, 206.148.27.x)
     ├── US PoPs (Chicago, Washington, Pittsburgh, Ashburn)
     ├── NL PoPs (Amsterdam)
     └── AU PoPs (Sydney)
```

Multiple "independent" VPN providers share this same upstream path.

---

## Platform

**CyberStroll** — https://47.111.28.162:8443

- `/probe` — Single IP: multi-node traceroute + AbuseIPDB/VirusTotal threat history
- `/batch-scan` — Bulk scanning with FOFA query import
- `/netcheck-ui` — Network quality from multiple vantage points

7 nodes: US Linux, HK Linux, Alibaba Cloud Linux, Alibaba Cloud Windows, 3× Android (China Mobile / China Telecom / HONOR).

---

## Limitations & Next Steps

- 109 IPs is sufficient for pattern identification, not statistically conclusive. Scaling to 1,000+ is next.
- Weekly re-scanning to detect infrastructure changes over time
- ASN cluster correlation to map which "different" providers share underlying infrastructure

Raw data available on request.

---

*Data collected April 10–11, 2026. All IPs sourced from public FOFA queries. Passive routing path observation only — no active exploitation.*
