"""Runs mtr measurements from remote Globalping probes (https://globalping.io).

API docs: https://globalping.io/docs/api.globalping.io
Each probe result counts as one test against the hourly rate limit, so keep
locations x targets x runs/hour within your quota.
"""

import asyncio
import time

import httpx

from .config import GlobalpingConfig, GlobalpingLocation, Target
from .model import Hop, Trace

API = "https://api.globalping.io/v1/measurements"
POLL_INTERVAL = 1.0
POLL_TIMEOUT = 90.0


def build_request(target: Target, location: GlobalpingLocation, packets: int) -> dict:
    options: dict = {"protocol": target.protocol.upper(), "packets": packets}
    if target.port and target.protocol != "icmp":
        options["port"] = target.port
    if target.ip_version:
        # Globalping only accepts ipVersion when the target is a hostname.
        options["ipVersion"] = target.ip_version
    return {
        "type": "mtr",
        "target": target.host,
        "locations": [{"magic": location.magic, "limit": 1}],
        "measurementOptions": options,
    }


def parse_hops(hops: list[dict]) -> list[Hop]:
    parsed = []
    for i, h in enumerate(hops, start=1):
        stats = h.get("stats") or {}
        ip = h.get("resolvedAddress") or None
        asns = h.get("asn") or []
        get = (lambda key: stats.get(key)) if ip else (lambda key: None)
        parsed.append(
            Hop(
                number=i,
                ip=ip,
                loss_percent=float(stats.get("loss", 100)),
                sent=int(stats.get("total", 0)),
                avg_ms=get("avg"),
                best_ms=get("min"),
                worst_ms=get("max"),
                stdev_ms=get("stDev"),
                asn=f"AS{asns[0]}" if asns else None,
            )
        )
    return parsed


def parse_measurement(measurement: dict, trace: Trace) -> None:
    """Fill trace.hops / trace.probe / trace.error from a finished measurement."""
    results = measurement.get("results") or []
    if not results:
        trace.error = "no probe result"
        return
    probe = results[0].get("probe") or {}
    trace.probe = {
        k: str(v)
        for k, v in {
            "city": probe.get("city"),
            "country": probe.get("country"),
            "asn": f"AS{probe['asn']}" if probe.get("asn") else None,
            "network": probe.get("network"),
        }.items()
        if v
    }
    result = results[0].get("result") or {}
    if result.get("status") != "finished":
        trace.error = f"probe status: {result.get('status')}"
        return
    trace.hops = parse_hops(result.get("hops") or [])
    if not trace.hops:
        trace.error = "probe returned no hops"


class GlobalpingClient:
    def __init__(self, cfg: GlobalpingConfig, http: httpx.AsyncClient):
        self.cfg = cfg
        self.http = http
        self.headers = {"Authorization": f"Bearer {cfg.token}"} if cfg.token else {}

    async def run(self, target: Target, location: GlobalpingLocation) -> Trace:
        started = time.time()
        trace = Trace(source=location.name, target=target.name, timestamp=started, duration=0)
        try:
            await self._measure(target, location, trace)
        except httpx.HTTPError as e:
            trace.error = f"{type(e).__name__}: {e}"
        trace.duration = time.time() - started
        return trace

    async def _measure(self, target: Target, location: GlobalpingLocation, trace: Trace) -> None:
        r = await self.http.post(
            API, json=build_request(target, location, self.cfg.packets), headers=self.headers
        )
        if r.status_code == 429:
            trace.error = "rate limited (429); lower interval/locations or set GLOBALPING_TOKEN"
            return
        if r.status_code >= 400:
            trace.error = f"HTTP {r.status_code}: {r.text[:200]}"
            return
        measurement_id = r.json()["id"]

        deadline = time.monotonic() + POLL_TIMEOUT
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            r = await self.http.get(f"{API}/{measurement_id}", headers=self.headers)
            r.raise_for_status()
            measurement = r.json()
            if measurement.get("status") != "in-progress":
                break
            if time.monotonic() > deadline:
                trace.error = "measurement did not finish in time"
                return
        parse_measurement(measurement, trace)
