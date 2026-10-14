"""Site-side summarisation: case records in, aggregate statistics out.

Runs inside the laboratory's boundary. It reads only the columns the model
specification names (score, label, features, subgroup attributes); any other
column in the extract, identifiers included, is never read into the output.
Subgroups smaller than ``min_subgroup`` are suppressed so small cells cannot
single out patients; the overall histogram is never suppressed because the
platform enforces a minimum population size instead.
"""

from __future__ import annotations

import bisect
import csv
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from cross_lab_ai_validation.domain.aggregates import (
    PerformanceAggregate,
    PopulationAggregate,
    ScoreHistogram,
)
from cross_lab_ai_validation.domain.errors import InsufficientDataError, InvalidAggregateError
from cross_lab_ai_validation.domain.models import (
    OTHER_CATEGORY,
    ContinuousFeature,
    ModelVersion,
)


class CaseRecord(BaseModel):
    """One de-identified case as held at the site. Never leaves the site."""

    model_config = ConfigDict(frozen=True)

    score: float = Field(ge=0.0, le=1.0)
    label: int | None = Field(default=None, ge=0, le=1)
    features: dict[str, float | str] = Field(default_factory=dict)
    subgroups: dict[str, str] = Field(default_factory=dict)


class SiteSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    performance: PerformanceAggregate | None
    population: PopulationAggregate
    suppressed_subgroups: tuple[str, ...]


def score_bin(score: float, edges: Sequence[float]) -> int:
    """Bin index for a probability score; the last bin is closed at 1.0."""
    return min(bisect.bisect_right(edges, score) - 1, len(edges) - 2)


def feature_bin(value: float, interior_edges: Sequence[float]) -> int:
    """Bin index over (-inf, e0), [e0, e1), ..., [e_last, +inf)."""
    return bisect.bisect_right(interior_edges, value)


def histogram(records: Iterable[CaseRecord], edges: Sequence[float]) -> ScoreHistogram:
    bins = len(edges) - 1
    positives = [0] * bins
    negatives = [0] * bins
    pos_sum = [0.0] * bins
    neg_sum = [0.0] * bins
    sq = 0.0
    for record in records:
        if record.label is None:
            raise InvalidAggregateError("performance aggregates need a label on every case")
        i = score_bin(record.score, edges)
        sq += record.score * record.score
        if record.label == 1:
            positives[i] += 1
            pos_sum[i] += record.score
        else:
            negatives[i] += 1
            neg_sum[i] += record.score
    return ScoreHistogram(
        positives=tuple(positives),
        negatives=tuple(negatives),
        positive_score_sum=tuple(pos_sum),
        negative_score_sum=tuple(neg_sum),
        score_sq_sum=sq,
    )


def population(model: ModelVersion, records: Sequence[CaseRecord]) -> PopulationAggregate:
    edges = model.score_edges
    score_counts = [0] * model.score_bins
    for record in records:
        score_counts[score_bin(record.score, edges)] += 1
    continuous: dict[str, tuple[int, ...]] = {}
    categorical: dict[str, dict[str, int]] = {}
    for feature in model.features:
        values = []
        for index, record in enumerate(records):
            if feature.name not in record.features:
                raise InvalidAggregateError(f"case {index} lacks feature {feature.name!r}")
            values.append(record.features[feature.name])
        if isinstance(feature, ContinuousFeature):
            counts = [0] * feature.bins
            for value in values:
                if isinstance(value, str):
                    raise InvalidAggregateError(f"feature {feature.name!r} must be numeric")
                counts[feature_bin(value, feature.edges)] += 1
            continuous[feature.name] = tuple(counts)
        else:
            known = set(feature.categories)
            categorical[feature.name] = dict(
                Counter(str(v) if str(v) in known else OTHER_CATEGORY for v in values)
            )
    return PopulationAggregate(
        n=len(records),
        continuous=continuous,
        categorical=categorical,
        score_counts=tuple(score_counts),
    )


def subgroups(
    model: ModelVersion, records: Sequence[CaseRecord], min_subgroup: int
) -> tuple[dict[str, ScoreHistogram], list[str]]:
    """Subgroup histograms, suppressing groups smaller than ``min_subgroup``."""
    result: dict[str, ScoreHistogram] = {}
    suppressed: list[str] = []
    for attribute in model.subgroup_attributes:
        groups: dict[str, list[CaseRecord]] = {}
        for record in records:
            level = record.subgroups.get(attribute)
            if level:
                groups.setdefault(level, []).append(record)
        for level, members in sorted(groups.items()):
            key = f"{attribute}={level}"
            if len(members) < min_subgroup:
                suppressed.append(key)
            else:
                result[key] = histogram(members, model.score_edges)
    return result, suppressed


def summarize(
    model: ModelVersion,
    records: Sequence[CaseRecord],
    *,
    min_cases: int = 20,
    min_subgroup: int = 10,
) -> SiteSummary:
    if len(records) < min_cases:
        raise InsufficientDataError(
            f"{len(records)} cases; at least {min_cases} are needed before anything is shared"
        )
    unlabeled = population(model, records)
    labeled = [r for r in records if r.label is not None]
    if not labeled:
        return SiteSummary(performance=None, population=unlabeled, suppressed_subgroups=())
    if len(labeled) != len(records):
        raise InvalidAggregateError("label every case or none: partial labels bias the benchmark")
    groups, suppressed = subgroups(model, labeled, min_subgroup)
    return SiteSummary(
        performance=PerformanceAggregate(
            overall=histogram(labeled, model.score_edges), subgroups=groups
        ),
        population=unlabeled,
        suppressed_subgroups=tuple(suppressed),
    )


def read_cases(path: Path, model: ModelVersion) -> list[CaseRecord]:
    """Read a CSV extract; only model-relevant columns are parsed."""
    numeric = {f.name for f in model.features if isinstance(f, ContinuousFeature)}
    wanted = {f.name for f in model.features}
    records: list[CaseRecord] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = ({"score"} | wanted) - set(reader.fieldnames or [])
        if missing:
            raise InvalidAggregateError(f"case file lacks columns {sorted(missing)}")
        for line, row in enumerate(reader, start=2):
            try:
                label_text = (row.get("label") or "").strip()
                features: dict[str, float | str] = {
                    name: float(row[name]) if name in numeric else row[name] for name in wanted
                }
                records.append(
                    CaseRecord(
                        score=float(row["score"]),
                        label=int(label_text) if label_text else None,
                        features=features,
                        subgroups={
                            a: row[a].strip()
                            for a in model.subgroup_attributes
                            if row.get(a) and row[a].strip()
                        },
                    )
                )
            except (ValueError, TypeError) as exc:
                raise InvalidAggregateError(f"line {line}: {exc}") from exc
    return records
