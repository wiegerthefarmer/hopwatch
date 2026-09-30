"""Generates grafana/dashboards/hopwatch.json.

Usage: python tools/gen_dashboard.py grafana/dashboards/hopwatch.json
Edit this file rather than the JSON; or edit in the Grafana UI and export the JSON.
"""

import json
import sys

DS = {"type": "prometheus", "uid": "vm"}
SEL = '{source="$source",target="$target"}'
_id = 0
_y = 0


def nid():
    global _id
    _id += 1
    return _id


def target(expr, legend="", ref="A", instant=False, fmt="time_series"):
    t = {"datasource": DS, "expr": expr, "legendFormat": legend or "__auto", "refId": ref}
    if instant:
        t |= {"instant": True, "range": False, "format": "table"}
    return t


def panel(kind, title, targets, w=12, h=8, x=0, unit=None, desc=None, custom=None, overrides=None, options=None, **extra):
    fc = {"defaults": {"custom": custom or {}}, "overrides": overrides or []}
    if unit:
        fc["defaults"]["unit"] = unit
    p = {
        "id": nid(),
        "type": kind,
        "title": title,
        "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": _y},
        "targets": targets,
        "fieldConfig": fc,
        "options": options or {},
    }
    if desc:
        p["description"] = desc
    p.update(extra)
    return p


def row(title):
    global _y
    p = {"id": nid(), "type": "row", "title": title, "collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": _y}, "panels": []}
    _y += 1
    return p


def advance(h):
    global _y
    _y += h


LINES = {"drawStyle": "line", "lineWidth": 1, "fillOpacity": 0, "showPoints": "never", "spanNulls": False}
TS_OPTS = {"legend": {"displayMode": "table", "placement": "right", "calcs": ["mean", "max"]}, "tooltip": {"mode": "multi", "sort": "desc"}}

panels = []

