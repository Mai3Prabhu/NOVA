"""Predefined tools the executor can call to carry out a task plan.

Each tool has the fixed signature `(task: PlanTask, context: dict) -> dict`
and returns a small JSON-serializable result. The executor only ever calls
tools through TOOL_REGISTRY, so an LLM-supplied name can never reach
arbitrary Python code - `AgentPlan.tool_name` already restricts the name to
one of these four, and the registry lookup is a second, independent guard.

write_section and make_table also have batch variants (BATCH_TOOLS) that
serve several tasks of the same kind from a single LLM call.
"""

import json
from typing import Callable, ClassVar, TypeVar

from pydantic import BaseModel, Field, model_validator

from app import docx_builder
from app.llm import LLMError, SchemaValidationError, request_structured_json
from app.mock_data import MOCK_BUSINESS_CONTEXT
from app.models import PlanTask


class ToolError(Exception):
    """Raised when a tool cannot produce a usable result for its task."""


# Shared across write_section and make_table so both content-generation tools
# apply the same anti-fabrication and source-labeling rules.
GROUNDING_RULES = (
    "Ground everything in the three inputs you are given below, which are "
    "distinct and must not be blended together:\n"
    "1. The user's original request - authoritative, but may be incomplete.\n"
    "2. Simulated/mock business context - illustrative background only; it was "
    "NOT supplied by the user and must never be described as if it was.\n"
    "3. Plan assumptions - explicitly labeled guesses the planner made about "
    "missing details, not verified facts.\n\n"
    "Do not invent specific company facts that appear in none of the above - "
    "budgets, named integrations or vendors, metrics, dates/deadlines, or "
    "policies. If such a detail is needed but unavailable, either phrase it "
    "generically, label it clearly as an illustrative assumption (e.g. "
    '"an estimated budget, to be confirmed"), or note that it is unresolved - '
    "never state an invented number or fact as if it were verified."
)

# Output-token caps (read by request_structured_json). Sections are asked to
# be ~100-250 words (a few hundred tokens), so these leave headroom for
# reasoning models, which spend the same budget on thinking before the JSON.
# They are estimates, not measured limits - see the usage logging in llm.py.
SECTION_MAX_TOKENS = 1800
TABLE_MAX_TOKENS = 1500
# A combined call scales with how many items it carries, within a ceiling.
BATCH_OVERHEAD_TOKENS = 1000
BATCH_PER_SECTION_TOKENS = 900
BATCH_PER_TABLE_TOKENS = 700
BATCH_SECTIONS_CEILING = 6000
BATCH_TABLES_CEILING = 4000

SECTION_STYLE = "Each section must be at least 120 words (aim for 150-250): 2-3 short paragraphs of plain prose, with a short bullet list where it reads better."
TABLE_STYLE = "Keep each table compact: at most 8 rows."


class SectionContent(BaseModel):
    max_output_tokens: ClassVar[int] = SECTION_MAX_TOKENS

    content: str = Field(..., min_length=1)


