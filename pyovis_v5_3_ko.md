# Pyovis v5.3 — 프로젝트 종합 기술 문서 (한국어)

> 상태: 현재 저장소 상태와 6개 집중 코드베이스 분석 결과를 바탕으로 통합 작성한 기술 문서
> 프로젝트 루트: `/Pyvis`
> 범위: 아키텍처, 모듈 구조, 실행 흐름, 메모리 시스템, Rust 코어, 샌드박스, MCP/스킬/추적, 인터페이스, 테스트, 스크립트, v5.x 로드맵

---

## 1. 프로젝트 개요

Pyovis는 단일 `llama.cpp` 추론 서버 위에서 **모델 핫스왑** 방식으로 4개의 전문 역할을 수행하는 **로컬 멀티롤 AI 어시스턴트 / 리서치 에이전트**이다.

- **Planner** — 작업 분해
- **Brain** — 분석, 검토, 에스컬레이션, 최종 종합
- **Hands** — 코드 생성 및 수정
- **Judge** — 평가 및 판정

시스템은 다음 요소들을 결합한다.

- 듀얼 GPU 기반 로컬 추론
- Python 오케스트레이션
- PyO3를 통한 Rust 성능 프리미티브
- Docker/venv 기반 실행 격리
- FAISS + NetworkX 기반 메모리 / Graph RAG
- MCP 도구 통합
- 스킬 추출 및 루프 추적
- Telegram / KG 웹 뷰어 / QnA Bot 인터페이스

기존 문서상 현재 구현 기준선은 주로 **v4.0**으로 설명되지만, 이 문서 `pyovis_v5_3_ko.md`는 현재 코드 상태와 `pyovis_v5_architecture.md`, `pyovis_v5_1.md`에 담긴 v5.x 방향성을 함께 반영한 **통합 기술 문서**이다.

---

## 2. 하드웨어 및 런타임 환경

출처: `config/unified_node.yaml`, `README.md`, `ARCHITECTURE.md`

### 2.1 하드웨어

- **CPU**: AMD Ryzen 9 3900X
- **YAML 상 CPU 코어 배치**
  - interface: `[0, 1]`
  - orchestration: `[2, 3]`
  - ai_inference: `[4, 5, 6, 7]`
- **GPU 0**: RTX 4070 SUPER, 12GB VRAM, `sm_89`, split ratio `0.55`
- **GPU 1**: RTX 3060, 12GB VRAM, `sm_86`, split ratio `0.45`
- **총 VRAM**: 24GB
- **RAM**: 32GB
- **저장공간**: 모델/워크스페이스 포함 약 60GB+

### 2.2 GPU 모델 서빙 전략

Pyovis는 여러 역할용 모델을 동시에 상주시켜 운용하지 않고, **하나의 llama.cpp 서버를 두고 활성 역할 모델을 교체**하는 방식을 사용한다.

핵심 서버 설정 (`config/unified_node.yaml`):

- **Host**: `0.0.0.0`
- **Port**: `8001`
- **split_mode**: `layer`
- **tensor_split**: `0.55,0.45`
- **기본 n_gpu_layers**: `60`
- **threads**: `4`
- **warmup_timeout**: `120s`

### 2.3 구성된 역할별 모델

| 역할 | 모델 | 크기 | 컨텍스트 | GPU Layers | 비고 |
|---|---|---:|---:|---:|---|
| Planner | `GLM-4.7-Flash-Q4_K_M.gguf` | 18GB | 65536 | 60 | Brain fallback 가능 |
| Brain | `Qwen3-14B-Q5_K_M.gguf` | 10GB | 40960 | 60 | 핵심 추론 역할 |
| Hands | `mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf` | 14GB | 65536 | 40 | `--jinja` 사용, Brain fallback 가능 |
| Judge | `DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf` | 9GB | 65536 | 60 | 독립 평가 역할 |

추가 역할별 런타임 특성:

- **Brain** KV cache: `q4_0`
- **Hands** KV cache: `q8_0`
- **Judge**는 fresh context / cache reset 성격
- 모델 스왑 중에는 요청이 차단될 수 있음

---

## 3. 최상위 아키텍처

### 3.1 전체 흐름

```text
사용자 요청
    ↓
SessionManager
    ↓
RequestAnalyzer
    ↓
분기:
  - CHAT / 직접 응답
  - SIMPLE task
  - COMPLEX task → ResearchLoopController

ResearchLoopController
  PLAN → BUILD → CRITIQUE → EVALUATE
                      ↘ REVISE / ENRICH / ESCALATE ↺

성공 시:
  final review → optional README generation → memory ingestion / tracking
```

### 3.2 주요 계층

1. **인터페이스 계층**
   - Telegram bot
   - KG web viewer
   - QnA bot

2. **오케스트레이션 계층**
   - SessionManager
   - RequestAnalyzer
   - ResearchLoopController / loop controller

3. **AI 역할 계층**
   - Planner
   - Brain
   - Hands
   - Judge / EnhancedJudge
   - ModelSwapManager

4. **실행 계층**
   - CriticRunner
   - WorkspaceManager / FileWriter
   - StaticAnalyzer
   - ExecutionPlan
   - Snapshot / rollback helpers
   - Search/Replace patching

5. **메모리 계층**
   - KGStore (FAISS)
   - KnowledgeGraphBuilder (NetworkX)
   - ExperienceDB
   - ConversationMemory
   - UserProfile

6. **인프라 / 통합 계층**
   - MCP client / registry / adapters
   - Skill system
   - Loop tracker
   - Monitoring / watchdog
   - Rust core (`pyovis_core`)

### 3.3 시작 방식 및 엔트리포인트

Pyovis는 어떤 범위까지 띄우느냐에 따라 여러 엔트리포인트를 가진다.

#### `pyovis/cli.py`

`pyproject.toml`에 `pyovis = "pyovis.cli:main"`으로 등록된 콘솔 엔트리포인트이다.

이 CLI는 다음을 수행한다.

- 로깅 구성
- `.env` 로드
- 기존 `llama-server` / launcher 프로세스 정리
- `PyPriorityQueue`, `ModelSwapManager`, `LoopTracker` 생성
- `SessionManager` 시작
- `TelegramBot` 시작
- KG Web Viewer (`8502`) 시작
- Brain 역할 LLM 서버를 백그라운드 스레드로 시작
- signal 기반 종료 처리

