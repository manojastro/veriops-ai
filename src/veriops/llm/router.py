"""Model routing: cheap models for simple tasks, strong models for root-cause reasoning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from veriops.llm.schemas import LLMTask
from veriops.shared.config import LLMProvider, Settings


class ModelTier(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


TASK_TIERS: dict[LLMTask, ModelTier] = {
    LLMTask.CLASSIFY_INCIDENT: ModelTier.SMALL,
    LLMTask.SUMMARIZE_EVIDENCE: ModelTier.SMALL,
    LLMTask.GENERATE_HYPOTHESES: ModelTier.LARGE,
    LLMTask.TEST_HYPOTHESIS: ModelTier.LARGE,
    LLMTask.PROPOSE_REMEDIATION: ModelTier.LARGE,
    LLMTask.WRITE_REPORT: ModelTier.MEDIUM,
}

_ANTHROPIC_DEFAULTS = {
    ModelTier.SMALL: "claude-haiku-4-5-20251001",
    ModelTier.MEDIUM: "claude-sonnet-5-5",
    ModelTier.LARGE: "claude-opus-5-5",
}


@dataclass(frozen=True)
class Route:
    task: LLMTask
    tier: ModelTier
    model: str


class ModelRouter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def model_for_tier(self, tier: ModelTier) -> str:
        s = self.settings
        if s.llm_provider == LLMProvider.AZURE_OPENAI:
            # Azure addresses models by deployment name.
            small = s.azure_openai_deployment_small or s.azure_openai_deployment or s.llm_model_small
            large = s.azure_openai_deployment or s.llm_model_large
            return small if tier == ModelTier.SMALL else large
        configured = {
            ModelTier.SMALL: s.llm_model_small,
            ModelTier.MEDIUM: s.llm_model_medium,
            ModelTier.LARGE: s.llm_model_large,
        }[tier]
        if s.llm_provider == LLMProvider.ANTHROPIC and configured.startswith("gpt-"):
            return _ANTHROPIC_DEFAULTS[tier]
        return configured

    def route(self, task: LLMTask) -> Route:
        tier = TASK_TIERS[task]
        return Route(task=task, tier=tier, model=self.model_for_tier(tier))

    def table(self) -> list[dict[str, str]]:
        return [
            {"task": t.value, "tier": tier.value, "model": self.model_for_tier(tier)} for t, tier in TASK_TIERS.items()
        ]
