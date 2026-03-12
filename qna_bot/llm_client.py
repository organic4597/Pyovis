"""
Brain LLM 스트리밍 클라이언트.

- llama.cpp OpenAI-compatible API (port 8001) 호출
- Qwen3의 <think>...</think> CoT 블록을 실시간 필터링
- AsyncIterator[str] 로 텍스트 청크 yield
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)

LLM_BASE_URL = "http://localhost:8001"
CHAT_ENDPOINT = f"{LLM_BASE_URL}/v1/chat/completions"

_SYSTEM_PROMPT_TEMPLATE = """\
당신은 **Pyovis 프로젝트** 전문 QnA 어시스턴트입니다.
아래 프로젝트 문서를 바탕으로 사용자의 질문에 정확하고 상세하게 답변하세요.

{context}

---

## 답변 규칙
- 프로젝트 문서에 근거한 답변만 제공합니다.
- 마크다운 형식으로 답변합니다 (코드 블록, 표, 목록 등 적극 활용).
- 한국어로 답변합니다.
- 문서에 없는 내용은 "문서에 해당 내용이 없습니다"라고 솔직히 말합니다.
- 추측성 답변은 "추측:"이라고 명시한 후 제공합니다.
"""


async def stream_brain_response(
    question: str,
    context: str,
    history: list[dict] | None = None,
) -> AsyncIterator[str]:
    """
    Brain (Qwen3-14B)에 질문을 보내고 응답을 스트리밍한다.
    <think>...</think> CoT 블록은 실시간으로 제거된다.

    Yields:
        str: 화면에 표시할 텍스트 청크 (CoT 제외)

    Raises:
        httpx.HTTPStatusError: LLM 서버 오류
        httpx.ConnectError: LLM 서버 연결 실패
    """
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(context=context)

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})

    payload = {
        "model": "local",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 4096,
        "stream": True,
    }

    logger.info("Brain QnA 요청 전송: %s", question[:80])

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0)
    ) as client:
        async with client.stream("POST", CHAT_ENDPOINT, json=payload) as resp:
            resp.raise_for_status()

            buffer = ""
            in_think = False

            async for raw_line in resp.aiter_lines():
                if not raw_line.startswith("data: "):
                    continue
                data = raw_line[6:].strip()
                if data == "[DONE]":
                    break

                try:
                    chunk_json = json.loads(data)
                    delta = chunk_json["choices"][0]["delta"].get("content") or ""
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

                if not delta:
                    continue

                buffer += delta

                # --- 실시간 <think>...</think> 필터링 ---
                while True:
                    if not in_think:
                        think_start = buffer.find("<think")
                        if think_start == -1:
                            # think 블록 없음 → 전부 yield
                            yield buffer
                            buffer = ""
                            break
                        else:
                            # think 시작 이전 텍스트 yield
                            if think_start > 0:
                                yield buffer[:think_start]
                            buffer = buffer[think_start:]
                            in_think = True
                    else:
                        think_end = buffer.find("</think>")
                        if think_end == -1:
                            # think 블록 끝 아직 안 옴 → 대기
                            break
                        # think 블록 통째로 버리기
                        buffer = buffer[think_end + len("</think>") :]
                        in_think = False
                        # 버퍼에 남은 텍스트 계속 처리

            # 스트림 종료 후 버퍼 잔여 flush
            if buffer and not in_think:
                yield buffer.strip()

    logger.info("Brain QnA 응답 완료")


async def check_llm_health() -> bool:
    """LLM 서버 연결 상태 확인."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{LLM_BASE_URL}/health")
            return resp.status_code == 200
    except Exception:
        return False
