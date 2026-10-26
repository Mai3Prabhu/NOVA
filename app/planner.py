"""Task planning: turns a natural-language request into an ordered task plan."""

import json

from app.llm import LLMError, SchemaValidationError, request_structured_json
from app.models import PERMITTED_TOOLS, AgentPlan, PlanTask

MAX_PLAN_TASKS = 8

SYSTEM_PROMPT = f"""You are the planning module of an autonomous document-writing agent.

Given a user's request for a document, produce a JSON execution plan with this \
exact schema:
{{
  "doc_type": string (e.g. "project_proposal", "report", "meeting_minutes"),
  "assumptions": list of strings - reasonable assumptions you made about missing details,
  "open_questions": list of strings - details you could not reasonably assume and \
would need the user to clarify,
  "tasks": list of 1 to 8 task objects, each:
    {{
      "task_id": short string identifier, e.g. "task_1",
      "description": short human-readable description of the step,
      "tool_name": one of {list(PERMITTED_TOOLS)},
      "args": object of arguments for the tool
    }}
}}

Rules:
- Only use tool names from this exact list: {list(PERMITTED_TOOLS)}.
- The plan must be specific to the user's request, not a generic template.
- Always set these "args" so the document gets proper headings: for "write_section" use {{"section": "<short heading, e.g. Objectives>"}}; for "make_table" use {{"table_name": "<short title, e.g. Implementation Timeline>"}}; for "gather_context" use {{"topic": "<topic>"}}. Headings are titles, not instructions - never start one with "Write" or "Create".
- Include at least one "make_table" task. Use tables for anything naturally tabular in the request - timelines/schedules, milestones, responsibilities, risks, resources, budgets, success metrics.- Use at most ONE "gather_context" task, as the first task. Do not repeat it.
- Every item the user asks the document to include (e.g. objectives, risks, responsibilities, success metrics) must get its own "write_section" task, or a "make_table" task if it is naturally tabular. Do not drop a requested item, and do not use a single task to cover several of them.
- Keep the plan within 8 tasks by not splitting one topic across several tasks.
- The "tasks" list must contain EXACTLY ONE "build_docx" task, and it MUST be \
the final element of the list. No task may come after it, and "build_docx" must \
not appear anywhere earlier in the list.
- Never invent specific company facts you were not given - budgets, named \
integrations or vendors, metrics, deadlines, dates, or policies. If the request \
is missing a detail like this, either record a clearly-labeled assumption (see \
below) or, if you cannot reasonably guess, add an open question instead.
- Every entry in "assumptions" must read as an assumption, not as a stated fact \
- e.g. "Assuming a mid-size team of roughly 20-50 people (not specified by the \
user)" rather than "The team has 35 people."
- If the request contains conflicting or ambiguous constraints (e.g. contradictory \
deadlines or scope), do not silently resolve them in one direction - record the \
conflict as an open question instead.
- Prefer recording an open question over inventing specific facts you were not given.
- Respond with JSON only, no extra commentary.
"""

CORRECTION_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + "\nYou will be shown your previous JSON response and the validation error "
    "it caused. Return a corrected JSON plan that fixes that specific problem "
    "and still satisfies every rule above. Respond with JSON only, no extra "
    "commentary."
)


class PlanningError(Exception):
    """Raised when a valid plan could not be produced for a request."""


def create_plan(user_request: str) -> AgentPlan:
    try:
        plan = request_structured_json(SYSTEM_PROMPT, user_request, AgentPlan)
    except SchemaValidationError as exc:
        plan = _retry_with_correction(user_request, exc)
    except LLMError as exc:
        # Non-validation failure (auth, network, malformed JSON, ...): fail
        # immediately rather than spending a second call on it.
        raise PlanningError(f"Could not generate a valid plan: {exc}") from exc
    return _ensure_table(_drop_repeated_context_tasks(plan))


def _drop_repeated_context_tasks(plan: AgentPlan) -> AgentPlan:
    """Keep only the first gather_context task. It always returns the same
    mock context, so repeats add nothing and only crowd out the sections and
    tables the user asked for within the 8-task cap."""
    seen = False
    kept = []
    for task in plan.tasks:
        if task.tool_name == "gather_context":
            if seen:
                continue
            seen = True
        kept.append(task)
    if len(kept) == len(plan.tasks):
        return plan
    return plan.model_copy(update={"tasks": kept})


def _ensure_table(plan: AgentPlan) -> AgentPlan:
    """Guarantee the document has at least one table.

    The prompt asks for one, but an LLM can still omit it. Rather than spend
    another call on that, add a generic summary-table task just before
    build_docx (the table's cells are still written by make_table under the
    usual grounding rules). If the plan is already at the 8-task cap there is
    no room, so it is returned unchanged.
    """
    if any(task.tool_name == "make_table" for task in plan.tasks):
        return plan
    if len(plan.tasks) >= MAX_PLAN_TASKS:
        return plan

    used_ids = {task.task_id for task in plan.tasks}
    number = len(plan.tasks) + 1
    while f"task_{number}" in used_ids:
        number += 1
    table_task = PlanTask(
        task_id=f"task_{number}",
        description=(
            "Summarize the key items of this document in a table (for example "
            "item, owner, timing, status), using TBD where details are unknown."
        ),
        tool_name="make_table",
        args={"table_name": "Summary"},
    )
    return plan.model_copy(update={"tasks": [*plan.tasks[:-1], table_task, plan.tasks[-1]]})


def _retry_with_correction(user_request: str, error: SchemaValidationError) -> AgentPlan:
    """Make exactly one follow-up call asking the LLM to fix its own plan."""
    correction_prompt = (
        f"Original request: {user_request}\n\n"
        f"Your previous JSON response was:\n{json.dumps(error.raw_response)}\n\n"
        f"That response was invalid for this reason:\n{error}\n\n"
        "Return a corrected JSON plan."
    )
    try:
        return request_structured_json(CORRECTION_SYSTEM_PROMPT, correction_prompt, AgentPlan)
    except LLMError as exc:
        raise PlanningError(
            f"Could not generate a valid plan after one correction attempt: {exc}"
        ) from exc
