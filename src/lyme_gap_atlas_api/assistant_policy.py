"""Validated, deployment-controlled Research Assistant behavior policy."""

import json
from functools import lru_cache
from importlib.resources import files
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

QuestionClass = Literal[
    "surveillance_epidemiology",
    "vector_host_pathogen",
    "environment_exposure",
    "study_comparison",
    "diagnostics_interventions_outcomes",
    "atlas_applicability",
]
Strictness = Literal["standard", "heightened"]


class HardRefusal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    personalized_diagnosis: Literal[True]
    personalized_treatment: Literal[True]
    medication_dosing: Literal[True]
    clearly_unsafe: Literal[True]


class DecisionSupportStrictness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surveillance_epidemiology: Strictness
    vector_host_pathogen: Strictness
    environment_exposure: Strictness
    study_comparison: Strictness
    diagnostics_interventions_outcomes: Strictness
    atlas_applicability: Strictness


class AssistantPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["assistant-policy-v1"]
    proactive_follow_up_suggestions: bool
    hard_refusal: HardRefusal
    decision_support_strictness: DecisionSupportStrictness

    def strictness_for(self, question_class: QuestionClass) -> Strictness:
        return cast(Strictness, getattr(self.decision_support_strictness, question_class))


@lru_cache
def load_assistant_policy() -> AssistantPolicy:
    path = files("lyme_gap_atlas_api").joinpath("assistant-policy-v1.json")
    return AssistantPolicy.model_validate(json.loads(path.read_text(encoding="utf-8")))


def classify_question(message: str) -> QuestionClass:
    """Conservative routing for prompt strictness, never a safety override."""
    text = message.casefold()
    if any(word in text for word in ("compare", "conflict", "disagree", "studies")):
        return "study_comparison"
    if any(word in text for word in ("diagnos", "treat", "intervention", "outcome")):
        return "diagnostics_interventions_outcomes"
    if any(word in text for word in ("atlas", "applicab", "county", "local decision")):
        return "atlas_applicability"
    if any(word in text for word in ("climate", "environment", "exposure", "habitat")):
        return "environment_exposure"
    if any(word in text for word in ("vector", "tick", "host", "pathogen", "borrelia")):
        return "vector_host_pathogen"
    return "surveillance_epidemiology"
