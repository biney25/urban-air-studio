"""Bounded study assistant contracts; model output is never permission authority."""
from typing import Annotated, Literal
from pydantic import Field, StrictBool
from api.schemas import Identifier, Input, Operation
from api.responses import Output


class AssistantMessage(Input):
    message: Annotated[str, Field(min_length=1, max_length=2000)]
    scenario_id: Identifier | None = None
    baseline_run_id: Identifier | None = None
    alternative_run_id: Identifier | None = None
    session_id: Identifier | None = None


class AssistantModel(Output):
    provider: Literal["ollama", "gemini"] = "ollama"
    model_id: str


class AssistantStatus(Output):
    available: bool
    state: Literal["ready", "unavailable", "busy"]
    label: Literal["Study assistant"] = "Study assistant"
    model: AssistantModel
    limits: dict
    reason_code: str | None = None
    message: str
    qualified: Literal[False] = False
    evaluation: dict | None = None


class AssistantEvidence(Output):
    kind: Literal["project", "scenario", "run", "comparison", "proposal", "evidence"]
    id: str
    label: str
    details: dict | None = None


class AssistantToolCall(Output):
    name: str
    status: Literal["complete", "blocked", "failed"]
    evidence_ids: list[str]


class AssistantValidation(Output):
    valid: StrictBool
    issues: list[dict]
    geometry_valid: bool
    programme_status: str


class AssistantProposal(Output):
    scenario_id: str
    expected_version: int
    operations: list[Operation]
    validation: AssistantValidation
    requires_confirmation: Literal[True] = True
    input_hash: str


class AssistantReply(Output):
    id: str
    session_id: str
    status: Literal["complete", "blocked", "unavailable"]
    answer: str
    evidence: list[AssistantEvidence]
    tool_calls: list[AssistantToolCall]
    proposal: AssistantProposal | None = None
    limitations: list[str]
    qualified: Literal[False] = False
    model: AssistantModel
    elapsed_seconds: float
    content_hash: str


class AssistantHistory(Output):
    id: str
    project_id: str
    messages: list[dict]
