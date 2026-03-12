"""
Pyovis QnA Bot — FastAPI 애플리케이션.

엔드포인트:
  GET  /             → 채팅 UI (index.html)
  POST /api/chat     → SSE 스트리밍 응답
  GET  /api/health   → 서버 + LLM 연결 상태
  GET  /api/context  → 로드된 컨텍스트 길이 확인
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from qna_bot.context_loader import load_project_context
from qna_bot.llm_client import check_llm_health, stream_brain_response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Pyovis QnA Bot", version="1.0.0")

_CONTEXT: str = ""
_STATIC_DIR = Path(__file__).parent / "static"


# ─────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────


@app.on_event("startup")
async def _startup() -> None:
    global _CONTEXT
    logger.info("프로젝트 컨텍스트 로딩 중...")
    _CONTEXT = load_project_context()
    logger.info("컨텍스트 로딩 완료 (%d자)", len(_CONTEXT))


# ─────────────────────────────────────────────
# 모델
# ─────────────────────────────────────────────


class ChatRequest(BaseModel):
    question: str
    history: list[dict] = []


# ─────────────────────────────────────────────
# 라우터
# ─────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index() -> HTMLResponse:
    html_path = _STATIC_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=500, detail="index.html 없음")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """질문을 받아 Brain LLM의 응답을 SSE 스트리밍으로 반환한다."""
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="질문이 비어 있습니다")

    if not _CONTEXT:
        raise HTTPException(status_code=503, detail="컨텍스트가 로드되지 않았습니다")

    async def _event_stream():
        try:
            async for chunk in stream_brain_response(
                question=req.question,
                context=_CONTEXT,
                history=req.history or None,
            ):
                if chunk:
                    payload = json.dumps({"text": chunk}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
        except Exception as exc:
            logger.exception("스트리밍 중 오류")
            error_payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
            yield f"data: {error_payload}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/health")
async def health() -> dict:
    """서버 및 LLM 연결 상태를 반환한다."""
    llm_ok = await check_llm_health()
    return {
        "status": "ok",
        "llm_server": "connected" if llm_ok else "disconnected",
        "context_loaded": bool(_CONTEXT),
        "context_chars": len(_CONTEXT),
    }


@app.get("/api/context")
async def context_info() -> dict:
    """로드된 컨텍스트 메타 정보를 반환한다."""
    return {
        "context_chars": len(_CONTEXT),
        "context_preview": _CONTEXT[:500] + "..." if len(_CONTEXT) > 500 else _CONTEXT,
    }
