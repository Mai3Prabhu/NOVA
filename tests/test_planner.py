import pytest

from app.llm import LLMError, SchemaValidationError
from app.models import AgentPlan
from app.planner import PlanningError, create_plan

VALID_PLAN = {
    "doc_type": "project_proposal",
    "assumptions": ["Audience is internal stakeholders."],
    "open_questions": ["What is the budget?"],
    "tasks": [
        {
            "task_id": "task_1",
            "description": "Gather context about the chatbot project.",
            "tool_name": "gather_context",
            "args": {"topic": "AI customer support chatbot"},
        },
        {
            "task_id": "task_2",
            "description": "Write the proposal body.",
            "tool_name": "write_section",
            "args": {"section": "overview"},
        },
        {
            "task_id": "task_3",
            "description": "Build the final Word document.",
            "tool_name": "build_docx",
            "args": {},
        },
    ],
}

INVALID_PLAN_RAW = {
    "doc_type": "project_proposal",
    "assumptions": [],
    "open_questions": [],
    "tasks": [
        {
            "task_id": "task_1",
            "description": "Build the document too early.",
            "tool_name": "build_docx",
            "args": {},
        },
        {
            "task_id": "task_2",
            "description": "Write a section after build_docx (invalid).",
            "tool_name": "write_section",
            "args": {},
        },
    ],
}


def test_create_plan_returns_agent_plan_on_valid_first_attempt(monkeypatch):
    calls = []

    def fake_request_structured_json(system_prompt, user_prompt, response_model):
        calls.append(user_prompt)
        assert response_model is AgentPlan
        return AgentPlan.model_validate(VALID_PLAN)

    monkeypatch.setattr(
        "app.planner.request_structured_json", fake_request_structured_json
    )

    plan = create_plan("Create a project proposal for an AI customer support chatbot.")

    assert isinstance(plan, AgentPlan)
    assert plan.tasks[-1].tool_name == "build_docx"
    assert len(calls) == 1  # no correction attempt needed


def test_create_plan_corrects_invalid_plan_on_second_attempt(monkeypatch):
    calls = []

    def fake_request_structured_json(system_prompt, user_prompt, response_model):
        calls.append(user_prompt)
        if len(calls) == 1:
            raise SchemaValidationError(
                "the final task must use the build_docx tool", INVALID_PLAN_RAW
            )
        return AgentPlan.model_validate(VALID_PLAN)

    monkeypatch.setattr(
        "app.planner.request_structured_json", fake_request_structured_json
    )

    plan = create_plan("Create a project proposal.")

    assert len(calls) == 2
    assert plan.tasks[-1].tool_name == "build_docx"
    # The correction prompt should include the prior error for the LLM to fix.
    assert "build_docx" in calls[1]


def test_create_plan_raises_planning_error_when_still_invalid_after_correction(monkeypatch):
    calls = []

    def fake_request_structured_json(system_prompt, user_prompt, response_model):
        calls.append(user_prompt)
        raise SchemaValidationError(
            "the final task must use the build_docx tool", INVALID_PLAN_RAW
        )

    monkeypatch.setattr(
        "app.planner.request_structured_json", fake_request_structured_json
    )

    with pytest.raises(PlanningError):
        create_plan("Create a project proposal.")

    assert len(calls) == 2  # exactly one correction attempt, no retry loop


def test_create_plan_does_not_retry_non_validation_llm_errors(monkeypatch):
    calls = []

    def fake_request_structured_json(system_prompt, user_prompt, response_model):
        calls.append(user_prompt)
        raise LLMError("Groq API request failed: 401 unauthorized")

    monkeypatch.setattr(
        "app.planner.request_structured_json", fake_request_structured_json
    )

    with pytest.raises(PlanningError):
        create_plan("Create a project proposal.")

    assert len(calls) == 1  # auth/API failures are not retried


# --- A table is always present -------------------------------------------------