# ---- Overview: every source/target ----
panels.append(row("Overview (all sources and targets)"))
panels.append(panel("timeseries", "Destination latency (avg)", [target("mtr_dest_rtt_avg_ms", "{{source}} → {{target}}")], unit="ms", custom=LINES, options=TS_OPTS))
panels.append(panel("timeseries", "Destination packet loss", [target("mtr_dest_loss_percent", "{{source}} → {{target}}")], x=12, unit="percent", custom=LINES | {"drawStyle": "bars", "fillOpacity": 60}, options=TS_OPTS))
advance(8)
panels.append(panel(
    "timeseries", "Route changes per hour",
    [target("changes(mtr_path_hash[1h])", "{{source}} → {{target}}")],
    desc="How often the sequence of hop IPs changed. Frequent changes usually mean load balancing (ECMP) or route flapping.",
    custom=LINES | {"drawStyle": "bars", "fillOpacity": 60}, options=TS_OPTS,
))
panels.append(panel(
    "state-timeline", "Collector runs",
    [target("mtr_run_success", "{{source}} → {{target}}")],
    x=12,
    desc="Green = mtr completed. Red = failed (see collector logs: docker compose logs collector).",
    overrides=[],
    options={"showValue": "never", "mergeValues": True},
    **{"fieldConfig": {"defaults": {"mappings": [{"type": "value", "options": {"0": {"text": "failed", "color": "red"}, "1": {"text": "ok", "color": "green"}}}], "color": {"mode": "thresholds"}, "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}]}}, "overrides": []}},
))
advance(8)

# ---- Path detail for $source / $target ----
panels.append(row("Path: $source → $target"))
latest = lambda m: f"{m}{SEL} and (timestamp({m}{SEL}) >= scalar(max(timestamp(mtr_path_hops{SEL}))))"
cols = [("mtr_hop_loss_percent", "Loss %"), ("mtr_hop_rtt_avg_ms", "Avg ms"), ("mtr_hop_rtt_best_ms", "Best ms"), ("mtr_hop_rtt_worst_ms", "Worst ms"), ("mtr_hop_rtt_stdev_ms", "StDev ms"), ("mtr_hop_sent", "Sent")]
refs = "ABCDEF"
panels.append(panel(
    "table", "Latest route (mtr report)",
    [target(latest(m), ref=refs[i], instant=True) for i, (m, _) in enumerate(cols)],
    w=24, h=10,
    desc="Hops from the most recent run. ??? = hop did not reply (common for routers that rate-limit ICMP; only loss that continues to the destination matters).",
    transformations=[
        {"id": "merge", "options": {}},
        {"id": "organize", "options": {
            "excludeByName": {"Time": True, "source": True, "target": True, "__name__": True},
            "indexByName": {"hop": 0, "hop_ip": 1, "asn": 2} | {f"Value #{refs[i]}": 3 + i for i in range(len(cols))},
            "renameByName": {"hop": "Hop", "hop_ip": "Host", "asn": "ASN"} | {f"Value #{refs[i]}": name for i, (_, name) in enumerate(cols)},
        }},
        {"id": "convertFieldType", "options": {"conversions": [{"targetField": "Hop", "destinationType": "number"}]}},
        {"id": "sortBy", "options": {"sort": [{"field": "Hop"}]}},
    ],
    overrides=[{
        "matcher": {"id": "byName", "options": "Loss %"},
        "properties": [
            {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
            {"id": "thresholds", "value": {"mode": "absolute", "steps": [{"color": "transparent", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 10}]}},
        ],
    }],
    options={"showHeader": True, "cellHeight": "sm"},
))
advance(10)
panels.append(panel(
    "timeseries", "Destination latency spread (smoke)",
    [
        target(f"mtr_dest_rtt_best_ms{SEL}", "best", "A"),
        target(f"mtr_dest_rtt_avg_ms{SEL}", "avg", "B"),
        target(f"mtr_dest_rtt_worst_ms{SEL}", "worst", "C"),
    ],
    w=16, unit="ms",
    desc="Shaded band = best..worst RTT within each run; line = average. A wide band means jitter.",
    custom=LINES,
    overrides=[
        {"matcher": {"id": "byName", "options": "worst"}, "properties": [{"id": "custom.fillBelowTo", "value": "best"}, {"id": "custom.fillOpacity", "value": 25}, {"id": "custom.lineWidth", "value": 0}, {"id": "color", "value": {"mode": "fixed", "fixedColor": "blue"}}]},
        {"matcher": {"id": "byName", "options": "best"}, "properties": [{"id": "custom.lineWidth", "value": 0}, {"id": "color", "value": {"mode": "fixed", "fixedColor": "blue"}}]},
        {"matcher": {"id": "byName", "options": "avg"}, "properties": [{"id": "custom.lineWidth", "value": 2}, {"id": "color", "value": {"mode": "fixed", "fixedColor": "dark-blue"}}]},
    ],
    options={"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
))
panels.append(panel("timeseries", "Destination loss", [target(f"mtr_dest_loss_percent{SEL}", "loss")], w=8, x=16, unit="percent", custom=LINES | {"drawStyle": "bars", "fillOpacity": 70}, options={"legend": {"showLegend": False}}))
advance(8)
panels.append(panel(
    "timeseries", "Per-hop latency (avg)",
    [target(f"mtr_hop_rtt_avg_ms{SEL}", "{{hop}} {{hop_ip}}")],
    h=10, unit="ms", desc="A step up that persists to every later hop points at the hop where latency is added.",
    custom=LINES, options=TS_OPTS,
))
panels.append(panel(
    "timeseries", "Per-hop loss",
    [target(f"mtr_hop_loss_percent{SEL}", "{{hop}} {{hop_ip}}")],
    h=10, x=12, unit="percent", custom=LINES, options=TS_OPTS,
))
advance(10)

# ---- blackbox_exporter ----
panels.append(row("HTTP/S and ping (blackbox_exporter)"))
panels.append(panel(
    "state-timeline", "Probe success", [target("probe_success", "{{job}} {{instance}}")],
    options={"showValue": "never", "mergeValues": True},
    **{"fieldConfig": {"defaults": {"mappings": [{"type": "value", "options": {"0": {"text": "down", "color": "red"}, "1": {"text": "up", "color": "green"}}}], "color": {"mode": "thresholds"}, "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}]}}, "overrides": []}},
))
panels.append(panel(
    "timeseries", "HTTP timing by phase: $http_instance",
    [target('probe_http_duration_seconds{instance="$http_instance"}', "{{phase}}")],
    x=12, unit="s", custom=LINES | {"drawStyle": "bars", "fillOpacity": 80, "stacking": {"mode": "normal"}},
    options={"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
))
advance(8)
panels.append(panel(
    "timeseries", "ICMP round trip", [target('probe_icmp_duration_seconds{phase="rtt"}', "{{instance}}")],
    unit="s", custom=LINES, options=TS_OPTS,
))
panels.append(panel(
    "bargauge", "TLS certificate expires in", [target("(probe_ssl_earliest_cert_expiry - time()) / 86400", "{{instance}}", instant=False)],
    x=12, unit="d",
    options={"orientation": "horizontal", "displayMode": "basic", "reduceOptions": {"calcs": ["lastNotNull"]}},
    **{"fieldConfig": {"defaults": {"unit": "d", "decimals": 0, "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 14}, {"color": "green", "value": 30}]}}, "overrides": []}},
))
advance(8)

# ---- Uptime Kuma ----
panels.append(row("Uptime Kuma"))
panels.append(panel(
    "state-timeline", "Monitor status", [target("monitor_status", "{{monitor_name}}")],
    desc="Needs ./secrets/kuma_api_key; see README.",
    options={"showValue": "never", "mergeValues": True},
    **{"fieldConfig": {"defaults": {"mappings": [{"type": "value", "options": {"0": {"text": "down", "color": "red"}, "1": {"text": "up", "color": "green"}, "2": {"text": "pending", "color": "orange"}, "3": {"text": "maintenance", "color": "blue"}}}], "color": {"mode": "thresholds"}, "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]}}, "overrides": []}},
))
panels.append(panel("timeseries", "Monitor response time", [target("monitor_response_time", "{{monitor_name}}")], x=12, unit="ms", custom=LINES, options=TS_OPTS))
advance(8)


def var(name, query, label):
    return {
        "name": name, "label": label, "type": "query", "datasource": DS,
        "query": {"query": query, "refId": name}, "definition": query,
        "refresh": 2, "sort": 1, "multi": False, "includeAll": False,
    }


dashboard = {
    "uid": "hopwatch",
    "title": "hopwatch",
    "tags": ["mtr", "network"],
    "timezone": "browser",
    "schemaVersion": 39,
    "refresh": "1m",
    "time": {"from": "now-6h", "to": "now"},
    "templating": {"list": [
        var("source", "label_values(mtr_run_success, source)", "Source"),
        var("target", 'label_values(mtr_run_success{source="$source"}, target)', "Target"),
        var("http_instance", "label_values(probe_http_duration_seconds, instance)", "HTTP target"),
    ]},
    "panels": panels,
}

with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(dashboard, f, indent=2, ensure_ascii=False)
    f.write("\n")
