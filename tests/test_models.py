import pytest
from pydantic import ValidationError

from app.models import AgentPlan, PlanTask


def _task(tool_name: str, task_id: str = "task_1") -> dict:
    return {
        "task_id": task_id,
        "description": "do something",
        "tool_name": tool_name,
        "args": {},
    }


def test_valid_plan_passes_validation():
    plan = AgentPlan(
        doc_type="project_proposal",
        assumptions=["Audience is internal stakeholders."],
        open_questions=["What is the budget?"],
        tasks=[
            _task("gather_context"),
            _task("write_section", "task_2"),
            _task("build_docx", "task_3"),
        ],
    )
    assert plan.tasks[-1].tool_name == "build_docx"


def test_unsupported_tool_name_is_rejected():
    with pytest.raises(ValidationError):
        PlanTask(
            task_id="task_1",
            description="do something unsupported",
            tool_name="delete_everything",
            args={},
        )


def test_plan_with_too_many_tasks_is_rejected():
    tasks = [_task("write_section", f"task_{i}") for i in range(8)]
    tasks.append(_task("build_docx", "task_9"))  # 9 tasks total, max is 8
    with pytest.raises(ValidationError):
        AgentPlan(
            doc_type="report",
            assumptions=[],
            open_questions=[],
            tasks=tasks,
        )


def test_plan_must_end_with_build_docx():
    with pytest.raises(ValidationError):
        AgentPlan(
            doc_type="report",
            assumptions=[],
            open_questions=[],
            tasks=[_task("gather_context")],
        )
