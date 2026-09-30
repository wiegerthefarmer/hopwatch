"""Trace/hop data model and conversion to Prometheus text exposition format."""

import zlib
from dataclasses import dataclass, field


@dataclass
class Hop:
    number: int  # TTL, starting at 1
    ip: str | None  # None when the hop never replied ("???" in mtr)
    loss_percent: float
    sent: int
    avg_ms: float | None = None
    best_ms: float | None = None
    worst_ms: float | None = None
    stdev_ms: float | None = None
    asn: str | None = None  # e.g. "AS13335"


@dataclass
class Trace:
    source: str  # where the trace ran from ("local", "gp-frankfurt", ...)
    target: str  # Target.name
    timestamp: float  # unix seconds when the run started
    duration: float  # seconds
    hops: list[Hop] = field(default_factory=list)
    error: str | None = None
    probe: dict[str, str] = field(default_factory=dict)  # remote probe details (Globalping)

    def summary(self) -> str:
        if self.error:
            return f"{self.source} -> {self.target}: ERROR {self.error}"
        dest = self.hops[-1]
        rtt = f"{dest.avg_ms:.1f}ms" if dest.avg_ms is not None else "no reply"
        return f"{self.source} -> {self.target}: {len(self.hops)} hops, dest loss {dest.loss_percent:.0f}% avg {rtt}"


def path_hash(hops: list[Hop]) -> int:
    """Fingerprint of the hop IP sequence; changes whenever the route changes."""
    return zlib.crc32(",".join(h.ip or "*" for h in hops).encode())


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def to_prometheus(trace: Trace) -> str:
    """Render a trace as Prometheus text lines with explicit millisecond timestamps,
    suitable for VictoriaMetrics' /api/v1/import/prometheus endpoint."""
    ts = int(trace.timestamp * 1000)
    lines: list[str] = []

    def add(name: str, labels: dict[str, str], value: float | int | None) -> None:
        if value is None:
            return
        label_str = ",".join(f'{k}="{_escape(v)}"' for k, v in labels.items() if v)
        lines.append(f"{name}{{{label_str}}} {value} {ts}")

    base = {"source": trace.source, "target": trace.target}
    add("mtr_run_success", base, 0 if trace.error else 1)
    add("mtr_run_duration_seconds", base, round(trace.duration, 3))
    if trace.probe:
        add("mtr_probe_info", base | {f"probe_{k}": v for k, v in trace.probe.items()}, 1)
    if trace.error or not trace.hops:
        return "\n".join(lines) + "\n"

    add("mtr_path_hops", base, len(trace.hops))
    add("mtr_path_hash", base, path_hash(trace.hops))

    for hop in trace.hops:
        labels = base | {"hop": str(hop.number), "hop_ip": hop.ip or "???", "asn": hop.asn or ""}
        add("mtr_hop_loss_percent", labels, hop.loss_percent)
        add("mtr_hop_sent", labels, hop.sent)
        add("mtr_hop_rtt_avg_ms", labels, hop.avg_ms)
        add("mtr_hop_rtt_best_ms", labels, hop.best_ms)
        add("mtr_hop_rtt_worst_ms", labels, hop.worst_ms)
        add("mtr_hop_rtt_stdev_ms", labels, hop.stdev_ms)

    # The last hop is the destination (or the last thing that answered). Exported
    # without hop labels so it stays one series per source/target even when the
    # path length changes.
    dest = trace.hops[-1]
    add("mtr_dest_loss_percent", base, dest.loss_percent)
    add("mtr_dest_rtt_avg_ms", base, dest.avg_ms)
    add("mtr_dest_rtt_best_ms", base, dest.best_ms)
    add("mtr_dest_rtt_worst_ms", base, dest.worst_ms)
    add("mtr_dest_rtt_stdev_ms", base, dest.stdev_ms)

    return "\n".join(lines) + "\n"