#### `pyovis/main.py`

더 작은 비동기 부트스트랩 경로이다. `PyPriorityQueue`, `ModelSwapManager`, KG server task, `LoopTracker`, `SessionManager`를 생성하고 메인 세션 루프를 실행한다.

#### `run_unified.py`

프로세스 정리 → 핵심 컴포넌트 생성 → Telegram Bot 시작 → 백그라운드 LLM 서버 시작 → SessionManager 실행까지 포함하는 독립 실행형 통합 런처이다.

#### `run_telegram_bot.py`

Telegram 전용 런처로, Telegram Bot 엔트리포인트를 직접 불러 실행한다.

#### `run_qna.py`

문서 기반 QnA 웹앱을 시작한다. Brain 모델 서버가 `localhost:8001`에서 먼저 떠 있어야 한다.

---

## 4. 저장소 구조

### 4.1 핵심 디렉토리

```text
/Pyvis/
├── pyovis/
│   ├── ai/
│   ├── orchestration/
│   ├── execution/
│   ├── memory/
│   ├── skill/
│   ├── mcp/
│   ├── tracking/
│   ├── monitoring/
│   └── interface/
├── pyovis_core/
├── config/
├── docker/
├── scripts/
├── tests/
├── qna_bot/
├── README.md
├── ARCHITECTURE.md
├── pyovis_v5_architecture.md
├── pyovis_v5_1.md
└── run_qna.py
```

### 4.2 책임별 핵심 파일

- `config/unified_node.yaml` — 전역 설정 소스
- `pyproject.toml` — Python 의존성 및 maturin 빌드 설정
- `pyovis_core.pyi` — Rust 바인딩용 Python stub
- `pyovis/main.py` — 최소 비동기 bootstrap 엔트리포인트
- `pyovis/cli.py` — `pyovis` 콘솔 엔트리포인트
- `docker/sandbox/Dockerfile` — 격리 런타임 이미지
- `run_qna.py` — QnA 웹앱 런처
- `run_unified.py` — Telegram + SessionManager + LLM + KG web 통합 런처
- `run_telegram_bot.py` — Telegram 전용 런처
- `ARCHITECTURE.md` — 현재 아키텍처 참조 문서
- `pyovis_v5_architecture.md`, `pyovis_v5_1.md` — v5.x 설계 문서

---

## 5. AI 역할 계층

디렉토리: `pyovis/ai/`

### 5.1 Planner

주요 역할:

- 복잡 작업 분해
- 파일 단위 todo 정의
- pass criteria 정의
- Hands의 self-fix 범위 정의

구현 파일:

- `pyovis/ai/planner.py`

예상 출력 구조:

- `plan`
- `file_structure`
- `todo_list`
- `pass_criteria`
- `self_fix_scope`

현재 Planner 구현은 위 필드를 가진 **JSON-only 출력**을 명시적으로 요구한다.

핵심 행동 원칙:

- **tool-first principle** — 코드 생성보다 도구/MCP/fetch로 해결 가능한 경우 우선 도구 경로를 사용

프롬프트 파일과 구현에서 확인되는 추가 Planner 규칙:

- 먼저 전체 file structure를 설계해야 함
- `todo_list`는 의존성 순서대로 정렬되어야 함
- 각 todo는 정확히 하나의 파일에 대응해야 함
- 각 description은 Hands가 바로 구현할 수 있을 정도로 구체적이어야 함
- 실시간 정보성 요청은 코드 생성보다 `fetch` / MCP tool plan을 우선해야 함

`planner.py` 구현에는 다음과 같은 schema normalization / guardrail 로직도 들어 있다.

- 문자열 형태 todo를 구조화된 object로 변환
- 누락된 `id`, `file_path` 자동 보완
- `"app.py - 설명"` 같은 `file_path`에서 설명 접미사 제거
- 모델이 빈 `todo_list`를 반환하면 fallback `todo_list` 생성
- `pass_criteria`가 없으면 fallback `pass_criteria` 생성
- `pass_criteria` key를 문자열로 정규화

Planner 프롬프트는 todo 항목의 `pass_type` 의미도 정의한다.

- `exit_only` — 정상 실행 자체가 목표인 경우
- `output_check` — 출력/산출물을 의미적으로 검증해야 하는 경우

런타임에서는 Planner 출력이 loop controller에 직접 주입된다.

- `ctx.todo_list`
- `ctx.pass_criteria`
- `ctx.self_fix_scope`

이 값들이 실제 build 순서, Judge 평가 기준, Hands self-fix 허용 범위를 결정한다.

### 5.2 Brain

주요 역할:

- 요청 분석
- 계획 검토
- 에스컬레이션 처리
- 최종 결과 종합
- 단순 플로우 직접 응답

QnA Bot도 Brain 역할을 로컬 OpenAI 호환 엔드포인트에 연결하여 사용한다.

### 5.3 Hands

주요 역할:

- 계획 기반 코드 생성
- Search/Replace block을 이용한 수정
- execution hint / execution plan 생성
- 필요 시 `pip_packages` 선언

핵심 특성:

- 낮은 temperature로 더 결정적인 코드 생성
- 수정은 **Aider-style Search/Replace blocks** 사용
- 패치 정합성이 낮을 경우 무작정 전체 파일을 갈아엎지 않고 fallback 로직 사용

### 5.4 Judge

주요 역할:

- 실행 결과가 pass criteria를 만족하는지 평가
- 실패 분류
- 다음 판정 반환
  - `PASS`
  - `REVISE`
  - `ENRICH`
  - `ESCALATE`

### 5.5 EnhancedJudge

v5.1에서는 더 명시적인 **4단계 Thought Instruction checklist**가 도입되었다.

1. Exit code 검증
2. PASS criteria 검증
3. 누락 심볼 검출
4. 에러 분류

이를 통해 단순한 블랙박스 판정보다 더 투명한 평가 메타데이터를 제공한다.

### 5.6 프롬프트 파일

프롬프트 파일은 `pyovis/ai/prompts/` 아래에 있으며, `pyovis/ai/prompts/loaders.py`로 로드된다.

