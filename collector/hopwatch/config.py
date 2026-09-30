"""Loads hopwatch.yml into typed config objects."""

import os
from dataclasses import dataclass, field

import yaml

PROTOCOLS = ("icmp", "tcp", "udp")


@dataclass
class Target:
    name: str  # stable label used in metrics, e.g. "cloudflare-dns"
    host: str  # hostname or IP to trace
    protocol: str = "icmp"
    port: int | None = None  # only used for tcp/udp
    ip_version: int | None = None  # 4 or 6; None = let the resolver decide


@dataclass
class LocalConfig:
    enabled: bool = True
    source: str = "local"  # value of the "source" label for local runs
    interval: int = 60  # seconds between runs, per target
    cycles: int = 10  # probes sent per hop per run (mtr -c)
    asn_lookup: bool = False  # mtr --aslookup (extra DNS queries to cymru)
    max_parallel: int = 4  # concurrent mtr processes


@dataclass
class GlobalpingLocation:
    name: str  # value of the "source" label, e.g. "gp-frankfurt"
    magic: str  # Globalping "magic" location string, e.g. "Frankfurt" or "AS3320"


@dataclass
class GlobalpingConfig:
    enabled: bool = False
    interval: int = 900
    packets: int = 3  # 1-16
    locations: list[GlobalpingLocation] = field(default_factory=list)
    token: str | None = None

    def tests_per_hour(self, n_targets: int) -> float:
        return len(self.locations) * n_targets * 3600 / self.interval


@dataclass
class Config:
    push_url: str
    targets: list[Target]
    local: LocalConfig
    globalping: GlobalpingConfig


def _location(raw) -> GlobalpingLocation:
    if isinstance(raw, str):
        return GlobalpingLocation(name="gp-" + raw.lower().replace(" ", "-"), magic=raw)
    return GlobalpingLocation(**raw)


def load(path: str) -> Config:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    targets = [Target(**t) for t in raw.get("targets") or []]
    if not targets:
        raise ValueError(f"{path}: no targets configured")
    for t in targets:
        if t.protocol not in PROTOCOLS:
            raise ValueError(f"target {t.name}: protocol must be one of {PROTOCOLS}")

    gp_raw = dict(raw.get("globalping") or {})
    locations = [_location(loc) for loc in gp_raw.pop("locations", None) or []]
    file_token = gp_raw.pop("token", None)
    token = os.environ.get("GLOBALPING_TOKEN") or file_token

    return Config(
        push_url=os.environ.get("HOPWATCH_PUSH_URL")
        or raw.get("push_url", "http://victoriametrics:8428/api/v1/import/prometheus"),
        targets=targets,
        local=LocalConfig(**(raw.get("local") or {})),
        globalping=GlobalpingConfig(**gp_raw, locations=locations, token=token),
    )