def _plan_without_table(extra_tasks: int = 0) -> AgentPlan:
    tasks = [
        {"task_id": f"task_{i}", "description": f"d{i}", "tool_name": "write_section",
         "args": {"section": f"s{i}"}}
        for i in range(1, extra_tasks + 2)
    ]
    tasks.append(
        {"task_id": "task_build", "description": "build", "tool_name": "build_docx", "args": {}}
    )
    return AgentPlan.model_validate(
        {"doc_type": "report", "assumptions": [], "open_questions": [], "tasks": tasks}
    )


def test_plan_without_a_table_gets_one_added_before_build_docx(monkeypatch):
    calls = []

    def fake(system_prompt, user_prompt, response_model):
        calls.append(1)
        return _plan_without_table(extra_tasks=2)

    monkeypatch.setattr("app.planner.request_structured_json", fake)

    plan = create_plan("Create a report.")

    tools = [t.tool_name for t in plan.tasks]
    assert tools.count("make_table") == 1
    assert tools[-1] == "build_docx"
    assert tools[-2] == "make_table"  # right before the build step
    assert len({t.task_id for t in plan.tasks}) == len(plan.tasks)  # unique ids
    assert plan.tasks[-2].args["table_name"]
    assert len(calls) == 1  # added in Python, no extra LLM call


def test_plan_that_already_has_a_table_is_left_alone(monkeypatch):
    original = AgentPlan.model_validate(
        {
            "doc_type": "report",
            "assumptions": [],
            "open_questions": [],
            "tasks": [
                {"task_id": "task_1", "description": "t", "tool_name": "make_table",
                 "args": {"table_name": "Risks"}},
                {"task_id": "task_2", "description": "b", "tool_name": "build_docx", "args": {}},
            ],
        }
    )
    monkeypatch.setattr("app.planner.request_structured_json", lambda s, u, m: original)

    plan = create_plan("Create a report.")

    assert [t.task_id for t in plan.tasks] == ["task_1", "task_2"]


def test_full_plan_at_the_task_cap_is_not_pushed_over_it(monkeypatch):
    full = _plan_without_table(extra_tasks=6)  # 7 sections + build = 8 tasks
    assert len(full.tasks) == 8
    monkeypatch.setattr("app.planner.request_structured_json", lambda s, u, m: full)

    plan = create_plan("Create a report.")

    assert len(plan.tasks) == 8  # still a valid plan; no room for a table


def test_planner_prompt_requires_a_table_and_named_args():
    from app.planner import SYSTEM_PROMPT

    assert 'at least one "make_table"' in SYSTEM_PROMPT
    assert '"section"' in SYSTEM_PROMPT and '"table_name"' in SYSTEM_PROMPT


def test_repeated_gather_context_tasks_are_dropped_keeping_the_first(monkeypatch):
    raw = {
        "doc_type": "report",
        "assumptions": [],
        "open_questions": [],
        "tasks": [
            {"task_id": "task_1", "description": "g", "tool_name": "gather_context", "args": {}},
            {"task_id": "task_2", "description": "g", "tool_name": "gather_context", "args": {}},
            {"task_id": "task_3", "description": "w", "tool_name": "write_section", "args": {"section": "A"}},
            {"task_id": "task_4", "description": "t", "tool_name": "make_table", "args": {"table_name": "T"}},
            {"task_id": "task_5", "description": "b", "tool_name": "build_docx", "args": {}},
        ],
    }
    monkeypatch.setattr(
        "app.planner.request_structured_json", lambda s, u, m: AgentPlan.model_validate(raw)
    )

    plan = create_plan("Create a report.")

    assert [t.task_id for t in plan.tasks] == ["task_1", "task_3", "task_4", "task_5"]


def test_planner_prompt_limits_context_and_requires_a_task_per_requested_item():
    from app.planner import SYSTEM_PROMPT

    assert 'at most ONE "gather_context"' in SYSTEM_PROMPT
    assert "own \"write_section\" task" in SYSTEM_PROMPT
