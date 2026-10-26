"""Deterministic tests for groundedness safeguards.

These tests cannot prove an LLM will never hallucinate - that would require
real model calls, which these tests deliberately avoid. What they verify
instead is the part that *is* deterministic and testable: that the prompts
instruct the model not to fabricate facts, that the user's request, mock
context, and assumptions stay labeled and separate throughout the pipeline,
and that missing information flows through to open_questions/assumptions and
into the final document rather than being silently dropped or invented by
our own code.
"""

from app.executor import execute_plan
from app.models import AgentPlan, PlanTask
from app.planner import SYSTEM_PROMPT
from app.tools import GROUNDING_RULES, gather_context

USER_REQUEST = "Create a project proposal for an AI customer support chatbot."


def _plan(doc_type: str, assumptions: list[str], open_questions: list[str], tasks: list[dict]) -> AgentPlan:
    return AgentPlan.model_validate(
        {
            "doc_type": doc_type,
            "assumptions": assumptions,
            "open_questions": open_questions,
            "tasks": tasks,
        }
    )


# --- Prompt-level safeguards ------------------------------------------------


def test_planner_prompt_forbids_fabricating_specific_facts():
    lowered = SYSTEM_PROMPT.lower()
    assert "budgets" in lowered
    assert "never invent specific company facts" in lowered
    assert "assumption" in lowered


def test_planner_prompt_requires_flagging_conflicting_constraints():
    lowered = SYSTEM_PROMPT.lower()
    assert "conflicting" in lowered
    assert "open question" in lowered


def test_tool_grounding_rules_separate_request_context_and_assumptions():
    lowered = GROUNDING_RULES.lower()
    assert "not supplied by the user" in lowered
    assert "verified" in lowered
    assert "budgets" in lowered


# --- Mock context stays labeled, never merged into "user" data -------------


def test_gather_context_never_claims_to_be_user_supplied():
    task = PlanTask(
        task_id="t0", description="gather", tool_name="gather_context",
        args={"topic": "chatbot integrations"},
    )
    result = gather_context(task, {"request": USER_REQUEST})
    assert "not supplied by the user" in result["source"]
    assert "mock" in result["source"].lower() or "simulated" in result["source"].lower()


def test_write_section_prompt_keeps_request_and_mock_context_distinct(monkeypatch):
    from app.tools import write_section

    captured = {}

    def fake_request_structured_json(system_prompt, user_prompt, response_model):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        from app.tools import SectionContent

        return SectionContent(content="Integration needs are not yet specified by the user.")

    monkeypatch.setattr("app.tools.request_structured_json", fake_request_structured_json)

    context = {
        "request": USER_REQUEST,
        "assumptions": [],
        "open_questions": ["Which CRM or support platform should it integrate with?"],
        "gathered_context": gather_context(
            PlanTask(task_id="t0", description="gather", tool_name="gather_context", args={}),
            {"request": USER_REQUEST},
        ),
    }
    task = PlanTask(task_id="t1", description="Write the integrations section.", tool_name="write_section", args={"section": "integrations"})

    write_section(task, context)

    user_prompt = captured["user_prompt"]
    request_line = next(l for l in user_prompt.splitlines() if l.startswith("Original user request"))
    context_line = next(l for l in user_prompt.splitlines() if l.startswith("Simulated business context"))

    assert USER_REQUEST in request_line
    assert "not supplied by the user" in context_line
    assert "Zendesk" in context_line or "Shopify" in context_line  # mock data present, but labeled


# --- Scenario: missing budget information -----------------------------------


