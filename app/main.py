"""FastAPI application entry point."""

import logging
import os
import re

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.docx_builder import GENERATED_DIR
from app.executor import ExecutionError, execute_plan
from app.models import AgentRequest, AgentRunResult, HealthResponse
from app.planner import PlanningError, create_plan

load_dotenv()

logger = logging.getLogger(__name__)

app = FastAPI(title="Autonomous AI Agent")

# Lets the Vite frontend (a different origin in dev and when deployed) call
# this API. No cookies/auth are involved, so a plain origin allowlist is
# enough; override CORS_ORIGINS for a locked-down deployment.
_cors_origins = os.getenv("CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors_origins == "*" else [o.strip() for o in _cors_origins.split(",")],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# build_docx always names files "<32 hex chars>.docx" (see app/docx_builder.py);
# anything else cannot be a real document, so it is rejected before even
# touching the filesystem - this is what blocks path traversal.
DOCUMENT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/agent", response_model=AgentRunResult)
def run_agent(payload: AgentRequest) -> AgentRunResult:
    # Provider exceptions can carry raw API responses (org ids, quota
    # details), so the full error is logged server-side and the client only
    # gets a generic detail.
    try:
        plan = create_plan(payload.request)
    except PlanningError as exc:
        logger.error("Planning failed: %s", exc)
        raise HTTPException(
            status_code=502, detail="Could not generate a valid plan. Please try again."
        ) from exc
    except RuntimeError as exc:
        # Missing GROQ_API_KEY or similar configuration error.
        logger.error("Agent configuration error: %s", exc)
        raise HTTPException(status_code=500, detail="Server configuration error.") from exc

    try:
        context = execute_plan(payload.request, plan)
    except ExecutionError as exc:
        logger.error("Execution failed: %s", exc)
        raise HTTPException(
            status_code=502, detail="Document generation failed. Please try again."
        ) from exc

    # build_docx is always the plan's final task, so its result holds the
    # document that was just written to disk.
    docx_result = context["task_results"][plan.tasks[-1].task_id]

    return AgentRunResult(
        doc_type=plan.doc_type,
        assumptions=plan.assumptions,
        open_questions=plan.open_questions,
        execution_log=context["execution_log"],
        sections=context["sections"],
        tables=context["tables"],
        document_id=docx_result["document_id"],
        download_url=f"/download/{docx_result['document_id']}",
    )


@app.get("/download/{document_id}")
def download_document(document_id: str) -> FileResponse:
    if not DOCUMENT_ID_PATTERN.fullmatch(document_id):
        raise HTTPException(status_code=404, detail="Document not found.")

    file_path = (GENERATED_DIR / f"{document_id}.docx").resolve()
    if file_path.parent != GENERATED_DIR.resolve() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document not found.")

    return FileResponse(
        path=file_path,
        filename=f"{document_id}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
