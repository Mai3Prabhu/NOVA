import pytest
from docx import Document

from app.llm import LLMError
from app.models import PlanTask
from app.tools import SectionContent, TableContent, ToolError, build_docx, gather_context, make_table, write_section

BASE_CONTEXT = {
    "request": "Create a project proposal for an AI customer support chatbot.",
    "doc_type": "project_proposal",
    "assumptions": ["Audience is internal stakeholders."],
    "open_questions": ["What is the budget?"],
    "gathered_context": {},
    "sections": [{"section": "overview", "content": "This is the overview."}],
    "tables": [{"table_name": "timeline", "headers": ["Milestone", "Date"], "rows": [["Kickoff", "Jan 1"]]}],
}


def _task(tool_name: str, args: dict | None = None) -> PlanTask:
    return PlanTask(
        task_id="task_1",
        description="do something",
        tool_name=tool_name,
        args=args or {},
    )


def test_gather_context_returns_mock_data_labeled_as_mock():
    result = gather_context(_task("gather_context", {"topic": "chatbots"}), BASE_CONTEXT)

    assert result["topic"] == "chatbots"
    assert "mock_context" in result
    assert "mock" in result["source"].lower()


def test_gather_context_falls_back_to_request_as_topic():
    result = gather_context(_task("gather_context"), BASE_CONTEXT)
    assert result["topic"] == BASE_CONTEXT["request"]


def test_write_section_returns_llm_content(monkeypatch):
    monkeypatch.setattr(
        "app.tools.request_structured_json",
        lambda system_prompt, user_prompt, response_model: SectionContent(
            content="Generated section text."
        ),
    )

    result = write_section(_task("write_section", {"section": "overview"}), BASE_CONTEXT)

    assert result == {"section": "overview", "content": "Generated section text."}


def test_write_section_wraps_llm_errors_as_tool_error(monkeypatch):
    def failing(system_prompt, user_prompt, response_model):
        raise LLMError("boom")

    monkeypatch.setattr("app.tools.request_structured_json", failing)

    with pytest.raises(ToolError):
        write_section(_task("write_section", {"section": "overview"}), BASE_CONTEXT)


def test_make_table_returns_validated_headers_and_rows(monkeypatch):
    monkeypatch.setattr(
        "app.tools.request_structured_json",
        lambda system_prompt, user_prompt, response_model: TableContent(
            headers=["Milestone", "Date"],
            rows=[["Kickoff", "Jan 1"], ["Launch", "Mar 1"]],
        ),
    )

    result = make_table(_task("make_table", {"table_name": "timeline"}), BASE_CONTEXT)

    assert result["headers"] == ["Milestone", "Date"]
    assert len(result["rows"]) == 2


def test_make_table_wraps_llm_errors_as_tool_error(monkeypatch):
    def failing(system_prompt, user_prompt, response_model):
        raise LLMError("boom")

    monkeypatch.setattr("app.tools.request_structured_json", failing)

    with pytest.raises(ToolError):
        make_table(_task("make_table", {"table_name": "timeline"}), BASE_CONTEXT)


def test_table_content_rejects_mismatched_row_width():
    with pytest.raises(Exception):
        TableContent(headers=["A", "B"], rows=[["only one cell"]])


def test_build_docx_writes_a_real_file_and_returns_its_id(monkeypatch, tmp_path):
    monkeypatch.setattr("app.docx_builder.GENERATED_DIR", tmp_path)

    result = build_docx(_task("build_docx"), BASE_CONTEXT)

    assert set(result.keys()) == {"document_id", "filename"}
    assert result["filename"] == f"{result['document_id']}.docx"

    file_path = tmp_path / result["filename"]
    assert file_path.is_file()
    document = Document(str(file_path))  # must open as a valid .docx
    assert any("overview" in p.text.lower() for p in document.paragraphs)
