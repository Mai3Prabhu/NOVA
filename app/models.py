"""Pydantic request/response models for the agent API."""

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Tools the executor will support (implemented in a later step). The planner
# is restricted to only ever reference these names.
PERMITTED_TOOLS = ("gather_context", "write_section", "make_table", "build_docx")
ToolName = Literal["gather_context", "write_section", "make_table", "build_docx"]


class AgentRequest(BaseModel):
    """Incoming natural-language request for the agent to process."""

    request: str = Field(
        ...,
        min_length=1,
        max_length=6000,
        description=(
            "Natural-language description of what the agent should do. The "
            "higher bound (vs. a single request) accommodates the frontend's "
            "clarification/regeneration flow, which combines the original "
            "request, the user's clarification, and a fixed instructions "
            "block into one string sent through this same field."
        ),
    )

    @field_validator("request")
    @classmethod
    def request_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("request must not be blank")
        return stripped


class PlanTask(BaseModel):
    """A single step of the execution plan, bound to one permitted tool."""

    task_id: str
    description: str
    tool_name: ToolName
    args: dict[str, Any] = Field(default_factory=dict)


class AgentPlan(BaseModel):
    """A structured, validated plan produced by the LLM for one request."""

    # Output-token cap read by request_structured_json. A plan is a few
    # hundred tokens of JSON; the headroom is for reasoning models, which
    # spend this same budget on thinking before they emit the JSON.
    max_output_tokens: ClassVar[int] = 2500

    doc_type: str
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    tasks: list[PlanTask] = Field(..., min_length=1, max_length=8)

    @model_validator(mode="after")
    def last_task_must_build_docx(self) -> "AgentPlan":
        if self.tasks[-1].tool_name != "build_docx":
            raise ValueError("the final task must use the build_docx tool")
        return self


class ExecutionLogEntry(BaseModel):
    """One recorded step of running a plan - never marked successful on failure."""

    task_id: str
    tool_name: ToolName
    status: Literal["success", "failed"]
    summary: str


class AgentRunResult(BaseModel):
    """Full result of planning and then executing a user's request."""

    doc_type: str
    assumptions: list[str]
    open_questions: list[str]
    execution_log: list[ExecutionLogEntry]
    sections: list[dict[str, Any]]
    tables: list[dict[str, Any]]
    document_id: str
    download_url: str


class HealthResponse(BaseModel):
    status: str
