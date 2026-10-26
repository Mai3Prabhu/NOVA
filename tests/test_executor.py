import time

import pytest

from app.executor import ExecutionError, execute_plan
from app.models import AgentPlan, PlanTask
from app.tools import ToolError

USER_REQUEST = "Create a project proposal for an AI customer support chatbot."


def _plan(tasks: list[PlanTask]) -> AgentPlan:
    # model_construct skips validation so tests can exercise the executor's
    # own defenses (e.g. an unsupported tool) independently of AgentPlan's.
    return AgentPlan.model_construct(
        doc_type="project_proposal",
        assumptions=["Audience is internal stakeholders."],
        open_questions=[],
        tasks=tasks,
    )


def test_tasks_execute_in_the_order_given(monkeypatch):
    call_order: list[str] = []

    def spy(name):
        def fn(task, context):
            call_order.append(name)
            return {"ok": True}

        return fn

    monkeypatch.setattr(
        "app.executor.TOOL_REGISTRY",
        {
            "gather_context": spy("gather_context"),
            "write_section": spy("write_section"),
            "make_table": spy("make_table"),
            "build_docx": spy("build_docx"),
        },
    )

    plan = _plan(
        [
            PlanTask(task_id="t1", description="d", tool_name="gather_context", args={}),
            PlanTask(task_id="t2", description="d", tool_name="write_section", args={}),
            PlanTask(task_id="t3", description="d", tool_name="make_table", args={}),
            PlanTask(task_id="t4", description="d", tool_name="build_docx", args={}),
        ]
    )

    context = execute_plan(USER_REQUEST, plan)

    # gather_context and build_docx are ordering barriers (first and last);
    # write_section/make_table run concurrently between them, so their
    # relative start order isn't guaranteed - only that both ran, between
    # the barriers.
    assert call_order[0] == "gather_context"
    assert call_order[-1] == "build_docx"
    assert set(call_order[1:-1]) == {"write_section", "make_table"}
    assert [entry["task_id"] for entry in context["execution_log"]] == ["t1", "t2", "t3", "t4"]


def test_independent_content_tasks_run_concurrently_not_sequentially(monkeypatch):
    def slow_write(task, context):
        time.sleep(0.3)
        return {"section": "s", "content": "c"}

    def slow_table(task, context):
        time.sleep(0.3)
        return {"table_name": "t", "headers": ["h"], "rows": []}

    monkeypatch.setattr(
        "app.executor.TOOL_REGISTRY",
        {
            "write_section": slow_write,
            "make_table": slow_table,
            "build_docx": lambda task, context: {"status": "recorded"},
        },
    )

    plan = _plan(
        [
            PlanTask(task_id="t1", description="d", tool_name="write_section", args={}),
            PlanTask(task_id="t2", description="d", tool_name="make_table", args={}),
            PlanTask(task_id="t3", description="d", tool_name="build_docx", args={}),
        ]
    )

    start = time.monotonic()
    execute_plan(USER_REQUEST, plan)
    elapsed = time.monotonic() - start

    # Sequential would take >= 0.6s (two 0.3s calls back to back); running
    # concurrently should take close to a single 0.3s call.
    assert elapsed < 0.5


def test_build_docx_task_is_reached_and_recorded(monkeypatch):
    monkeypatch.setattr(
        "app.executor.TOOL_REGISTRY",
        {"build_docx": lambda task, context: {"status": "recorded"}},
    )

    plan = _plan([PlanTask(task_id="t1", description="d", tool_name="build_docx", args={})])

    context = execute_plan(USER_REQUEST, plan)

    assert context["execution_log"][-1]["tool_name"] == "build_docx"
    assert context["execution_log"][-1]["status"] == "success"


def test_unsupported_tool_is_never_executed():
    # Bypass AgentPlan/PlanTask validation to simulate a name that slipped
    # past the schema; the executor must refuse it rather than call it.
    bad_task = PlanTask.model_construct(
        task_id="t1", description="d", tool_name="delete_everything", args={}
    )
    plan = _plan([bad_task])

    with pytest.raises(ExecutionError) as exc_info:
        execute_plan(USER_REQUEST, plan)

    assert exc_info.value.execution_log[-1]["status"] == "failed"
    assert exc_info.value.execution_log[-1]["tool_name"] == "delete_everything"


def test_tool_failure_is_recorded_accurately_not_as_success(monkeypatch):
    def failing_tool(task, context):
        raise ToolError("simulated failure")

    monkeypatch.setattr(
        "app.executor.TOOL_REGISTRY",
        {
            "gather_context": lambda task, context: {"ok": True},
            "write_section": failing_tool,
            "build_docx": lambda task, context: {"status": "recorded"},
        },
    )

    plan = _plan(
        [
            PlanTask(task_id="t1", description="d", tool_name="gather_context", args={}),
            PlanTask(task_id="t2", description="d", tool_name="write_section", args={}),
            PlanTask(task_id="t3", description="d", tool_name="build_docx", args={}),
        ]
    )

    with pytest.raises(ExecutionError) as exc_info:
        execute_plan(USER_REQUEST, plan)

    log = exc_info.value.execution_log
    assert log[0]["status"] == "success"
    assert log[1]["task_id"] == "t2"
    assert log[1]["status"] == "failed"
    assert "simulated failure" in log[1]["summary"]
    # build_docx (t3) must never run after an earlier task failed.
    assert len(log) == 2


def test_one_failure_in_a_parallel_batch_still_records_the_other_accurately(monkeypatch):
    def failing_write(task, context):
        raise ToolError("simulated failure")

    def ok_table(task, context):
        return {"table_name": "t", "headers": ["h"], "rows": []}

    def build_docx_must_not_run(task, context):
        raise AssertionError("build_docx must never run after a batch failure")

    monkeypatch.setattr(
        "app.executor.TOOL_REGISTRY",
        {
            "write_section": failing_write,
            "make_table": ok_table,
            "build_docx": build_docx_must_not_run,
        },
    )

    plan = _plan(
        [
            PlanTask(task_id="t1", description="d", tool_name="write_section", args={}),
            PlanTask(task_id="t2", description="d", tool_name="make_table", args={}),
            PlanTask(task_id="t3", description="d", tool_name="build_docx", args={}),
        ]
    )

    with pytest.raises(ExecutionError) as exc_info:
        execute_plan(USER_REQUEST, plan)

    log = {entry["task_id"]: entry for entry in exc_info.value.execution_log}
    assert log["t1"]["status"] == "failed"
    assert log["t2"]["status"] == "success"  # the other concurrent task is still recorded accurately
    assert "t3" not in log  # build_docx (the next batch) never ran
