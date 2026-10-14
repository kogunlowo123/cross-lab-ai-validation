"""Load profile for a deployed assurance platform.

Run against a staging deployment, never production:

    CLV_LOAD_AGENT_TOKEN=<agent credential> CLV_LOAD_SITE=<site id> \
    CLV_LOAD_AGGREGATES=aggregates.json CLV_LOAD_MODEL=<model id> CLV_LOAD_VERSION=<x.y.z> \
    locust -f tests/performance/locustfile.py --host https://assurance.staging.internal \
           --users 50 --spawn-rate 5 --run-time 10m --headless

``aggregates.json`` is the output of ``clv-site summarize`` for a synthetic
site. Site agents read approvals far more often than they submit evidence,
so the mix weights reads over benchmark and shift submissions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from locust import HttpUser, between, task

TOKEN = os.environ.get("CLV_LOAD_AGENT_TOKEN", "")
SITE = os.environ.get("CLV_LOAD_SITE", "")
MODEL = os.environ.get("CLV_LOAD_MODEL", "")
VERSION = os.environ.get("CLV_LOAD_VERSION", "")
AGGREGATES_FILE = os.environ.get("CLV_LOAD_AGGREGATES", "")


def _aggregates() -> dict[str, Any]:
    if not AGGREGATES_FILE:
        return {}
    document: dict[str, Any] = json.loads(Path(AGGREGATES_FILE).read_text(encoding="utf-8"))
    return document


class SiteAgentTraffic(HttpUser):
    wait_time = between(0.2, 1.0)

    def on_start(self) -> None:
        self.headers = {"Authorization": f"Bearer {TOKEN}"}
        self.aggregates = _aggregates()
        self.ids = {"site_id": SITE, "model_id": MODEL, "version": VERSION}

    @task(10)
    def approvals(self) -> None:
        self.client.get(
            f"/v1/sites/{SITE}/approvals", headers=self.headers, name="/v1/sites/{id}/approvals"
        )

    @task(2)
    def shift(self) -> None:
        if self.aggregates.get("population"):
            self.client.post(
                "/v1/shift-analyses",
                json={**self.ids, "population": self.aggregates["population"]},
                headers=self.headers,
            )

    @task(1)
    def benchmark(self) -> None:
        if self.aggregates.get("performance"):
            self.client.post(
                "/v1/benchmarks",
                json={**self.ids, "aggregate": self.aggregates["performance"]},
                headers=self.headers,
            )
