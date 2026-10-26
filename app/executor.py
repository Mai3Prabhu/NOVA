"""Execution of an AgentPlan against the predefined tool registry.

Tasks run in the plan's order, but `write_section`/`make_table` tasks don't
depend on each other - only on `gathered_context`, which is already set
before they run - so consecutive runs of them execute concurrently instead
of one-by-one. This is the main source of latency for a multi-section
document: each one is a blocking LLM call, so N of them in series take N
times as long as the slowest one; running them concurrently collapses that
back down to roughly the slowest single call. Two or more sections (or
tables) in one batch are further combined into a single LLM call each (see
`BATCH_TOOLS`), so a long plan makes at most two concurrent calls, not one
per task. `gather_context` and
`build_docx` (and any other/unknown tool) remain ordering barriers: nothing
after one starts until everything before it has finished.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.models import AgentPlan, PlanTask
from app.tools import BATCH_TOOLS, TOOL_REGISTRY, ToolError

PARALLELIZABLE_TOOLS = {"write_section", "make_table"}


class ExecutionError(Exception):
    """Raised when a task fails or references a tool outside the registry.

    Carries the execution log recorded up to (and including) the failure,
    so the caller can see exactly how far the plan got.
    """

    def __init__(self, message: str, execution_log: list[dict]):
        super().__init__(message)
        self.execution_log = execution_log


def _summarize(tool_name: str, result: dict) -> str:
    if tool_name == "gather_context":
        return f"gathered mock context on '{result.get('topic')}'"
    if tool_name == "write_section":
        return f"wrote section '{result.get('section')}' ({len(result.get('content', ''))} chars)"
    if tool_name == "make_table":
        rows = result.get("rows", [])
        return f"built table '{result.get('table_name')}' with {len(rows)} row(s)"
    if tool_name == "build_docx":
        return f"built document '{result.get('filename')}'"
    return "completed"


def _group_into_batches(tasks: list[PlanTask]) -> list[list[PlanTask]]:
    """Group consecutive parallelizable tasks together; every other task
    (gather_context, build_docx, or anything unrecognized) is its own
    single-task batch, acting as a barrier before and after it."""
    batches: list[list[PlanTask]] = []
    current: list[PlanTask] = []
    for task in tasks:
        if task.tool_name in PARALLELIZABLE_TOOLS:
            current.append(task)
            continue
        if current:
            batches.append(current)
            current = []
        batches.append([task])
    if current:
        batches.append(current)
    return batches


def _run_task(task: PlanTask, context: dict) -> tuple[dict | None, str | None]:
    """Run one task. Returns (result, error_message) - exactly one is set."""
    tool = TOOL_REGISTRY.get(task.tool_name)
    if tool is None:
        return None, "unsupported tool - not in TOOL_REGISTRY"
    try:
        return tool(task, context), None
    except ToolError as exc:
        return None, str(exc)


def _run_group(tasks: list[PlanTask], context: dict) -> dict[str, tuple[dict | None, str | None]]:
    """Run same-tool tasks. Two or more of a batchable tool share ONE LLM
    call (so the shared prompt and grounding rules are sent once, and a
    document with many sections costs one rate-limit hit, not many); if
    that call fails, every task in the group is recorded as failed."""
    batch_tool = BATCH_TOOLS.get(tasks[0].tool_name)
    if len(tasks) < 2 or batch_tool is None:
        return {task.task_id: _run_task(task, context) for task in tasks}
    try:
        results = batch_tool(tasks, context)
    except ToolError as exc:
        return {task.task_id: (None, str(exc)) for task in tasks}
    return {task.task_id: (results[task.task_id], None) for task in tasks}


def _run_batch(batch: list[PlanTask], context: dict) -> dict[str, tuple[dict | None, str | None]]:
    if len(batch) == 1:
        task = batch[0]
        return {task.task_id: _run_task(task, context)}

    groups: dict[str, list[PlanTask]] = {}
    for task in batch:
        groups.setdefault(task.tool_name, []).append(task)

    # At most one group per parallelizable tool (sections, tables), so this
    # is at most two concurrent LLM calls however long the plan is.
    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        futures = [pool.submit(_run_group, tasks, context) for tasks in groups.values()]
        outcomes: dict[str, tuple[dict | None, str | None]] = {}
        for future in futures:
            outcomes.update(future.result())
        return outcomes


def execute_plan(user_request: str, plan: AgentPlan) -> dict[str, Any]:
    """Run `plan`'s tasks (batching independent content tasks), threading
    shared context through.

    Stops at the first failure - within a batch, every task in it still
    runs to completion (so its real outcome is recorded accurately), but no
    later batch starts - so a bad task is never silently reported as
    successful, and build_docx never runs after an earlier failure. The
    raised ExecutionError carries the log recorded so far.
    """
    context: dict[str, Any] = {
        "request": user_request,
        "doc_type": plan.doc_type,
        "assumptions": plan.assumptions,
        "open_questions": plan.open_questions,
        "gathered_context": {},
        "sections": [],
        "tables": [],
        "task_results": {},
        "execution_log": [],
    }

    for batch in _group_into_batches(plan.tasks):
        outcomes = _run_batch(batch, context)
        first_failure: tuple[PlanTask, str] | None = None

        for task in batch:
            result, error = outcomes[task.task_id]

            if error is not None:
                context["execution_log"].append(
                    {
                        "task_id": task.task_id,
                        "tool_name": task.tool_name,
                        "status": "failed",
                        "summary": error,
                    }
                )
                if first_failure is None:
                    first_failure = (task, error)
                continue

            context["task_results"][task.task_id] = result
            if task.tool_name == "gather_context":
                context["gathered_context"] = result
            elif task.tool_name == "write_section":
                context["sections"].append(result)
            elif task.tool_name == "make_table":
                context["tables"].append(result)

            context["execution_log"].append(
                {
                    "task_id": task.task_id,
                    "tool_name": task.tool_name,
                    "status": "success",
                    "summary": _summarize(task.tool_name, result),
                }
            )

        if first_failure is not None:
            task, error = first_failure
            raise ExecutionError(
                f"Task {task.task_id} ({task.tool_name}) failed: {error}",
                context["execution_log"],
            )

    return context