현재 저장소에 존재하는 프롬프트 파일:

- `brain_prompt.txt`
- `judge_prompt.txt`
- `planner_prompt.txt`
- `hands_prompt.txt`
- `hands_revise_prompt.txt`
- `planner_behavior.txt`
- `planner_system_v5.3.txt`

이 프롬프트 파일들이 실제 역할 행동 정책의 상당 부분을 규정하며, 특히 tool-first planner 동작과 planning/coding/judging 역할 분리를 고정한다.

### 5.7 Response utilities

`pyovis/ai/response_utils.py`는 다음과 같은 정리/파싱 작업을 담당한다.

- 텍스트 메시지 내용 추출
- `<think>...</think>` 같은 reasoning / CoT block 제거
- 모델 출력 내부 JSON 파싱
- 긴 내부 reasoning 문자열 요약

---

## 6. 모델 핫스왑 아키텍처

핵심 파일: `pyovis/ai/swap_manager.py`

### 6.1 목적

`ModelSwapManager`는 `8001` 포트의 활성 llama.cpp 서버를 관리하고, 추론 전 요청 역할에 맞는 모델이 로드되도록 보장한다.

### 6.2 스왑 절차

일반적인 스왑 흐름:

1. 요청 역할이 이미 활성화되어 있는지 확인
2. 현재 서버 health check 수행
3. 잘못된 역할이거나 비정상 상태라면:
   - 현재 프로세스 종료 (`SIGTERM`, 이후 필요 시 `SIGKILL`)
   - 포트 해제
   - 목표 모델로 llama server 재시작
   - `/health` 폴링
   - 필요 시 `/props`로 역할 검증
4. 결과를 `swap.jsonl`에 기록

### 6.3 역할별 특성

- `Planner`, `Brain`, `Hands`, `Judge`는 명시적 역할
- Hands는 특수 런치 플래그(`--jinja`)가 필요할 수 있음
- Brain / Judge는 더 엄격한 역할로 취급
- 스왑 중 요청 차단 가능

### 6.4 로깅

스왑 히스토리 저장 위치:

- `/pyovis_memory/logs/swap.jsonl`

저장 필드 예시:

- source role
- target role
- elapsed time
- success 여부
- swap count

---

## 7. 오케스트레이션 계층

디렉토리: `pyovis/orchestration/`

### 7.1 SessionManager

주요 책임:

- 작업 라우팅 진입점
- MCP 도구 가용성 통합
- graph/context 정보로 요청 보강
- 직접 응답 vs loop controller 경로 결정
- 대화/결과를 memory에 적재 가능

핵심 라우팅 개념:

- **CHAT** → 경량 직접 응답
- **SIMPLE** → 직접 또는 단순 실행 경로
- **COMPLEX** → 전체 루프 실행

부가적으로 다음 기능을 제공한다.

- live MCP tool lookup
- fallback tool suggestion
- keyword-to-tool mapping

### 7.2 RequestAnalyzer

주요 책임:

- 작업 복잡도 분류
- 추가 clarification 필요 여부 판단
- 필요한 도구 결정
- tool availability / fallback 상태 판단

주요 분석 산출물:

- complexity (`CHAT`, `SIMPLE`, `COMPLEX` 등)
- clarification requirement
- required tools
- tool availability / fallback status
- analyzer reasoning

### 7.3 ResearchLoopController / LoopController

중앙 멀티스텝 실행 FSM이다.

#### 루프 상태

```text
PLAN
BUILD
CRITIQUE
EVALUATE
REVISE
ENRICH
ESCALATE
COMPLETE
```

#### 핵심 생명주기

1. **PLAN**
   - Planner/Brain이 계획, todo, criteria, self-fix scope 생성

2. **BUILD**
   - Hands가 코드 생성
   - execution plan / setup command 수집 가능
   - 현재 코드 저장

3. **CRITIQUE**
   - CriticRunner가 생성 코드/테스트 실행
   - stdout/stderr/exit_code/error_type 수집

4. **EVALUATE**
   - Judge가 점수와 verdict 반환

5. **REVISE / ENRICH**
   - Hands가 self-fix 허용 범위에서 수정
   - syntax validation / fallback / rollback 적용

6. **ESCALATE**
   - Brain이 계획 수정 또는 인간 개입 필요 여부 판단

7. **COMPLETE**
   - final review
   - optional README generation
   - skill evaluation / memory ingestion / tracking finalize

#### 안전 제약

- `max_loops: 5`
- `max_consecutive_fails: 3`
- `max_escalations: 2`
- `pass_threshold: 90`
- `revise_threshold: 70`
- `sandbox_timeout: 30`
- failure reason / reasoning log는 bounded 처리

### 7.4 ChatChainController

파일: `pyovis/orchestration/chat_chain.py`

이 컨트롤러는 모든 불일치를 일반 revise loop로 처리하지 않고, v5.1의 **consensus loop** 메커니즘을 구현한다.

구현된 세그먼트:

- **Segment A**: Planner ↔ Brain 설계 합의
- **Segment B**: Brain ↔ Hands 수정 합의

핵심 런타임 구조:

- `TerminationReason`
- `ConsensusResult`
- `HardLimitConfig`
- `ChatChainController`

`ChatChainController.consensus_loop(...)`는 bounded 대화를 수행하고 다음을 반환한다.

- 합의 성공 여부
- 최종 내용
- 대화 메시지 목록
- turn 수
- termination reason
- optional hard-limit trigger metadata

### 7.5 Hard Limit 중단 시스템

파일:

- `pyovis/orchestration/chat_chain.py`
- `pyovis/orchestration/hard_limit.py`

Hard Limit 시스템은 생산성 없는 합의 루프를 강제로 끊기 위해 존재한다.

구현된 트리거 계열:

1. `diff_too_small` — 무의미한 반복 / 너무 작은 변경
2. `ast_error_repeat` — 반복적인 구조 붕괴
3. `clarification_loop` — clarification 과다 반복
4. `max_turns` — 상한 도달
5. `sycophancy` — 잘못된 코드에 너무 쉽게 동의

`hard_limit.py`의 핵심 구조:

- `HardLimitTrigger`
- `EscalationAction`
- `TriggerDefinition`
- `HardLimitState`
- `HardLimitResult`
- `HardLimitChecker`

