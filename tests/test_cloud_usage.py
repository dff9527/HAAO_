from orchestrator.cloud_usage import CloudUsage, apply_usage_to_requirement, estimate_cost_usd
from orchestrator.models.requirement import Requirement


def test_estimate_cost_usd_rounds() -> None:
    assert estimate_cost_usd(1_000_000, 0) == 3.0
    assert estimate_cost_usd(0, 1_000_000) == 15.0


def test_apply_usage_to_requirement_accumulates() -> None:
    requirement = Requirement(
        id="R-001",
        prompt="build feature",
        cloud_input_tokens=100,
        cloud_output_tokens=50,
        cloud_cost_usd=0.01,
    )
    updated = apply_usage_to_requirement(requirement, CloudUsage(input_tokens=200, output_tokens=100))
    assert updated.cloud_input_tokens == 300
    assert updated.cloud_output_tokens == 150
    assert updated.cloud_cost_usd == round(0.01 + CloudUsage(200, 100).cost_usd, 4)
