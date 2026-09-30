# hopwatch

Graphical mtr over time. A small Python collector runs `mtr` on a schedule, locally and/or from
remote [Globalping](https://globalping.io) probes, and stores per-hop loss and latency in
VictoriaMetrics. Grafana shows it next to HTTP/S checks from blackbox_exporter and up/down status
from Uptime Kuma.

```
 collector (mtr, Globalping) ──push──▶ VictoriaMetrics ◀──scrape── blackbox_exporter
                                            ▲       ▲
                                  Grafana ──┘       └──scrape── Uptime Kuma /metrics
 SmokePing (optional, own UI)
```

| Service         | URL                    | Purpose                                                |
| --------------- | ---------------------- | ------------------------------------------------------ |
| Grafana         | http://localhost:3000  | Dashboards (admin / `GRAFANA_ADMIN_PASSWORD`)          |
| Uptime Kuma     | http://localhost:3001  | Up/down checks, alerting, status pages                 |
| VictoriaMetrics | http://localhost:8428  | Time-series store; `/vmui` for ad-hoc queries          |
| SmokePing       | http://localhost:8080  | Optional reference graphs (`--profile smokeping`)      |

## Quick start

```sh
cp .env.example .env            # set a Grafana password
docker compose up -d --build
# optional:
docker compose --profile smokeping up -d
```

Open Grafana. The **hopwatch** dashboard is the home page. The first mtr results arrive within about a minute.

### Important: where the collector runs

Local traces are only meaningful when the collector runs on a **Linux host** (a Raspberry Pi,
VPS or home server) with `network_mode: host` uncommented in `docker-compose.yml`.

On **Docker Desktop (Windows/macOS)**, traffic goes through Docker's userspace network proxy,
which drops the intermediate TTL-expired replies. mtr then shows only 2 hops (Docker's bridge
and the destination). Destination latency and loss are still roughly right, but the path is not.
Globalping traces are unaffected.

## Configuration

- **`config/hopwatch.yml`**: mtr targets, local schedule, Globalping locations. Restart the
  collector after editing: `docker compose restart collector`.
- **`victoriametrics/scrape.yml`**: blackbox HTTP/S and ICMP targets, plus the Uptime Kuma scrape.
- **`smokeping/Targets`**: SmokePing targets.

Targets are currently listed separately in each of these files.

### Uptime Kuma metrics

1. In Kuma: Settings → API Keys → create a key.
2. Save only the key in `secrets/kuma_api_key` (the file is gitignored).
3. `docker compose restart victoriametrics`

### Globalping budget

Every probe result is one test. Anonymous users get 250 tests/hour, and a free account
(`GLOBALPING_TOKEN`) gets more. Usage = locations × targets × (3600 / interval), and the
collector logs its estimate at startup.

## Metrics

All carry `source` (where the trace ran from) and `target` (the `name` from config).

| Metric                                                     | Extra labels            | Meaning                                          |
| ---------------------------------------------------------- | ----------------------- | ------------------------------------------------ |
| `mtr_run_success`                                          |                         | 1 = mtr completed, 0 = failed                    |
| `mtr_run_duration_seconds`                                 |                         | How long the run took                            |
| `mtr_path_hops`                                            |                         | Number of hops                                   |
| `mtr_path_hash`                                            |                         | CRC32 of the hop IP sequence; changes = reroute  |
| `mtr_hop_loss_percent`, `mtr_hop_sent`                     | `hop`, `hop_ip`, `asn`  | Per-hop loss, probes sent                        |
| `mtr_hop_rtt_{avg,best,worst,stdev}_ms`                    | `hop`, `hop_ip`, `asn`  | Per-hop RTT (absent for hops that never replied) |
| `mtr_dest_loss_percent`, `mtr_dest_rtt_{avg,best,worst,stdev}_ms` |                  | Same, for the last hop only                      |
| `mtr_probe_info`                                           | `probe_city`, `probe_country`, `probe_asn`, `probe_network` | Which Globalping probe served the run |

Samples are pushed with the run's start timestamp, so one run = one point per series.
`hop_ip="???"` means the hop did not reply. Loss at an intermediate hop that does *not* carry on
to later hops is usually just ICMP rate limiting on that router, not real loss.

## Development

```sh
docker compose run --rm --no-deps collector python -m unittest -v   # tests
python tools/gen_dashboard.py grafana/dashboards/hopwatch.json      # regenerate dashboard
```

Collector layout (`collector/hopwatch/`):

- `config.py`: loads the YAML config
- `local_mtr.py`: runs `mtr --json` and parses it
- `globalping.py`: creates a Globalping mtr measurement, polls it, parses it
- `model.py`: `Trace`/`Hop` dataclasses → Prometheus text with timestamps
- `__main__.py`: scheduler (one loop per source × target) and push to VictoriaMetrics

Known limitations: a failed push is not retried or buffered, and only the first IP per hop is
recorded (ECMP paths show up as route changes rather than parallel hops).