### 7.6 SymbolExtractor

파일: `pyovis/orchestration/symbol_extractor.py`

Symbol Extractor는 v5.1의 컨텍스트 압축 기능이다. Python AST를 사용해 의존 파일의 공개 심볼을 요약하고, 이를 Hands 컨텍스트에 넣는다.

추출 대상 카테고리:

- classes
- functions / async functions
- constants

핵심 구조:

- `ClassSymbol`
- `FunctionSymbol`
- `ConstantSymbol`
- `SymbolSummary`
- `SymbolExtractor`

모듈 docstring에 따르면, extraction 성공 시 Hands 컨텍스트를 대략 **58K → 32K** 수준으로 줄이는 것이 목표다.

v5.3부터는 `extract_graph()` 메서드도 제공한다. 이 메서드는 Python 파일을 `KnowledgeGraphBuilder` code symbol graph에 적재하기 위한 구조화된 표현을 반환한다.

```python
{
    "module": {"id": "module:<file_path>", "file_path": ..., "language": "python"},
    "symbols": [...],  # id, name, qualified_name, kind, file_path, line, parent, signature
    "edges": [...],    # source, target, relation, line
}
```

---

## 8. 메모리 아키텍처

디렉토리: `pyovis/memory/`

Pyovis의 메모리는 단일 저장소가 아니라 여러 하위 시스템의 조합이다.

### 8.1 KGStore (FAISS 기반 벡터 스토어)

구현: `pyovis/memory/kg_server.py`

핵심 특성:

- 임베딩 모델: `sentence-transformers/all-MiniLM-L6-v2`
- 임베딩 차원: `384`
- FAISS index: `IndexFlatL2`
- 원문 문서를 벡터 인덱스와 함께 저장
- lazy initialization

저장 경로:

- `/pyovis_memory/kg/faiss.index`
- `/pyovis_memory/kg/documents.txt`

API 형태:

- 텍스트 추가
- 쿼리 기반 top-k 검색

### 8.2 KnowledgeGraphBuilder

구현: `pyovis/memory/graph_builder.py`

그래프 중심 RAG 컴포넌트이다.

주요 기능:

- 텍스트로부터 triplet 추출
- concept/entity 추출
- persistent graph 구축
- depth 기반 neighbor query
- NetworkX community detection
- community summary 생성
- Graph RAG query
- graph context + vector context merge
- HTML visualization 생성

핵심 메서드 (v5.3):

- `add_text(text, source)` — LLM이 triplet + concept를 추출해 그래프에 삽입. `asyncio.create_task()`로 호출 (fire-and-forget)
- `add_document(...)` — 긴 텍스트를 청크 분할 후 `add_text` 반복 호출
- `extract_triplets(...)` — LLM raw 추출, dict 리스트 반환
- `extract_concepts(...)` — LLM raw 추출, dict 리스트 반환
- `add_triplet(subject, predicate, object, origin)` — 시맨틱 triple 직접 삽입. Neo4j 미러링 포함. `asyncio.create_task()`로 호출
- `add_code_symbols(code, file_path, source)` — `SymbolExtractor.extract_graph()`를 통해 Python 파일을 code symbol graph에 적재. Neo4j 미러링 포함. `asyncio.create_task()`로 호출
- `query_code_symbols(query, depth)` — code symbol graph 탐색
- `query_graph_rag(...)` — knowledge graph 결과 외 `code_results`도 포함
- `hybrid_search(...)` — FAISS 벡터 결과와 graph context 결합
- `detect_communities()` — NetworkX greedy modularity 클러스터링
- `summarize_communities()` — community별 LLM 요약 생성
- `visualize(output_path, height, width)` — 인터랙티브 Pyvis HTML 그래프 렌더링. `node_type`별 스타일: `semantic`=파란색 dot (크기 18), `module`=주황색 box (크기 16), `code_symbol`=초록 diamond (크기 12, `kind`별 색상: function=`#3cb44b`, class=`#2dd4bf`, method=`#a3e635`, constant=`#fbbf24`). `edge_type="code"` 엣지는 초록(`#3cb44b`, 두께 2)으로 표시
- `to_networkx()` — knowledge graph + code symbol graph 노드/엣지를 `networkx.DiGraph`로 export
- `get_stats()` — `total_nodes`, `total_edges`, `total_communities`, `total_code_modules`, `total_code_symbols`, `total_code_edges`, `neo4j_enabled` 반환

### 8.3 하이브리드 검색 흐름

```text
사용자 질의
   ├─ KGStore / FAISS 벡터 검색
   └─ KnowledgeGraphBuilder 그래프 검색
        ├─ entity extraction
        ├─ neighborhood traversal
        ├─ community lookup
        └─ summary aggregation

병합된 context
   → downstream reasoning에 주입
```

### 8.4 ExperienceDB

구현: `pyovis/memory/experience_db.py`

목적:

- 성공/실패 경험 저장
- 성공 패턴 재사용
- task type / error type 기준 실패 패턴 분석
- FAISS 기반 과거 경험 semantic retrieval

이 모듈은 단순 placeholder가 아니다. 실제 구현에는 다음이 포함된다.

- `ExperienceEntry` 데이터 모델
- semantic indexing
- success/failure pattern retrieval
- task-type 중심 재사용 구조

즉 ExperienceDB는 현재 코드에 존재하며, 더 넓은 v5 learning workflow는 아직 진화 중인 상태다.

저장:

- `/pyovis_memory/experience/experience_faiss.index`
- `/pyovis_memory/experience/experience_metadata.json`

### 8.5 ConversationMemory

구현: `pyovis/memory/conversation.py`

목적:

- 채팅/사용자별 대화 기록 저장
- bounded history 유지 (분석 기준: 30 turns / 60 messages)
- keyword overlap / 참조어 기준 관련 히스토리 필터링
- prompt injection용 포맷 제공

저장:

- `/pyovis_memory/conversations/chat_{id}.json`

### 8.6 UserProfile

구현: `pyovis/memory/user_profile.py`

목적:

- 피드백과 코드 패턴으로 사용자 선호 학습
- 학습 결과 저장
- 선호 힌트를 다시 프롬프트에 주입

저장:

