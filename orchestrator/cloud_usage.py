from __future__ import annotations

from dataclasses import dataclass

# Approximate Sonnet-class pricing for transparency (USD per million tokens).
INPUT_COST_PER_MTOK = 3.0
OUTPUT_COST_PER_MTOK = 15.0


@dataclass(frozen=True)
class CloudUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        return estimate_cost_usd(self.input_tokens, self.output_tokens)

    def add(self, other: CloudUsage) -> CloudUsage:
        return CloudUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return round(
        (input_tokens / 1_000_000) * INPUT_COST_PER_MTOK
        + (output_tokens / 1_000_000) * OUTPUT_COST_PER_MTOK,
        4,
    )


def apply_usage_to_requirement(requirement, usage: CloudUsage):
    from orchestrator.models.requirement import Requirement

    if not isinstance(requirement, Requirement):
        raise TypeError("requirement must be a Requirement instance")
    return requirement.model_copy(
        update={
            "cloud_input_tokens": requirement.cloud_input_tokens + usage.input_tokens,
            "cloud_output_tokens": requirement.cloud_output_tokens + usage.output_tokens,
            "cloud_cost_usd": round(requirement.cloud_cost_usd + usage.cost_usd, 4),
        }
    )


def usage_from_api_payload(payload: dict) -> CloudUsage:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return CloudUsage()
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    return CloudUsage(input_tokens=input_tokens, output_tokens=output_tokens)