class TableContent(BaseModel):
    max_output_tokens: ClassVar[int] = TABLE_MAX_TOKENS

    headers: list[str] = Field(..., min_length=1)
    rows: list[list[str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def rows_match_header_width(self) -> "TableContent":
        for row in self.rows:
            if len(row) != len(self.headers):
                raise ValueError("every row must have the same number of cells as headers")
        return self


class SectionItem(SectionContent):
    task_id: str


class SectionsBatch(BaseModel):
    sections: list[SectionItem] = Field(..., min_length=1)


class TableItem(TableContent):
    task_id: str


class TablesBatch(BaseModel):
    tables: list[TableItem] = Field(..., min_length=1)


class IncompleteOutputError(SchemaValidationError):
    """A combined response was valid JSON but omitted, duplicated or invented
    task ids, so it can't be mapped back onto the tasks that were asked for."""


T = TypeVar("T", bound=BaseModel)


def _request_with_one_correction(
    system_prompt: str,
    user_prompt: str,
    response_model: type[T],
    *,
    max_tokens: int | None = None,
    check: Callable[[T], None] | None = None,
) -> T:
    """Request structured JSON; if it is invalid, make exactly one follow-up
    call showing the model its own response and the problem. A failure of the
    follow-up propagates - there is no second correction."""
    kwargs = {} if max_tokens is None else {"max_tokens": max_tokens}

    def attempt(prompt: str) -> T:
        result = request_structured_json(system_prompt, prompt, response_model, **kwargs)
        if check is not None:
            check(result)
        return result

    try:
        return attempt(user_prompt)
    except SchemaValidationError as exc:
        correction_prompt = (
            f"{user_prompt}\n"
            f"Your previous JSON response was:\n{json.dumps(exc.raw_response)}\n"
            f"That response was invalid for this reason:\n{exc}\n"
            "Return corrected JSON that fixes that specific problem."
        )
    return attempt(correction_prompt)


def _expect_task_ids(expected: list[str], returned: list[str]) -> None:
    if sorted(returned) != sorted(expected):
        raise ValueError(
            f"expected exactly one entry for each of these task_ids {expected}, "
            f"got {returned}"
        )


def _shared_prompt(context: dict) -> str:
    """The request/assumptions/open-questions/mock-context block every
    content prompt starts with. Kept as separate labeled lines so the user's
    request, guesses and simulated context are never blended."""
    return (
        f"Original user request (authoritative, from the user): {context['request']}\n"
        f"Plan assumptions (labeled guesses, not facts): {context.get('assumptions', [])}\n"
        f"Open questions already flagged as unresolved - do not invent answers "
        f"to these: {context.get('open_questions', [])}\n"
        f"Simulated business context (illustrative only, NOT supplied by the "
        f"user): {context.get('gathered_context', {})}\n"
    )


def gather_context(task: PlanTask, context: dict) -> dict:
    """Look up mock business context. Never returns user-provided data."""
    topic = task.args.get("topic", context["request"])
    return {
        "topic": topic,
        "mock_context": MOCK_BUSINESS_CONTEXT,
        "source": "mock_data.py (simulated business context, not supplied by the user)",
    }


def write_section(task: PlanTask, context: dict) -> dict:
    """Draft one document section with the LLM, using the shared context."""
    section_name = task.args.get("section", task.description)
    system_prompt = (
        "You are the writing module of an autonomous document-writing agent. "
        "Write one section of a document in plain prose. "
        f"{SECTION_STYLE} "
        f"{GROUNDING_RULES} "
        'Respond with JSON only: {"content": string}.'
    )
    user_prompt = (
        _shared_prompt(context)
        + f"Task description: {task.description}\n"
        + f"Section to write: {section_name}\n"
    )
    try:
        result = _request_with_one_correction(system_prompt, user_prompt, SectionContent)
    except LLMError as exc:
        raise ToolError(f"write_section failed: {exc}") from exc
    return {"section": section_name, "content": result.content}


def make_table(task: PlanTask, context: dict) -> dict:
    """Draft a structured table with the LLM, validating headers/rows line up."""
    table_name = task.args.get("table_name", task.description)
    system_prompt = (
        "You are the table-generation module of an autonomous document-writing "
        "agent. Produce one table relevant to the request. "
        f"{TABLE_STYLE} "
        f"{GROUNDING_RULES} "
        'Respond with JSON only: {"headers": [string, ...], "rows": [[string, ...], '
        '...]}, where every row has exactly as many cells as there are headers. '
        "If a cell value is not known, write a generic placeholder (e.g. "
        '"TBD" or "to be confirmed") rather than inventing a specific figure.'
    )
    user_prompt = (
        _shared_prompt(context)
        + f"Task description: {task.description}\n"
        + f"Table purpose: {table_name}\n"
    )
    try:
        result = _request_with_one_correction(system_prompt, user_prompt, TableContent)
    except LLMError as exc:
        raise ToolError(f"make_table failed: {exc}") from exc
    return {"table_name": table_name, "headers": result.headers, "rows": result.rows}


def write_sections(tasks: list[PlanTask], context: dict) -> dict[str, dict]:
    """Draft several sections in ONE LLM call instead of one call each.

    The request, assumptions, open questions, mock context and grounding
    rules are sent once instead of once per section. Returns
    {task_id: <same dict write_section returns>}.
    """
    names = {t.task_id: t.args.get("section", t.description) for t in tasks}
    system_prompt = (
        "You are the writing module of an autonomous document-writing agent. "
        "Write the requested sections of a document, each in plain prose, "
        f"independently of one another. {SECTION_STYLE} "
        f"{GROUNDING_RULES} "
        'Respond with JSON only: {"sections": [{"task_id": string, "content": string}, ...]} '
        "with exactly one entry per requested task_id."
    )
    requested = "".join(
        f"- task_id: {t.task_id} | section: {names[t.task_id]} | description: {t.description}\n"
        for t in tasks
    )
    user_prompt = _shared_prompt(context) + f"Sections to write:\n{requested}"
    budget = min(
        BATCH_OVERHEAD_TOKENS + BATCH_PER_SECTION_TOKENS * len(tasks), BATCH_SECTIONS_CEILING
    )

    def check(result: SectionsBatch) -> None:
        try:
            _expect_task_ids(list(names), [item.task_id for item in result.sections])
        except ValueError as exc:
            raise IncompleteOutputError(str(exc), result.model_dump()) from exc

    try:
        result = _request_with_one_correction(
            system_prompt, user_prompt, SectionsBatch, max_tokens=budget, check=check
        )
    except LLMError as exc:
        raise ToolError(f"write_section failed: {exc}") from exc
    by_id = {item.task_id: item for item in result.sections}
    return {
        task_id: {"section": names[task_id], "content": by_id[task_id].content}
        for task_id in names
    }


def make_tables(tasks: list[PlanTask], context: dict) -> dict[str, dict]:
    """Draft several tables in ONE LLM call; the table counterpart of
    write_sections. Returns {task_id: <same dict make_table returns>}."""
    names = {t.task_id: t.args.get("table_name", t.description) for t in tasks}
    system_prompt = (
        "You are the table-generation module of an autonomous document-writing "
        "agent. Produce the requested tables, each relevant to the request. "
        f"{TABLE_STYLE} "
        f"{GROUNDING_RULES} "
        'Respond with JSON only: {"tables": [{"task_id": string, "headers": [string, ...], '
        '"rows": [[string, ...], ...]}, ...]} with exactly one entry per requested '
        "task_id, where every row has exactly as many cells as that table has headers. "
        "If a cell value is not known, write a generic placeholder (e.g. "
        '"TBD" or "to be confirmed") rather than inventing a specific figure.'
    )
    requested = "".join(
        f"- task_id: {t.task_id} | table purpose: {names[t.task_id]} | description: {t.description}\n"
        for t in tasks
    )
    user_prompt = _shared_prompt(context) + f"Tables to produce:\n{requested}"
    budget = min(
        BATCH_OVERHEAD_TOKENS + BATCH_PER_TABLE_TOKENS * len(tasks), BATCH_TABLES_CEILING
    )

    def check(result: TablesBatch) -> None:
        try:
            _expect_task_ids(list(names), [item.task_id for item in result.tables])
        except ValueError as exc:
            raise IncompleteOutputError(str(exc), result.model_dump()) from exc

    try:
        result = _request_with_one_correction(
            system_prompt, user_prompt, TablesBatch, max_tokens=budget, check=check
        )
    except LLMError as exc:
        raise ToolError(f"make_table failed: {exc}") from exc
    by_id = {item.task_id: item for item in result.tables}
    return {
        task_id: {
            "table_name": names[task_id],
            "headers": by_id[task_id].headers,
            "rows": by_id[task_id].rows,
        }
        for task_id in names
    }


def build_docx(task: PlanTask, context: dict) -> dict:
    """Render everything gathered so far into a .docx file on disk.

    Always the plan's final task (enforced by AgentPlan), so it only ever
    runs once every preceding task has already succeeded.
    """
    try:
        document_id, filename = docx_builder.build_docx(
            doc_type=context.get("doc_type", "document"),
            original_request=context["request"],
            assumptions=context.get("assumptions", []),
            open_questions=context.get("open_questions", []),
            sections=context.get("sections", []),
            tables=context.get("tables", []),
        )
    except OSError as exc:
        raise ToolError(f"build_docx failed: {exc}") from exc
    return {"document_id": document_id, "filename": filename}


TOOL_REGISTRY: dict[str, Callable[[PlanTask, dict], dict]] = {
    "gather_context": gather_context,
    "write_section": write_section,
    "make_table": make_table,
    "build_docx": build_docx,
}

# Tools that can serve several same-kind tasks from one LLM call. The
# executor uses these when a batch holds two or more tasks of the same tool;
# a lone task still goes through TOOL_REGISTRY.
BATCH_TOOLS: dict[str, Callable[[list[PlanTask], dict], dict[str, dict]]] = {
    "write_section": write_sections,
    "make_table": make_tables,
}
