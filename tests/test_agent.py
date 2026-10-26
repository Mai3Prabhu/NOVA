from fastapi.testclient import TestClient

from app.executor import ExecutionError
from app.main import app
from app.models import AgentPlan
from app.planner import PlanningError

client = TestClient(app)

VALID_PLAN = AgentPlan(
    doc_type="project_proposal",
    assumptions=["Audience is internal stakeholders."],
    open_questions=["What is the budget?"],
    tasks=[
        {
            "task_id": "task_1",
            "description": "Gather context about the chatbot project.",
            "tool_name": "gather_context",
            "args": {},
        },
        {
            "task_id": "task_2",
            "description": "Build the final Word document.",
            "tool_name": "build_docx",
            "args": {},
        },
    ],
)

VALID_EXECUTION_CONTEXT = {
    "execution_log": [
        {
            "task_id": "task_1",
            "tool_name": "gather_context",
            "status": "success",
            "summary": "gathered mock context on 'chatbot'",
        },
        {
            "task_id": "task_2",
            "tool_name": "build_docx",
            "status": "success",
            "summary": "built document 'abc123.docx'",
        },
    ],
    "sections": [],
    "tables": [],
    "task_results": {
        "task_2": {
            "document_id": "d41d8cd98f00b204e9800998ecf8427e",
            "filename": "d41d8cd98f00b204e9800998ecf8427e.docx",
        }
    },
}


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_agent_returns_plan_and_execution_result_for_valid_request(monkeypatch):
    monkeypatch.setattr("app.main.create_plan", lambda request: VALID_PLAN)
    monkeypatch.setattr(
        "app.main.execute_plan", lambda request, plan: VALID_EXECUTION_CONTEXT
    )

    response = client.post("/agent", json={"request": "Create a project proposal."})

    assert response.status_code == 200
    body = response.json()
    assert body["doc_type"] == "project_proposal"
    assert body["execution_log"][-1]["tool_name"] == "build_docx"
    assert body["execution_log"][-1]["status"] == "success"
    assert body["document_id"] == "d41d8cd98f00b204e9800998ecf8427e"
    assert body["download_url"] == "/download/d41d8cd98f00b204e9800998ecf8427e"


def test_agent_returns_502_when_planning_fails(monkeypatch):
    def failing_create_plan(request):
        raise PlanningError("Could not generate a valid plan: boom")

    monkeypatch.setattr("app.main.create_plan", failing_create_plan)

    response = client.post("/agent", json={"request": "Create a project proposal."})

    assert response.status_code == 502
    assert "Could not generate a valid plan" in response.json()["detail"]


def test_agent_returns_502_when_execution_fails(monkeypatch):
    monkeypatch.setattr("app.main.create_plan", lambda request: VALID_PLAN)

    def failing_execute_plan(request, plan):
        raise ExecutionError("Task task_1 (gather_context) failed: boom", [])

    monkeypatch.setattr("app.main.execute_plan", failing_execute_plan)

    response = client.post("/agent", json={"request": "Create a project proposal."})

    assert response.status_code == 502
    assert "failed" in response.json()["detail"]


def test_agent_error_detail_does_not_leak_provider_response(monkeypatch):
    monkeypatch.setattr("app.main.create_plan", lambda request: VALID_PLAN)

    def failing_execute_plan(request, plan):
        raise ExecutionError(
            "Task task_4 (write_section) failed: Error code: 429 - org_01m4e5j6aqe8ta5gcs64gj0c0b",
            [],
        )

    monkeypatch.setattr("app.main.execute_plan", failing_execute_plan)

    response = client.post("/agent", json={"request": "Create a project proposal."})

    assert response.status_code == 502
    assert "org_" not in response.text
    assert "429" not in response.text


def test_agent_rejects_empty_request():
    response = client.post("/agent", json={"request": ""})
    assert response.status_code == 422


def test_agent_rejects_blank_request():
    response = client.post("/agent", json={"request": "   "})
    assert response.status_code == 422


def test_agent_rejects_missing_field():
    response = client.post("/agent", json={})
    assert response.status_code == 422


def test_agent_rejects_too_long_request():
    response = client.post("/agent", json={"request": "a" * 6001})
    assert response.status_code == 422


def test_agent_accepts_a_realistic_combined_clarification_request(monkeypatch):
    # Mirrors what the frontend sends on regenerate: original request (up to
    # 2000 chars) + clarification (up to 2000 chars) + the fixed instructions
    # block - must still fit comfortably under the new 6000-char limit.
    combined = (
        "Original user request:\n"
        + ("a" * 2000)
        + "\n\nAdditional user-provided clarification:\n"
        + ("b" * 2000)
        + "\n\nInstructions:\n"
        "- Treat the original request and clarification as user-provided requirements.\n"
        "- Use the clarification to resolve relevant open questions and update the document.\n"
        "- Preserve original requirements that are not contradicted by the clarification.\n"
        "- Do not invent facts, budgets, dates, integrations, metrics, or other details "
        "that the user has not provided or that are not supported by the available context.\n"
        "- If a question remains unanswered, keep it as an open question rather than "
        "inventing an answer.\n"
        "- Update assumptions and open questions based on the combined information.\n"
        "- Generate a complete revised document, not just a list of changes."
    )
    assert len(combined) <= 6000

    # The 6000-char limit is what this test verifies; planning itself is
    # mocked so the test never depends on a real LLM call.
    monkeypatch.setattr("app.main.create_plan", lambda request: VALID_PLAN)
    monkeypatch.setattr(
        "app.main.execute_plan", lambda request, plan: VALID_EXECUTION_CONTEXT
    )

    response = client.post("/agent", json={"request": combined})

    assert response.status_code == 200