- `/pyovis_memory/profiles/{user_id}.json`

### 8.7 Code Symbol Graph (v5.3)

v5.3부터 `KnowledgeGraphBuilder`는 기존 knowledge (triplet) 그래프와 나란히 동작하는 **code symbol graph** 계층을 내장한다.

이 계층은 Hands가 Python 파일을 생성하거나 수정할 때마다 자동으로 채워진다. loop controller는 `_save_current_code()` 직후에 `asyncio.create_task()`를 통해 `kg_builder.add_code_symbols()`를 호출한다 — 빌드 루프를 지연시키지 않는 fire-and-forget 방식이다.

#### 그래프 스키마

```text
(:CodeModule  { id, file_path, language, source })
(:CodeSymbol  { id, name, qualified_name, kind, file_path, line, parent, signature, docstring })

(:CodeModule) -[:DEFINES]->     (:CodeSymbol)
(:CodeSymbol) -[:CODE_RELATION { relation, origin, line }]-> (:CodeSymbol)
```

`SymbolExtractor.extract_graph()`가 생성하는 relation 유형:

- `inherits` — 클래스 상속
- `contains` — 메서드 / 중첩 함수 포함 관계
- `calls` — call-graph 엣지 (정적 분석, best-effort)
- `uses` — 상수 / 변수 참조

#### 쿼리 API

```python
result = kg_builder.query_code_symbols(query="DatabaseManager", depth=1)
# returns: {"symbols": [...], "edges": [...], "modules": [...]}
```

`query_graph_rag()`는 이제 knowledge graph neighborhood + vector hit에 code symbol 결과를 함께 병합한다.

### 8.8 Neo4j Graph Mirror (v5.3)

파일: `pyovis/memory/neo4j_backend.py`

`KnowledgeGraphBuilder`의 쓰기 연산을 실행 중인 Neo4j 인스턴스에 미러링하는 optional 계층이다. 로컬 JSON 파일이 **primary source of truth**이며, Neo4j는 Cypher 쿼리, PageRank 등 더 풍부한 그래프 분석을 위해 사용한다.

#### 활성화 방법

아래 4개 환경 변수를 모두 설정하면 자동 활성화된다.

```bash
PYOVIS_NEO4J_URI=bolt://localhost:7687
PYOVIS_NEO4J_USERNAME=neo4j
PYOVIS_NEO4J_PASSWORD=password
PYOVIS_NEO4J_DATABASE=neo4j   # optional, default: neo4j
```

`neo4j` Python 패키지가 없거나 환경 변수가 설정되지 않으면 mirror가 조용히 비활성화된다 — 오류를 발생시키지 않는다.

#### Neo4j 스키마

```cypher
(:Entity  { id, name, kind })
(:Module  { id, path, language, source })
(:CodeSymbol { id, name, qualified_name, kind, file_path, line, parent })

(:Entity)     -[:KG_RELATION   { predicate, origin }]->      (:Entity)
(:Module)     -[:DEFINES]->                                   (:CodeSymbol)
(:CodeSymbol) -[:CODE_RELATION { relation, origin, line }]->  (:CodeSymbol)
```

#### 공개 API

```python
class Neo4jGraphMirror:
    @classmethod
    def from_environment(cls) -> "Neo4jGraphMirror | None": ...
    def mirror_triplet(self, subject, predicate, object_value, origin="") -> None: ...
    def mirror_code_graph(self, module, symbols, edges) -> None: ...
```

---

## 9. Rust 코어 (`pyovis_core`)

Rust 코어는 성능 민감 프리미티브를 Python에 PyO3로 노출한다.

### 9.1 빌드 체인

`pyproject.toml` 기준:

- build backend: `maturin`
- exported module: `pyovis_core`
- manifest path: `pyovis_core/Cargo.toml`

Cargo release 최적화:

- `opt-level = 3`
- `lto = true`

### 9.2 Rust 의존성

분석된 핵심 crate:

- `pyo3`
- `crossbeam`
- `crossbeam-channel`
- `libc`

### 9.3 Priority queue

Rust 파일: `pyovis_core/src/queue/priority_queue.rs`

설계:

- lock-free / low-lock queue
- `SegQueue` 기반
- atomic size tracking
- tiered priority

우선순위 tiers:

- Stop
- Brain
- Hands
- Judge
- Orchestration
- IO

Python 노출 API (`pyovis_core.pyi`):

```python
class PyPriorityQueue:
    def __init__(self) -> None
    def enqueue(self, priority: int, task_type: str, payload: str) -> None
    def dequeue(self) -> Optional[Tuple[int, str, str]]
    def len(self) -> int
    def is_empty(self) -> bool
```

### 9.4 Model hot-swap primitive

Rust 파일: `pyovis_core/src/model/hot_swap.rs`

설계:

- atomic role state (`u8` enum)
- mutex-serialized switching
- sequential consistency semantics

Python 노출 API:

```python
class PyModelSwap:
    def __init__(self) -> None
    def switch_to_planner(self) -> Tuple[str, bool]
    def switch_to_brain(self) -> Tuple[str, bool]
    def switch_to_hands(self) -> Tuple[str, bool]
    def switch_to_judge(self) -> Tuple[str, bool]
    def current_role(self) -> str
```

### 9.5 Thread pool

Rust 파일: `pyovis_core/src/thread_pool/pool.rs`

설계:

- channel 기반 worker pool
- Linux CPU affinity (`libc::sched_setaffinity`)
- context switching 감소 / locality 향상 목적

---

## 10. 실행 및 샌드박싱

디렉토리: `pyovis/execution/`

### 10.1 CriticRunner

생성된 코드를 실제로 실행하는 메인 executor.

주요 책임:

- 격리 실행 환경 생성
- import 기반 dependency 자동 감지
- 필요 dependency 설치
- 코드 / 테스트 / CLI / API 체크 실행
- 실패 분류
- 구조화된 결과 반환

주요 결과 필드:

- `stdout`
- `stderr`
- `exit_code`
- `execution_time`
- `error_type`

### 10.2 ExecutionPlan

`pyovis/execution/execution_plan.py`는 Judge/Critic이 참고하는 실행 메타데이터를 정의한다.

실행 타입:

