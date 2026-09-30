"""Runs the local mtr binary and parses its --json report."""

import asyncio
import json
import time

from .config import LocalConfig, Target
from .model import Hop, Trace


def build_command(target: Target, cfg: LocalConfig) -> list[str]:
    cmd = ["mtr", "--json", "--no-dns", "--report-cycles", str(cfg.cycles)]
    if cfg.asn_lookup:
        cmd.append("--aslookup")
    if target.protocol == "tcp":
        cmd.append("--tcp")
    elif target.protocol == "udp":
        cmd.append("--udp")
    if target.port and target.protocol != "icmp":
        cmd += ["--port", str(target.port)]
    if target.ip_version:
        cmd.append(f"-{target.ip_version}")
    cmd.append(target.host)
    return cmd


def parse(output: str | bytes) -> list[Hop]:
    hops = []
    for hub in json.loads(output)["report"]["hubs"]:
        ip = None if hub["host"] == "???" else hub["host"]
        asn = hub.get("ASN")
        # mtr reports 0.0 for RTTs of hops that never answered; drop those so
        # they don't show up as fake 0 ms latency.
        rtt = (lambda key: float(hub[key])) if ip else (lambda key: None)
        hops.append(
            Hop(
                number=int(hub["count"]),  # int in mtr 0.95, string in older versions
                ip=ip,
                loss_percent=float(hub["Loss%"]),
                sent=int(hub["Snt"]),
                avg_ms=rtt("Avg"),
                best_ms=rtt("Best"),
                worst_ms=rtt("Wrst"),
                stdev_ms=rtt("StDev"),
                asn=asn if asn and asn != "AS???" else None,
            )
        )
    return hops


async def run(target: Target, cfg: LocalConfig) -> Trace:
    started = time.time()
    trace = Trace(source=cfg.source, target=target.name, timestamp=started, duration=0)

    proc = await asyncio.create_subprocess_exec(
        *build_command(target, cfg),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        # One cycle is ~1 s plus per-hop timeouts; leave generous headroom.
        out, err = await asyncio.wait_for(proc.communicate(), timeout=cfg.cycles * 3 + 30)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        trace.error = "mtr timed out"
    else:
        if proc.returncode != 0:
            trace.error = err.decode(errors="replace").strip() or f"mtr exited with {proc.returncode}"
        else:
            try:
                trace.hops = parse(out)
            except (ValueError, KeyError) as e:
                trace.error = f"could not parse mtr output: {e!r}"
            if not trace.error and not trace.hops:
                trace.error = "mtr returned no hops"

    trace.duration = time.time() - started
    return trace
