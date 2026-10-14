"""``clv-site``: run the federated site agent.

    clv-site summarize --model model.json --cases cases.csv --out aggregates.json
    clv-site submit --central URL --fabric URL --agent-uri spiffe://... \
                    --workload-token-file /var/run/secrets/tokens/clv \
                    --site lab-a --model-id ID --version X.Y.Z --cases cases.csv

``summarize`` writes the aggregates that would be shared, for local review.
``submit`` obtains an agent credential from agent-identity-fabric, fetches the
registered model specification, summarises locally and submits only the
aggregates for benchmarking and shift analysis.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

import httpx

from cross_lab_ai_validation.domain.errors import AssuranceError
from cross_lab_ai_validation.domain.models import ModelVersion
from cross_lab_ai_validation.federation.client import CentralClient, obtain_credential
from cross_lab_ai_validation.federation.summarize import read_cases, summarize
from cross_lab_ai_validation.integrations.identity_fabric import IdentityFabricClient


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clv-site", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    local = sub.add_parser("summarize", help="compute aggregates locally")
    local.add_argument("--model", type=Path, required=True, help="model version JSON")
    local.add_argument("--cases", type=Path, required=True, help="case extract CSV")
    local.add_argument("--out", type=Path, required=True)
    local.add_argument("--min-cases", type=int, default=20)
    local.add_argument("--min-subgroup", type=int, default=10)

    remote = sub.add_parser("submit", help="summarise and submit aggregates")
    remote.add_argument("--central", required=True, help="central platform base URL")
    remote.add_argument("--fabric", required=True, help="agent-identity-fabric base URL")
    remote.add_argument("--agent-uri", required=True)
    remote.add_argument("--workload-token-file", type=Path, required=True)
    remote.add_argument("--site", required=True)
    remote.add_argument("--model-id", required=True)
    remote.add_argument("--version", required=True)
    remote.add_argument("--cases", type=Path, required=True)
    remote.add_argument("--min-cases", type=int, default=20)
    remote.add_argument("--min-subgroup", type=int, default=10)
    return parser


def _summarize(args: argparse.Namespace, out: TextIO) -> int:
    model = ModelVersion.model_validate_json(args.model.read_text(encoding="utf-8"))
    summary = summarize(
        model,
        read_cases(args.cases, model),
        min_cases=args.min_cases,
        min_subgroup=args.min_subgroup,
    )
    args.out.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    out.write(
        f"wrote {args.out}: {summary.population.n} cases, "
        f"{len(summary.suppressed_subgroups)} subgroups suppressed\n"
    )
    return 0


async def submit(
    args: argparse.Namespace, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), transport=transport) as http:
        token = await obtain_credential(
            IdentityFabricClient(args.fabric, client=http),
            agent_uri=args.agent_uri,
            workload_token_file=args.workload_token_file,
            site_id=args.site,
        )
        central = CentralClient(args.central, client=http, token=token)
        model = await central.model(args.model_id, args.version)
        summary = summarize(
            model,
            read_cases(args.cases, model),
            min_cases=args.min_cases,
            min_subgroup=args.min_subgroup,
        )
        result: dict[str, Any] = {"suppressed_subgroups": list(summary.suppressed_subgroups)}
        if summary.performance is not None:
            result["benchmark"] = await central.submit_benchmark(
                args.site, model, summary.performance
            )
        result["shift"] = await central.submit_population(args.site, model, summary.population)
        return result


def main(argv: Sequence[str] | None = None, out: TextIO = sys.stdout) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "summarize":
            return _summarize(args, out)
        result = asyncio.run(submit(args))
    except (AssuranceError, OSError) as exc:
        sys.stderr.write(f"clv-site: {exc}\n")
        return 1
    out.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
