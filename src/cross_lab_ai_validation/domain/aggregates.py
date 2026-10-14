"""Federated aggregate statistics.

Sites never send case-level records. They send the aggregates defined here,
computed inside the site boundary by :mod:`cross_lab_ai_validation.federation`.
Every statistic the platform reports is computable from these aggregates:

* ``ScoreHistogram``: per score bin, the count of positive and negative cases
  and the sum of their scores, plus the total sum of squared scores. This is
  sufficient for sensitivity/specificity/PPV/NPV at any threshold on a bin
  edge, AUROC (exact on the binned scores), its stratified bootstrap (an
  exact multinomial resampling of bin counts), expected calibration error and
  the Brier score.
* ``PopulationAggregate``: unlabeled feature histograms, categorical counts
  and the score histogram used for covariate, prediction and label shift.
* Method-verification studies: agreement counts, precision replicates and
  linearity levels measured on verification material, not patient records.

All models forbid unknown fields, so a submission cannot smuggle record-level
fields (identifiers, dates, free text) through the API.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, field_validator, model_validator

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]


class ScoreHistogram(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    positives: tuple[NonNegativeInt, ...] = Field(min_length=2, max_length=1000)
    negatives: tuple[NonNegativeInt, ...] = Field(min_length=2, max_length=1000)
    positive_score_sum: tuple[FiniteFloat, ...] = Field(min_length=2, max_length=1000)
    negative_score_sum: tuple[FiniteFloat, ...] = Field(min_length=2, max_length=1000)
    score_sq_sum: FiniteFloat = Field(ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> ScoreHistogram:
        k = len(self.positives)
        if not (
            len(self.negatives) == len(self.positive_score_sum) == len(self.negative_score_sum) == k
        ):
            raise ValueError("histogram arrays must all have one entry per score bin")
        for counts, sums in (
            (self.positives, self.positive_score_sum),
            (self.negatives, self.negative_score_sum),
        ):
            for count, total in zip(counts, sums, strict=True):
                # Scores are probabilities, so a bin's score sum lies in [0, count].
                if total < 0 or total > count + 1e-6:
                    raise ValueError("bin score sums must lie between 0 and the bin count")
        return self

    @property
    def bins(self) -> int:
        return len(self.positives)

    @property
    def n_positive(self) -> int:
        return sum(self.positives)

    @property
    def n_negative(self) -> int:
        return sum(self.negatives)

    @property
    def n(self) -> int:
        return self.n_positive + self.n_negative


class PerformanceAggregate(BaseModel):
    """Labeled outcome statistics for one site and one model version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    overall: ScoreHistogram
    subgroups: dict[str, ScoreHistogram] = Field(
        default_factory=dict,
        description="Keyed '<attribute>=<value>', e.g. 'sex=F'; attributes come from the model",
        max_length=200,
    )

    @field_validator("subgroups")
    @classmethod
    def _keys(cls, value: dict[str, ScoreHistogram]) -> dict[str, ScoreHistogram]:
        for key in value:
            attribute, sep, level = key.partition("=")
            if not sep or not attribute or not level or len(key) > 120:
                raise ValueError(f"subgroup key {key!r} must be '<attribute>=<value>'")
        return value


class PopulationAggregate(BaseModel):
    """Unlabeled population statistics used for shift detection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    n: int = Field(ge=1)
    continuous: dict[str, tuple[NonNegativeInt, ...]] = Field(default_factory=dict, max_length=200)
    categorical: dict[str, dict[str, NonNegativeInt]] = Field(default_factory=dict, max_length=200)
    score_counts: tuple[NonNegativeInt, ...] = Field(min_length=2, max_length=1000)

    @model_validator(mode="after")
    def _totals(self) -> PopulationAggregate:
        if sum(self.score_counts) != self.n:
            raise ValueError("score_counts must sum to n")
        for name, counts in self.continuous.items():
            if sum(counts) != self.n:
                raise ValueError(f"continuous feature {name!r} counts must sum to n")
        for name, levels in self.categorical.items():
            if sum(levels.values()) != self.n:
                raise ValueError(f"categorical feature {name!r} counts must sum to n")
        return self


class AgreementStudy(BaseModel):
    """Qualitative accuracy: candidate (AI) result versus the comparator method."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    both_positive: NonNegativeInt
    candidate_positive_comparator_negative: NonNegativeInt
    candidate_negative_comparator_positive: NonNegativeInt
    both_negative: NonNegativeInt
    comparator: str = Field(min_length=3, max_length=200, description="Reference method used")

    @property
    def n(self) -> int:
        return (
            self.both_positive
            + self.candidate_positive_comparator_negative
            + self.candidate_negative_comparator_positive
            + self.both_negative
        )


class PrecisionLevel(BaseModel):
    """One verification material measured as replicates within runs over several days."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    material: str = Field(min_length=1, max_length=120)
    days: tuple[tuple[FiniteFloat, ...], ...] = Field(min_length=2, max_length=60)

    @field_validator("days")
    @classmethod
    def _balanced(cls, days: tuple[tuple[float, ...], ...]) -> tuple[tuple[float, ...], ...]:
        sizes = {len(day) for day in days}
        if len(sizes) != 1 or min(sizes) < 2:
            raise ValueError("precision design must be balanced with at least 2 replicates per day")
        return days


class PrecisionStudy(BaseModel):
    """CLSI EP15-style repeatability and within-laboratory precision study."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    levels: tuple[PrecisionLevel, ...] = Field(min_length=1, max_length=10)


class LinearityLevel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    expected: FiniteFloat
    measurements: tuple[FiniteFloat, ...] = Field(min_length=1, max_length=20)


class LinearityStudy(BaseModel):
    """CLSI EP6-style reportable range study of a quantitative model output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    levels: tuple[LinearityLevel, ...] = Field(min_length=3, max_length=20)

    @field_validator("levels")
    @classmethod
    def _ordered(cls, levels: tuple[LinearityLevel, ...]) -> tuple[LinearityLevel, ...]:
        expected = [level.expected for level in levels]
        if any(b <= a for a, b in pairwise(expected)):
            raise ValueError("linearity levels must have strictly increasing expected values")
        return levels


class VerificationStudies(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agreement: AgreementStudy | None = None
    precision: PrecisionStudy | None = None
    reportable_range: LinearityStudy | None = None