- `python_script`
- `python_module`
- `python_test`
- `function_call`
- `api_server`
- `cli_command`

핵심 구조:

- `ExecutionPlan`
- `TestCase`

Hands는 이 정보를 생성해 CriticRunner/Judge에게 넘길 수 있다.

### 10.3 WorkspaceManager and FileWriter

구현: `file_writer.py`

목적:

- 격리된 per-project workspace 생성
- `.venv` 관리
- 안전한 file write/read
- project root 밖 경로 traversal 차단
- stale project cleanup

경로:

- `/pyovis_memory/workspace/project_*`

### 10.4 StaticAnalyzer

구현: `static_analyzer.py`

목적:

- `ruff` 실행
- `mypy` 실행
- optional auto-fix
- sandbox 실행 전에 오류 포착

### 10.5 Snapshot / rollback

구현: `snapshot.py`

목적:

- git 기반 snapshot 관리
- 실패 시 이전 상태 복원

### 10.6 Search/Replace parser

구현: `search_replace.py`

목적:

- Aider-style search/replace block 파싱
- exact / normalized / fuzzy match 전략 적용
- whole-file rewrite 대신 incremental revision 지원

Hands 수정 동작에서 이 매칭 전략이 중요하다.

- **exact match** 우선
- **whitespace-normalized match** 다음
- **fuzzy match** 마지막

이 덕분에 패치가 약간 어긋나더라도 전체 파일 재생성을 줄일 수 있다.

### 10.7 Error classification

실행 계층은 다음과 같은 실패 유형을 분류한다.

- `type_error`
- `syntax_error`
- `missing_import`
- `name_error`
- `index_error`
- `key_error`
- `value_error`
- `attribute_error`
- `network_error`
- `install_error`
- `env_error`
- `timeout_error`
- `unknown_error`

분석 기준으로는 총 17종류 수준의 세분화된 분류 체계를 가진다.

---

## 11. Docker 샌드박스

파일: `docker/sandbox/Dockerfile`

### 11.1 Base image

- `python:3.11-slim`

### 11.2 시스템 패키지

- `xvfb`
- `xauth`
- `libgl1`
- `libgl1-mesa-dri`
- `libglib2.0-0`
- `libsm6`
- `libxext6`
- `libxrender1`
- `libx11-6`

headless display / OpenGL workload 지원 목적이다.

### 11.3 사전 설치 Python 패키지

- `requests`
- `pydantic`
- `fastapi`
- `httpx`
- `numpy`
- `pillow`
- `matplotlib`
- `pandas`
- `scipy`
- `pygame`
- `PyOpenGL`
- `PyOpenGL_accelerate`
- `pytest`
- `colorama`
- `click`
- `rich`

### 11.4 사용자 모델

- 비 root user `sandbox` (UID `1000`)
- working directory: `/workspace`
- default command: `python`

### 11.5 YAML 기반 sandbox config

- type: `docker`
- image: `pyvis-sandbox:latest`
- tmpfs path: `/dev/shm/pyovis_sandbox`
- memory limit: `512m`
- CPU limit: `1.0`
- network enabled: `true`

---

## 12. 스킬 시스템

디렉토리: `pyovis/skill/`

### 12.1 SkillManager

목적:

- 현재 task description에 맞는 verified skill 로드
- loop outcome 평가
- 반복 실패 패턴이 보이면 candidate skill draft 생성
- review / promotion workflow 연결

저장 구조:

```text
/pyovis_memory/skill_library/
  ├── verified/
  └── candidate/
```

skill 파일은 YAML frontmatter가 포함된 markdown 형식이며, 다음 metadata를 가진다.

- `id`
- `status`
- `name`
- `category`
- `tags`
- `when_to_use`

### 12.2 SkillValidator

목적:

- 반복 문제를 reusable skill로 승격할지 결정
- 스킬로 해결 불가능한 유형 배제
- 중복/유사 skill 탐지

판단 논리:

- 동일 failure reason 반복
- 충분한 task 다양성
- 환경성 오류 제외

---

## 13. MCP 도구 통합

디렉토리: `pyovis/mcp/`

### 13.1 MCPClient

JSON-RPC 2.0 기반 MCP 서버 통신 구현체.

기능:

- stdio 연결
- protocol session initialize
- tool list 조회
- tool call
- resource read
- capability 추적

### 13.2 MCPManager

목적:

- 여러 MCP client 관리
- add/remove server
- 전체 tool 집계
- 특정 server로 tool call 라우팅

### 13.3 MCPToolAdapter

목적:

- MCP tools를 OpenAI function/tool schema로 변환
- native tools + MCP tools를 하나의 인터페이스로 노출
- LLM이 생성한 tool call 실행

### 13.4 ToolEnabledLLM

multi-iteration tool-calling loop이다.

1. tools schema와 함께 prompt 전송
2. tool calls 수신
3. adapter를 통해 실행
4. 결과를 tool message로 다시 주입
5. 최대 iteration까지 반복
6. 최종 content 반환

이 계층이 `MCPToolAdapter`를 통해 **LLM reasoning ↔ MCP/native tool 실행**을 연결한다.

### 13.5 ToolRegistry and ToolInstaller

추가 MCP 지원 파일:

- `pyovis/mcp/tool_registry.py`
- `pyovis/mcp/tool_installer.py`

`ToolRegistry`는 name, description, approval requirement metadata를 가진 lightweight in-memory registry를 제공한다.

`ToolInstaller`는 approval-gated installation abstraction을 제공하며, `requires_approval=True`인 경우 자동 설치 대신 approval-required 결과를 반환한다.

### 13.6 Registry / installation

Registry explorer가 찾을 수 있는 대표 공식 MCP 서버 예:

- filesystem
- git
- github
- fetch
- memory
- sequential-thinking
- puppeteer

기본 approval mode:

```yaml
mcp:
  requires_approval: true
```

즉 승인 없이 외부 tool server를 자동 설치/사용하지 않는다.

---

## 14. 추적 및 모니터링

### 14.1 LoopTracker

디렉토리: `pyovis/tracking/`

목적:

- task record 시작
- loop failure / model switch count 기록
- task metric finalize
- JSONL 형식으로 저장

저장:

- `/pyovis_memory/loop_records/YYYY-MM-DD.jsonl`

