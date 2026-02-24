# Pyovis v4.0 - 개선 사항 및 다음 작업

## 현재 상태 (2026-02-22)

### 완료된 기능
- ✅ Telegram Bot 기본 기능 (/start, /help, /status, /tools)
- ✅ LLM 서버 + Telegram Bot 통합 실행기 (run_unified.py)
- ✅ 메시지 처리 시 typing indicator 표시
- ✅ /allow, /deny 커맨드 추가
- ✅ 도구 설치 승인/거부 흐름 구현
- ✅ 프로세스 시작 시 기존 프로세스 자동 종료
- ✅ Ctrl+C 정상 종료
- ✅ Pyvis → Pyovis 전체 리네이밍
- ✅ 코드 리뷰 패치 (Wave 1-4)
- ✅ KnowledgeGraphBuilder (Graph RAG) 구현 (~751줄)
- ✅ KGStore FAISS 영속성 추가
- ✅ request_analyzer.py 프롬프트 개선 (문맥 기반 판단, 실제 MCP 도구 목록)
- ✅ session_manager.py get_mcp_tools() + suggest_alternative_tools() 추가
- ✅ SessionManager Graph RAG 자동 통합 (_enrich_with_graph_rag, _ingest_to_graph)
- ✅ pyovis_core Rust 빌드 + Python 바인딩 (maturin, 8 Rust tests passing)
- ✅ kg_server.py lazy import 수정 (fastapi/pydantic/numpy → _create_app() 팩토리 패턴)
- ✅ memory/__init__.py `__getattr__` lazy export 추가
- ✅ networkx 패키지 설치
- ✅ 구 pyvis_core/ 디렉토리 삭제
- ✅ test_graph_builder.py 작성 (43 tests)
- ✅ **전체 테스트 172/172 통과**

### 해결된 문제

#### 1. Brain의 문맥 파악 부족 → 해결
**이전:**
- "오늘 요일 알려줘" → clarification_needed (실시간 정보 인식 실패)
- "서울 날씨 알려줘" → weather_api 요청 (존재하지 않는 도구)

**수정 내용:**
- request_analyzer.py 프롬프트에 실제 MCP 도구 목록 추가 (brave-search, fetch, filesystem 등)
- 문맥 기반 실시간 정보 판단 규칙 추가
- 도구 매핑 예시 추가 (날씨 → brave-search 등)
- needs_clarification 판단 기준 명확화

#### 2. 존재하지 않는 도구 요청 → 해결
**이전:**
- weather_api, date_finder 등 MCP registry에 없는 도구 요청

**수정 내용:**
- 프롬프트에 "이 외 도구를 요청하지 말 것" 명시
- session_manager.py에 `_TOOL_FALLBACK` 매핑 테이블 추가
- `suggest_alternative_tools()` 메서드로 실패 시 대체 도구 자동 제안
- `get_mcp_tools()` 메서드로 알려진 MCP 도구 목록을 Brain에 전달

---

## 환경 검증 결과 (2026-02-22)

| 항목 | 버전/상태 |
|------|-----------|
| GPU 0 | RTX 3060 12GB |
| GPU 1 | RTX 4070 SUPER 12GB |
| Rust | 1.93.1 |
| Cargo | 1.93.1 |
| CUDA (driver) | 13.1 |
| CUDA (nvcc) | 12.0 |
| Python | 3.12.3 |
| maturin | 1.12.2 |
| WSL2 | Linux 6.6 |
| networkx | 설치 완료 |
| pyovis_core | 빌드 + import 검증 완료 |
| pytest | **172/172 통과** |

---

## 다음 작업

### 즉시 가능
1. E2E 통합 테스트 시나리오 추가 (실제 KG 파이프라인 포함)
2. KnowledgeGraphBuilder 커뮤니티 요약 캐싱 최적화

### 환경 필요
3. E2E 통합 테스트 (실제 LLM 서버 + Telegram Bot)
4. llama.cpp CUDA 빌드, VRAM 검증

### 예약
5. Phase 4: 인터페이스 레이어 (오디오/비전/텔레그램)

---

## 파일 구조

```
/Pyvis/
+-- pyovis/
|   +-- orchestration/
|   |   +-- session_manager.py    # Graph RAG 자동 통합
|   |   +-- request_analyzer.py
|   |   +-- loop_controller.py
|   +-- ai/
|   |   +-- brain.py, planner.py
|   |   +-- swap_manager.py, response_utils.py
|   +-- memory/
|   |   +-- kg_server.py          # FAISS KGStore (lazy imports)
|   |   +-- graph_builder.py      # KnowledgeGraphBuilder (~751줄)
|   |   +-- __init__.py           # lazy __getattr__ export
|   +-- mcp/
|   |   +-- mcp_client.py, mcp_registry.py, tool_adapter.py
|   +-- skill/
|   |   +-- skill_manager.py
|   +-- tracking/
|   |   +-- loop_tracker.py
|   +-- execution/
|       +-- critic_runner.py, file_writer.py
+-- pyovis_core/                  # Rust (priority queue, hot-swap, thread pool)
+-- tests/                        # 172 Python tests
+-- ARCHITECTURE.md               # 전체 아키텍처 + API 레퍼런스
```

---

## 참고

### MCP 공식 서버 목록
- filesystem, git, github, fetch, brave-search
- slack, google-maps, memory, sequential-thinking, puppeteer

### 실행 명령어
```bash
cd /Pyvis
source .venv/bin/activate
python run_unified.py
```
