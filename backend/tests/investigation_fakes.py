"""Offline AIProvider for agent tests. Never calls Gemini.

Answers each structured request with the canned result for its response model, so one fake can
serve every agent (InvestigationAnalysis, RootCauseAnalysis, RemediationProposal,
VerificationExplanation).
"""

import asyncio
from typing import Any

from pydantic import BaseModel, ValidationError

from app.ai.base import AIProvider, AIResponse, GenerationOptions, ModelT
from app.ai.errors import AIStructuredOutputError

# Evidence ids for the Phase 3 scenario (see docs/agent-design.md):
# L2 = v1.8.2 started, L3 = config reload, L5 = "Database connection failed", L7 = pool exhausted,
# L8 = POST /payment 500, L9/L10 = error-rate/latency alerts, H1 = payment-api DEGRADED,
# H2 = last healthy baseline, H3/H4 = healthy dependencies, D1 = v1.8.2.
GOOD_ANALYSIS: dict[str, Any] = {
    "summary": "payment-api degraded (37% errors, 2800 ms) minutes after v1.8.2; DB errors seen.",
    "findings": [
        {"kind": "observation", "statement": "payment-api is DEGRADED.", "evidence_ids": ["H1"]},
        {
            "kind": "observation",
            "statement": "Database connection failures precede the HTTP 500s.",
            "evidence_ids": ["L5", "L8"],
        },
        {
            "kind": "hypothesis",
            "statement": "v1.8.2 may have changed database connectivity.",
            "evidence_ids": ["D1", "L3", "L5"],
        },
    ],
    "confidence": 0.8,
    "next_step": "root_cause_analysis",
}

GOOD_RCA: dict[str, Any] = {
    "root_cause": "Deployment v1.8.2 changed payment-api's database host/pool configuration, so "
    "payment-api can no longer connect to the (healthy) database.",
    "category": "configuration_error",
    "confidence": 0.87,
    "supporting_evidence": ["D1", "L3", "L5", "L7", "H3"],
    "causal_chain": [
        {"statement": "v1.8.2 deployment started.", "evidence_ids": ["L2", "D1"]},
        {
            "statement": "Config reloaded: database.host, database.pool_size.",
            "evidence_ids": ["L3"],
        },
        {"statement": "Database connection failures began.", "evidence_ids": ["L5", "L7"]},
        {"statement": "POST /payment returned 500s.", "evidence_ids": ["L8"]},
        {"statement": "Error rate 37%, latency 2800 ms: DEGRADED.", "evidence_ids": ["H1"]},
    ],
    "contributing_factors": [
        {"statement": "Connection pool exhausted (0/20 available).", "evidence_ids": ["L7"]}
    ],
    "alternative_explanations": [
        {
            "explanation": "Database infrastructure failure",
            "assessment": "ruled_out",
            "reason": "The database service is healthy.",
            "evidence_ids": ["H3"],
        },
        {
            "explanation": "Resource exhaustion",
            "assessment": "ruled_out",
            "reason": "CPU and memory are unchanged from the healthy baseline.",
            "evidence_ids": ["H1", "H2"],
        },
    ],
    "missing_evidence": ["The actual configuration diff of v1.8.2"],
    "reasoning_summary": "Errors start 60 s after v1.8.2 reloads database settings while the "
    "database itself stays healthy and resources are unchanged.",
    "recommended_next_step": "propose_remediation",
}

GOOD_REMEDIATION: dict[str, Any] = {
    "action": "ROLLBACK_DEPLOYMENT",
    "target_service": "payment-api",
    "rollback_to_version": "v1.8.1",
    "reason": "v1.8.2 introduced the database configuration change; v1.8.1 was healthy.",
    "supporting_evidence": ["D1", "L3", "L5", "H3"],
    "confidence": 0.85,
}

GOOD_VERIFICATION: dict[str, Any] = {
    "reasoning_summary": "After the approved rollback to v1.8.1 the error rate fell from 37% to "
    "0.8% and latency from 2800 ms to 180 ms; payment-api is HEALTHY.",
    "supporting_evidence": ["V1", "V2", "V3", "V4"],
}


class FakeProvider(AIProvider):
    name = "fake"
    model = "fake-model"

    def __init__(self, result: dict[str, Any] | None = None, error: Exception | None = None):
        self.results: dict[str, dict[str, Any]] = {
            "InvestigationAnalysis": GOOD_ANALYSIS if result is None else result,
            "RootCauseAnalysis": GOOD_RCA,
            "RemediationProposal": GOOD_REMEDIATION,
            "VerificationExplanation": GOOD_VERIFICATION,
        }
        self.error = error
        self.delay = 0.0
        self.calls: list[tuple[str, type[BaseModel], GenerationOptions | None]] = []

    @property
    def result(self) -> dict[str, Any]:
        """The investigation result (kept for the Phase 5 tests)."""
        return self.results["InvestigationAnalysis"]

    @result.setter
    def result(self, value: dict[str, Any]) -> None:
        self.results["InvestigationAnalysis"] = value

    def calls_for(self, model: type[BaseModel]) -> list[str]:
        return [prompt for prompt, response_model, _ in self.calls if response_model is model]

    async def generate(
        self, prompt: str, *, options: GenerationOptions | None = None
    ) -> AIResponse:
        raise AssertionError("agents must only use generate_structured")

    async def generate_structured(
        self,
        prompt: str,
        response_model: type[ModelT],
        *,
        options: GenerationOptions | None = None,
    ) -> ModelT:
        self.calls.append((prompt, response_model, options))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        try:
            return response_model.model_validate(self.results[response_model.__name__])
        except ValidationError:  # same contract as GeminiProvider: never return invalid data
            raise AIStructuredOutputError(
                f"output did not match {response_model.__name__}"
            ) from None
