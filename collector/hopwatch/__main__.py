"""hopwatch collector: runs mtr traces on a schedule and pushes results to VictoriaMetrics.

Usage: python -m hopwatch [path/to/hopwatch.yml]
"""

import asyncio
import logging
import random
import sys
import time
from collections.abc import Awaitable, Callable

import httpx

from . import config, local_mtr
from .globalping import GlobalpingClient
from .model import Trace, to_prometheus

log = logging.getLogger("hopwatch")

# Anonymous Globalping quota; authenticated users get more.
GLOBALPING_ANON_TESTS_PER_HOUR = 250


async def every(interval: float, job: Callable[[], Awaitable[None]]) -> None:
    # Random initial delay so all targets don't fire at the same moment.
    await asyncio.sleep(random.uniform(0, min(interval, 30)))
    while True:
        started = time.monotonic()
        try:
            await job()
        except Exception:
            log.exception("job failed")
        await asyncio.sleep(max(0.0, interval - (time.monotonic() - started)))


async def push(http: httpx.AsyncClient, url: str, trace: Trace) -> None:
    log.info(trace.summary())
    try:
        r = await http.post(url, content=to_prometheus(trace))
        r.raise_for_status()
    except httpx.HTTPError as e:
        # No buffering: a failed push drops this run's data point.
        log.warning("push to %s failed: %s", url, e)


async def main(config_path: str) -> None:
    cfg = config.load(config_path)
    jobs = []

    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "hopwatch/0.1"}) as http:
        if cfg.local.enabled:
            sem = asyncio.Semaphore(cfg.local.max_parallel)
            for target in cfg.targets:

                async def local_job(target=target):
                    async with sem:
                        trace = await local_mtr.run(target, cfg.local)
                    await push(http, cfg.push_url, trace)

                jobs.append(every(cfg.local.interval, local_job))

        if cfg.globalping.enabled:
            gp = GlobalpingClient(cfg.globalping, http)
            rate = cfg.globalping.tests_per_hour(len(cfg.targets))
            log.info("globalping: ~%.0f tests/hour", rate)
            if rate > GLOBALPING_ANON_TESTS_PER_HOUR and not cfg.globalping.token:
                log.warning("that exceeds the anonymous limit (%d/h); set GLOBALPING_TOKEN", GLOBALPING_ANON_TESTS_PER_HOUR)
            for location in cfg.globalping.locations:
                for target in cfg.targets:

                    async def gp_job(target=target, location=location):
                        await push(http, cfg.push_url, await gp.run(target, location))

                    jobs.append(every(cfg.globalping.interval, gp_job))

        if not jobs:
            raise SystemExit("nothing to do: enable local and/or globalping in the config")
        log.info("started %d jobs, pushing to %s", len(jobs), cfg.push_url)
        await asyncio.gather(*jobs)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # otherwise logs every request
    try:
        asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "/config/hopwatch.yml"))
    except KeyboardInterrupt:
        pass