추적 필드 예:

- task id / description
- started / finished times
- total loops
- total time
- switch count
- escalation flag
- fail reasons with timestamps
- final quality
- skill patch added flag

### 14.2 LogMonitor

디렉토리: `pyovis/monitoring/`

목적:

- 세밀한 loop metric 기록
- `loop_metrics.jsonl` 저장
- avg duration / avg cost / success rate 통계 생성

저장:

- `/pyovis_memory/logs/loop_metrics.jsonl`

### 14.3 Watchdog

목적:

- llama server 지속 health-check
- 비정상 시 auto-restart
- 시간 창 내 재시작 횟수 제한

재시작 전략 예:

- `systemctl restart`
- `docker-compose restart`
- shell fallback script

### 14.4 HealthMonitor

목적:

- disk / memory / CPU 사용량 모니터링
- loop cost / error threshold 모니터링
- 임계 초과 시 Telegram alert 발송

모니터링 항목:

- disk usage %
- memory usage %
- CPU usage %
- loop cost
- error count
- loop iteration duration

---

## 15. 인터페이스 계층

### 15.1 Telegram Bot

파일:

- `pyovis/interface/telegram_bot.py`
- `pyovis/interface/telegram_enhanced.py`
- `run_telegram_bot.py`
- `run_unified.py`

핵심 기능:

- 채팅 요청 처리
- 복잡도에 따라 SessionManager 또는 직접 경로 사용
- escalation 추적
- Telegram 길이 제한 대응 메시지 분할
- 운영 명령 제공

명령 예:

- `/start`
- `/help`
- `/status`
- `/tools`
- `/allow`
- `/deny`

향상 기능:

- Whisper 기반 voice transcription
- LLaVA 계열 vision endpoint 연동 image analysis
- code formatting helper
- progress notification

`telegram_enhanced.py`는 단순 placeholder가 아니라 실제 multimodal extension layer다. voice file download + Whisper transcription, image file download + vision analysis, richer progress/code-formatting helper를 포함한다.

### 15.2 KG Web Viewer

파일:

- `pyovis/interface/kg_web.py`

스택:

- Starlette
- NetworkX
- pyvis 스타일 HTML 그래프 시각화

엔드포인트:

- `GET /`
- `GET /graph.html`
- `GET /api/stats`
- `POST /api/rebuild`
- `POST /api/detect-communities`
- `GET /api/nodes`
- `GET /api/edges`

기본 포트:

- `8502`

### 15.3 QnA Bot

파일:

- `qna_bot/app.py`
- `qna_bot/context_loader.py`
- `qna_bot/llm_client.py`
- `qna_bot/static/index.html`
- `run_qna.py`

목적:

Pyovis 프로젝트에 대해 질문할 수 있는 가벼운 웹 UI이며, Brain 모델(`8001`)을 사용한다.

FastAPI 엔드포인트:

- `GET /` → `index.html`
- `POST /api/chat` → SSE streaming response
- `GET /api/health` → LLM / context 상태
- `GET /api/context` → context metadata / preview

startup 동작:

- `load_project_context()`가 project docs를 로드해 캐시
- startup log에 total loaded context size 기록

context loading 기본 대상:

- `README.md`
- `ARCHITECTURE.md`
- `IMPROVEMENTS.md`
- `TASK_TYPES_AND_ROUTING.md`
- `TASK_TYPES_INDEX.md`
- `ISSUE_LIST.md`
- `config/unified_node.yaml`
- 이후 통합 시점에 `pyovis_v5_3.md`, `pyovis_v5_3_ko.md`도 포함 가능

`pyovis/` 모듈 트리도 추가한다.

기본 per-file truncation limit:

- `8000` chars (통합 이후 조정 가능)

LLM integration:

- base URL: `http://localhost:8001`
- endpoint: `/v1/chat/completions`
- model field: `local`
- `temperature = 0.7`
- `max_tokens = 4096`
- streaming enabled

CoT filtering:

`stream_brain_response()`가 스트리밍 중 `<think>...</think>` block을 제거한다.

frontend:

- markdown rendering (`marked.js`)
- syntax highlighting (`highlight.js`)
- dark-themed chat layout
- sample question chips
- live health/context status indicators

실행:

```bash
python run_qna.py
python run_qna.py --host 0.0.0.0 --port 8080
python run_qna.py --reload
```

전제:

- Brain model server가 `localhost:8001`에서 먼저 실행 중이어야 함

---

## 16. Python 의존성

출처: `pyproject.toml`

### 16.1 Runtime dependencies

- `fastapi>=0.100.0`
- `uvicorn[standard]>=0.23.0`
- `httpx>=0.24.0`
- `pydantic>=2.0.0`
- `pyyaml>=6.0`
- `faiss-cpu>=1.7.4`
- `sentence-transformers>=2.2.0`
- `docker>=6.0.0`
- `uvloop>=0.17.0`
- `numpy>=1.24.0`
- `networkx>=3.0`
- `pandas>=2.0.0`

### 16.2 Development dependencies

- `pytest>=7.0.0`
- `pytest-asyncio>=0.21.0`
- `black>=23.0.0`
- `ruff>=0.1.0`
- `mypy>=1.5.0`

### 16.3 Python 버전 제약

- `requires-python = ">=3.10"`

---

## 17. 테스트 및 커버리지 표면

디렉토리: `tests/`

직접 스캔된 Python test files 15개 + `__init__.py`:

- `test_ai_modules.py`
- `test_chat_chain.py`
- `test_e2e_v5_pipeline.py`
- `test_file_writer.py`
- `test_search_replace.py`
- `test_phase5_integration.py`
- `test_task_classification.py`
- `test_judge_enhanced.py`
- `test_symbol_extractor.py`
- `test_hard_limit.py`
- `test_e2e_loop.py`
- `test_graph_builder.py`
- `test_request_analyzer.py`
- `test_infra_modules.py`
- `__init__.py`

기존 분석 요약 기준 전체 상태:

- **249 / 254 tests passing (~98%)**

커버 영역:

- AI modules / response parsing
- request analysis / routing
- graph builder / memory
- infra modules (MCP, skills, tracking, critic)
- file writer / workspace management
- end-to-end loop behavior
- v5.1 추가 기능: chat chain, hard limit, enhanced judge, symbol extraction, search/replace, v5 pipeline integration

