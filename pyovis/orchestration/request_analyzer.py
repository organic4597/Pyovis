from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import httpx

from pyovis.ai.prompts.loaders import load_prompt
from pyovis.ai.swap_manager import ModelSwapManager
from pyovis.ai.response_utils import parse_json_message


class TaskComplexity(Enum):
    CHAT = "chat"  # 일반 대화, 인사, 질문 - 파일 생성 없음
    SIMPLE = "simple"  # 단일 파일 코드 작성 - 파일 생성 가능
    COMPLEX = "complex"  # 복수 파일, 아키텍처 - ResearchLoop 실행


class ToolStatus(Enum):
    NOT_NEEDED = "not_needed"
    NEEDED_APPROVED = "needed_approved"
    NEEDED_PENDING = "needed_pending"
    ALREADY_AVAILABLE = "already_available"


@dataclass
class AnalysisResult:
    complexity: TaskComplexity
    needs_clarification: bool
    clarification_questions: list[str]
    required_tools: list[str]
    tool_status: ToolStatus
    reasoning: str
    available_tools_to_use: list[str]


class RequestAnalyzer:
    def __init__(self, swap_manager: ModelSwapManager) -> None:
        self.system_prompt = load_prompt("brain_prompt.txt")
        self.swap = swap_manager
        self.client = httpx.AsyncClient(timeout=600.0)

    async def analyze(
        self, user_request: str, available_tools: list[str] | None = None
    ) -> AnalysisResult:
        # Check for casual chat - these should NEVER trigger file generation
        chat_patterns = [
            # Greetings
            "안녕",
            "hello",
            "hi",
            "hey",
            "안녕하세요",
            "반가워",
            "good morning",
            "good afternoon",
            "good evening",
            "bye",
            "안녕히",
            "see you",
            # Thanks/Apology
            "thanks",
            "thank you",
            "감사",
            "고마워",
            "sorry",
            "죄송",
            "미안",
            # Casual chat
            "how are you",
            "어떻게 지내",
            "잘 지내",
            "what's up",
            "지내",
            "오늘",
            "날씨",
            "점심",
            "저녁",
            "식사",
            "밥",
            # Politeness
            "please",
            "부탁",
            "제발",
            "could you",
            "would you",
        ]

        # Check for code-related requests (these might need files)
        code_patterns = [
            "만들어",
            "작성",
            "코드",
            "스크립트",
            "함수",
            "클래스",
            "구현",
            "create",
            "write",
            "code",
            "script",
            "function",
            "class",
            "file",
            "python",
            "py",
            "js",
            "ts",
            "java",
            "cpp",
            "파일",
        ]

        user_request_lower = user_request.lower()
        is_chat = any(pattern in user_request_lower for pattern in chat_patterns)
        is_code_request = any(
            pattern in user_request_lower for pattern in code_patterns
        )

        # If it's casual chat WITHOUT code request, classify as CHAT
        if is_chat and not is_code_request:
            return AnalysisResult(
                complexity=TaskComplexity.CHAT,
                needs_clarification=False,
                clarification_questions=[],
                required_tools=[],
                tool_status=ToolStatus.NOT_NEEDED,
                available_tools_to_use=[],
                reasoning="일반 대화 또는 인사말 - 파일 생성 불필요",
            )

        available_tools_str = (
            "\n".join(f"- {tool}" for tool in available_tools)
            if available_tools
            else "없음"
        )

        user_message = f"""사용자 요청: {user_request}

현재 연결된 도구 목록:
{available_tools_str}

아래 JSON 형식으로만 응답하라 (설명 없이):
{{
    "complexity": "simple 또는 complex",
    "needs_clarification": false,
    "clarification_questions": [],
    "required_tools": [],
    "tool_status": "not_needed 또는 already_available 또는 needed_pending",
    "available_tools_to_use": [],
    "reasoning": "한 줄 근거"
}}

## complexity 판단 기준
- simple: 단일 답변/파일, 명확한 스펙, 코드 1~2 개 파일 이내
- complex: 다중 파일 생성, 아키텍처 설계, 여러 모듈 수정, 장시간 작업

## needs_clarification
- 핵심 정보가 완전히 없는 경우에만 true
- 날씨·날짜·시간·간단한 질문은 항상 false

## 도구 판단
사용 가능한 실제 도구 (이 목록 외 도구는 required_tools 에 넣지 말 것):
- fetch: HTTP GET 으로 공개 URL/API 호출 — **항상 연결됨**
- filesystem: 파일 읽기/쓰기/삭제
- git: Git 작업
- github: GitHub API
- puppeteer: 브라우저 자동화

tool_status 값:
- not_needed: 도구 불필요
- already_available: 필요한 도구가 위 목록에 있음 → available_tools_to_use 에 명시
- needed_pending: 필요하지만 위 목록에 없음 → required_tools 에 명시

## 실시간/외부 데이터 처리
- 날씨·환율·뉴스·주가 등 외부 데이터 → fetch 사용 (already_available)
  - 날씨: https://api.open-meteo.com/v1/forecast?latitude=37.27&longitude=127.00&current_weather=true&timezone=Asia%2FSeoul
- 날짜·시간·요일 계산 → 도구 불필요 (not_needed), Brain 이 system 메시지의 현재 시각으로 직접 답변
- 특정 웹페이지 내용 조회 → fetch (already_available)
- 파일 작업 → filesystem (already_available)
"""
        response = await self._call(user_message)
        result = parse_json_message(
            {"content": response},
            default={
                "complexity": "complex",
                "needs_clarification": True,
                "clarification_questions": ["요청을 더 구체적으로 설명해 주세요."],
                "required_tools": [],
                "tool_status": "not_needed",
                "available_tools_to_use": [],
                "reasoning": "분석 실패로 기본값 사용",
            },
        )

        tool_status_str = result.get("tool_status", "not_needed")
        tool_status_map = {
            "not_needed": ToolStatus.NOT_NEEDED,
            "already_available": ToolStatus.ALREADY_AVAILABLE,
            "needed_pending": ToolStatus.NEEDED_PENDING,
        }

        return AnalysisResult(
            complexity=TaskComplexity(result.get("complexity", "complex")),
            needs_clarification=result.get("needs_clarification", False),
            clarification_questions=result.get("clarification_questions", []),
            required_tools=result.get("required_tools", []),
            tool_status=tool_status_map.get(tool_status_str, ToolStatus.NOT_NEEDED),
            reasoning=result.get("reasoning", ""),
            available_tools_to_use=result.get("available_tools_to_use", []),
        )

    async def handle_simple_task(self, user_request: str) -> dict:
        """Handle simple task - may or may not generate file based on request type."""
        user_message = f"""
사용자 요청: {user_request}

이 요청은 간단한 작업으로 판단되었다. 직접 처리하라.

다음 JSON 형식으로 응답하라:
{{
    "status": "success 또는 need_info",
    "result": "작업 결과 (코드, 답변 등)",
    "file_path": "저장할 파일 경로 (코드 작성인 경우에만, 일반 질문은 null)",
    "message": "사용자에게 전달할 메시지"
}}

작업 유형별 처리:
- 코드 작성: 완전하고 실행 가능한 코드를 작성하고 file_path 에 파일 경로 명시
- 질문 답변: 명확하고 간결한 답변을 result 에 작성, file_path 는 null
- 날짜/시간: 현재 시각을 기반으로 답변, file_path 는 null
- 날씨/실시간 정보: fetch 도구로 API 호출 후 결과 반환, file_path 는 null

**중요**: 일반 대화, 인사, 질문에는 file_path 를 포함하지 마라 (null 로 설정).
"""
        response = await self._call(user_message)
        return parse_json_message(
            {"content": response}, default={"status": "error", "message": "처리 실패"}
        )

    async def _call(self, user_message: str) -> str:
        await self.swap.ensure_model("brain")

        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.3,
            "max_tokens": 4096,
        }
        resp = await self.client.post(self.swap.api_url, json=payload)
        resp.raise_for_status()
        message = resp.json()["choices"][0]["message"]
        return message.get("content") or message.get("reasoning_content") or ""
