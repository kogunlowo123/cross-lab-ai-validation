"""Export published contracts: JSON Schemas for inputs/outputs and the OpenAPI document.

python -m cross_lab_ai_validation.contracts            # rewrite schemas/ and docs/api/
python -m cross_lab_ai_validation.contracts --check    # exit 1 if committed files are stale
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from cross_lab_ai_validation.api.app import create_app
from cross_lab_ai_validation.application.service import (
    BenchmarkCommand,
    ReapprovalCommand,
    ShiftCommand,
    ShiftOutcome,
    SignatureCommand,
    ValidationCommand,
)
from cross_lab_ai_validation.domain.aggregates import PerformanceAggregate, PopulationAggregate
from cross_lab_ai_validation.domain.models import (
    ModelVersion,
    ReapprovalRequest,
    Site,
    SiteApproval,
)
from cross_lab_ai_validation.evaluation.reports import BenchmarkReport, ValidationRun

MODELS: dict[str, type[BaseModel]] = {
    "site.request": Site,
    "model-version.request": ModelVersion,
    "performance-aggregate": PerformanceAggregate,
    "population-aggregate": PopulationAggregate,
    "benchmark.request": BenchmarkCommand,
    "benchmark-report.response": BenchmarkReport,
    "shift-analysis.request": ShiftCommand,
    "shift-analysis.response": ShiftOutcome,
    "validation.request": ValidationCommand,
    "validation-run.response": ValidationRun,
    "reapproval.request": ReapprovalCommand,
    "reapproval.response": ReapprovalRequest,
    "signature.request": SignatureCommand,
    "site-approval": SiteApproval,
}


async def _no_container() -> Any:  # pragma: no cover - never started
    raise RuntimeError("contract export does not start the application")


def documents() -> dict[str, dict[str, Any]]:
    docs: dict[str, dict[str, Any]] = {
        f"schemas/{name}.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"https://github.com/kogunlowo123/cross-lab-ai-validation/schemas/{name}.schema.json",
            **model.model_json_schema(mode="validation"),
        }
        for name, model in MODELS.items()
    }
    app: FastAPI = create_app(_no_container)
    docs["docs/api/openapi.json"] = app.openapi()
    return docs


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    base = root or Path.cwd()
    stale: list[str] = []
    for relative, document in documents().items():
        path = base / relative
        content = render(document)
        if "--check" in args:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(relative)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    if stale:
        sys.stderr.write(
            "stale contracts (run python -m cross_lab_ai_validation.contracts): "
            + ", ".join(stale)
            + "\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