---

## 18. 운영 스크립트

디렉토리: `scripts/`

| 스크립트 | 타입 | 목적 |
|---|---|---|
| `start_model.sh` | Bash | 역할별 llama server 시작/중지/상태 확인 |
| `validate_hardware.sh` | Bash | 하드웨어 및 역할 로드 검증 |
| `profile_swap.sh` | Bash | 여러 cycle에 걸친 swap 성능 측정 |
| `e2e_test.py` | Python | 실제 모델 흐름 기반 end-to-end loop 테스트 |
| `stress_test.py` | Python | swap-cycle 안정성 / stress 테스트 |

---

## 19. 로깅 및 저장소 레이아웃

공통 경로:

```text
/pyovis_memory/
├── models/
├── workspace/
├── loop_records/
├── logs/
│   ├── swap.jsonl
│   └── loop_metrics.jsonl
├── kg/
├── experience/
├── conversations/
├── profiles/
├── skill_library/
│   ├── verified/
│   └── candidate/
└── mcp_servers/
```

---

## 20. 보안 / 신뢰성 메모

`ISSUE_LIST.md` 기반으로 다음 범주의 문제가 보고되었다.

- hardcoded sensitive values / secret 노출 위험
- 일부 utility의 `eval` / `exec`
- async context에서의 blocking `time.sleep()`
- 테스트/도구 내부의 blocking `input()` 패턴
- bare `except:` / debug `print()` 정리 필요

즉 아키텍처는 상당 부분 구현되었지만, hardening work는 여전히 남아 있다.

---

## 21. v5.x 진화 맥락

`pyovis_v5_architecture.md`, `pyovis_v5_1.md`를 기반으로 보면, v5 라인은 v4 기반 위에 더 엄격하고 투명한 autonomous coding architecture를 올리는 방향이다.

### 21.1 주요 v5.1 테마

- **Chat Chain** 합의 루프
  - Planner ↔ Brain
  - Brain ↔ Hands
- **Hard Limit**로 비생산적 루프 중단
- **Communicative Dehallucination**으로 Hands가 불명확 조건을 추정으로 메우지 않도록 유도
- **Enhanced Judge**의 투명한 체크리스트
- **Execution Plan**을 통한 평가 정교화
- **Hands context policy** 개선
- **Graph retrieval** 고도화
- **Experience DB** 강화

### 21.2 구현 상태 스냅샷

설계 문서는 방향을 설명하지만, 현재 저장소는 구현/스텁/진행 중 항목이 섞여 있다.

| 기능 | 현재 상태 | 근거 |
|---|---|---|
| Chat Chain | 구현됨 | `pyovis/orchestration/chat_chain.py` |
| Hard Limit | 구현됨 | `pyovis/orchestration/hard_limit.py` |
| Symbol Extractor | 구현됨 | `pyovis/orchestration/symbol_extractor.py` |
| Enhanced Judge checklist | 구현됨 | `pyovis/ai/judge_enhanced.py` |
| Execution Plan | 구현됨 | `pyovis/execution/execution_plan.py` |
| ExperienceDB | 구현됨, 더 넓은 학습 플로우는 계속 진화 중 | `pyovis/memory/experience_db.py` |
| ToolEnabledLLM / MCP adapter flow | 구현됨 | `pyovis/mcp/tool_adapter.py` |
| Test generator | 스텁 / 부분 구현 | `pyovis/ai/test_generator.py` |
| Parallel generator | 스텁 / 부분 구현 | `pyovis/orchestration/parallel_generator.py` |
| Code Symbol Graph + Neo4j mirror | 구현됨 | `pyovis/memory/graph_builder.py`, `pyovis/memory/neo4j_backend.py` |

중요한 점: 일부 v5.x 테마는 하나의 독립 runtime module이 아니라 prompt/process 전략에 가깝다. `Communicative Dehallucination`이 대표적이다.

### 21.3 분석 기반 phase 상태

- Phase 1: Rust core — complete
- Phase 2: AI engine — complete
- Phase 3: orchestration / loop features — substantially complete
- Phase 4: ExperienceDB 등 robustness / enhancement — in progress
- Phase 5: broader interface layer evolution — planned / partially reserved

---

## 22. QnA Bot 부록 (현재 구현 스냅샷)

이 저장소에는 프로젝트 문서 질의를 위한 전용 웹 QnA 앱이 포함되어 있다.

### 22.1 런타임 구조

```text
Browser
  ↓
FastAPI (`qna_bot/app.py`)
  ├─ startup 시 load_project_context()
  ├─ /api/chat → stream_brain_response()
  ├─ /api/health
  └─ /api/context

stream_brain_response()
  ↓
httpx streaming POST
  ↓
llama.cpp OpenAI-compatible endpoint on :8001
  ↓
Qwen3 Brain response
  ↓
<think> filtering
  ↓
SSE to browser
```

### 22.2 코드 기준 핵심 구현 디테일

- startup 시 전체 project context를 `_CONTEXT`에 캐시
- `POST /api/chat`은 `data: {"text": ...}` 형태의 SSE message를 전송
- `[DONE]`으로 stream 종료
- health endpoint는 다음을 반환
  - `status`
  - `llm_server`
  - `context_loaded`
  - `context_chars`

### 22.3 왜 이 앱이 필요한가

전체 autonomous loop를 거치지 않고도 Pyovis 프로젝트 문서/코드베이스 기반 질문에 빠르게 답하게 해주는 저마찰 인터페이스이기 때문이다.

---

## 23. 요약

Pyovis는 다음을 결합한 **로컬 멀티레이어 자율 AI 시스템**이다.

- 역할 분리된 LLM 오케스트레이션
- 명시적인 실행 / 평가 루프
- 그래프 + 벡터 메모리
- Rust 기반 동시성 프리미티브
- 격리된 코드 실행
- MCP tool use
- skill extraction + operational tracking
- 다양한 인터페이스 표면

현재 저장소에는 이미 상당한 인프라가 구현되어 있으며, v5.x 설계 문서는 이를 더 엄격하고 투명하며 자기수정 가능한 개발 워크플로우로 확장하려는 방향을 보여준다.