def test_missing_budget_flows_to_open_questions_not_invented_facts(monkeypatch, tmp_path):
    from app.tools import SectionContent

    monkeypatch.setattr("app.docx_builder.GENERATED_DIR", tmp_path)

    captured_prompts = []

    def fake_request_structured_json(system_prompt, user_prompt, response_model):
        captured_prompts.append(user_prompt)
        # A well-behaved model: no invented dollar figure, defers to the open question.
        return SectionContent(
            content="A detailed budget was not provided; see the open questions for this gap."
        )

    monkeypatch.setattr("app.tools.request_structured_json", fake_request_structured_json)

    plan = _plan(
        doc_type="project_proposal",
        assumptions=["Audience is internal stakeholders (not specified by the user)."],
        open_questions=["What is the budget for this project?"],
        tasks=[
            {
                "task_id": "t1",
                "description": "Write the budget overview section.",
                "tool_name": "write_section",
                "args": {"section": "budget_overview"},
            },
            {
                "task_id": "t2",
                "description": "Build the document.",
                "tool_name": "build_docx",
                "args": {},
            },
        ],
    )

    context = execute_plan(USER_REQUEST, plan)

    # The open question reached the LLM prompt, so a compliant model can see
    # the gap instead of having to guess a figure.
    assert any("budget" in p.lower() for p in captured_prompts)

    section_text = context["sections"][0]["content"]
    assert "$" not in section_text  # the mocked (well-behaved) output invents no figure

    # The gap is still visible in the final document's Open Questions section.
    document_id, filename = _build_docx_from_context(context, plan)
    doc_text = _read_docx_text(filename)
    assert "What is the budget for this project?" in doc_text


# --- Scenario: integrations not specified -----------------------------------


def test_missing_integrations_keeps_mock_tools_labeled_as_simulated(monkeypatch, tmp_path):
    from app.tools import TableContent

    monkeypatch.setattr("app.docx_builder.GENERATED_DIR", tmp_path)

    def fake_request_structured_json(system_prompt, user_prompt, response_model):
        assert "not supplied by the user" in user_prompt
        return TableContent(
            headers=["Integration", "Status"],
            rows=[["To be determined", "Not specified by the user"]],
        )

    monkeypatch.setattr("app.tools.request_structured_json", fake_request_structured_json)

    plan = _plan(
        doc_type="project_proposal",
        assumptions=[],
        open_questions=["Which CRM or support platform should it integrate with?"],
        tasks=[
            {
                "task_id": "t1",
                "description": "Gather context about current tools.",
                "tool_name": "gather_context",
                "args": {"topic": "support tooling"},
            },
            {
                "task_id": "t2",
                "description": "Build an integrations table.",
                "tool_name": "make_table",
                "args": {"table_name": "integrations"},
            },
            {
                "task_id": "t3",
                "description": "Build the document.",
                "tool_name": "build_docx",
                "args": {},
            },
        ],
    )

    context = execute_plan(USER_REQUEST, plan)

    assert "not supplied by the user" in context["gathered_context"]["source"]
    table = context["tables"][0]
    assert table["rows"] == [["To be determined", "Not specified by the user"]]

    document_id, filename = _build_docx_from_context(context, plan)
    doc_text = _read_docx_text(filename)
    assert "Which CRM or support platform should it integrate with?" in doc_text


# --- Scenario: conflicting constraints --------------------------------------


def test_conflicting_constraints_are_flagged_not_silently_resolved(monkeypatch, tmp_path):
    monkeypatch.setattr("app.docx_builder.GENERATED_DIR", tmp_path)

    # Simulates a compliant planner response (per the SYSTEM_PROMPT rule
    # tested above) for a request like: "Launch next week, but also run a
    # full 6-month phased rollout." A valid AgentPlan can carry that conflict
    # forward as an open question instead of picking one timeline silently.
    plan = _plan(
        doc_type="project_proposal",
        assumptions=[],
        open_questions=[
            "The request specifies both a 1-week launch and a 6-month phased "
            "rollout - these timelines conflict and need clarification."
        ],
        tasks=[
            {
                "task_id": "t1",
                "description": "Build the document.",
                "tool_name": "build_docx",
                "args": {},
            }
        ],
    )

    assert "conflict" in plan.open_questions[0].lower()

    document_id, filename = _build_docx_from_context(
        execute_plan(USER_REQUEST, plan), plan
    )
    doc_text = _read_docx_text(filename)
    assert "timelines conflict" in doc_text


# --- helpers -----------------------------------------------------------------


def _build_docx_from_context(context: dict, plan: AgentPlan):
    build_task_id = plan.tasks[-1].task_id
    result = context["task_results"][build_task_id]
    return result["document_id"], result["filename"]


def _read_docx_text(filename: str) -> str:
    from docx import Document

    from app.docx_builder import GENERATED_DIR

    document = Document(str(GENERATED_DIR / filename))
    return "\n".join(p.text for p in document.paragraphs)
