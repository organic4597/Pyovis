# PYVIS v4.0 — TODO

> Implementation checklist organized by dependency order.
> Single-server architecture: dual GPU parallel (split-mode layer), one model loaded at a time, port 8001.

---

## Sprint 1: Infrastructure Foundation

- [x] Create `/pyvis_memory` directory structure (models, workspace, logs, skill_library, etc.)
- [x] Download model files to `/pyvis_memory/models/`
  - GLM-4.7-Flash-Q4_K_M.gguf (Planner, 18GB)
  - Qwen3-14B-Q5_K_M.gguf (Brain, 10GB)
  - mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf (Hands, 14GB)
  - DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf (Judge, 9GB)
- [x] Write `config/unified_node.yaml` (per-role ctx_size, n_gpu_layers, jinja, fallback)
- [x] Write `scripts/start_model.sh` (planner/brain/hands/judge/swap/status commands)
- [x] Write `pyvis/ai/swap_manager.py` (model swap with fallback logic)

---

## Sprint 2: Rust Core Layer (Phase 1)

- [x] Set up Cargo.toml workspace
- [x] Implement Lock-Free priority queue (crossbeam SegQueue, P0 Stop → P1 AI → P2 IO)
- [x] Implement CPU Affinity thread pool (cores 4-7 for AI inference)
- [x] Implement Model Hot-Swap controller (ModelHotSwap, SwitchResult)
- [x] PyO3 bindings + maturin build (PyPriorityQueue, PyModelSwap)
- [x] Unit tests (cargo test — 8/8 passing)

---

## Sprint 3: AI Engine (Phase 2)

- [x] Build llama.cpp with CUDA (sm_86 + sm_89 mixed architecture)
- [x] Verify server startup per role:
  - Planner: GLM-4.7-Flash, ctx 65536, ngl 60, kv q8_0
   - Brain: Qwen3-14B, ctx 40960, ngl 60, kv q4_0 (model limit: 40K)
   - Hands: Devstral-24B, ctx 80000, ngl 40, kv q8_0, --jinja
   - Judge: DeepSeek-R1-14B, ctx 65536, ngl 60, kv q8_0 (model limit: 128K)
- [x] Measure actual VRAM usage and verify n_gpu_layers values
- [x] Implement Planner client (plan generation)
- [x] Implement Brain client (plan, handle_escalation, final_review + CoT strip)
- [x] Implement Hands client (build, revise)
- [x] Implement Judge client (evaluate with fresh context)
- [x] Write system prompts (planner_prompt.txt, brain_prompt.txt, hands_prompt.txt, judge_prompt.txt)

---

## Sprint 4: Orchestration Core (Phase 3 — Critical Path)

- [x] Build Docker sandbox image (pyvis-sandbox:latest)
- [x] Implement CriticRunner (Docker sandbox execution + error classification)
- [x] Implement LoopController (state machine: PLAN→BUILD→CRITIQUE→EVALUATE→REVISE/ENRICH→COMPLETE/ESCALATE)
- [x] Implement LoopTracker (JSONL cost tracking per loop)
- [x] Implement SkillManager + SkillValidator (verified/candidate split, 4-condition check)
- [x] Implement SessionManager

---

## Sprint 5: Orchestration Extensions (Phase 3 — Non-Critical)

- [x] Implement MCP ToolRegistry + ToolInstaller (with approval mode)
- [x] Implement KG server (FAISS CPU + FastAPI, port 8003)

---

## Sprint 6: Integration & Validation (Phase 3 → Phase 4)

### Test Suite (101 tests, all passing)
- [x] `tests/test_e2e_loop.py` — E2E loop integration (19 tests)
  - Happy path: single task PASS, multi-task PASS, planner delegation
  - REVISE path: revise-then-pass, cant-self-fix escalation
  - ESCALATION: direct verdict, consecutive fails, max_loops human escalation, brain plan revision
  - Edge cases: no-code RuntimeError, tracker switch recording, skill_manager invocation
  - Helper methods: _check_escalation, _can_self_fix, _human_escalation
- [x] `tests/test_ai_modules.py` — AI module unit tests (42 tests)
  - response_utils: message_text, parse_json_message (trailing comma, nested, fallback)
  - strip_cot: single/multi-line/multiple think blocks
  - Hands code fence regex stripping
  - SwapManagerConfig defaults (ctx_size, ngl, kv_cache, jinja_roles, fallbacks, model paths)
  - SwapManager: _ctx_size_for_role, _ngl_for_role, fallback logic, health check
  - ModelRole enum
  - Judge._parse (valid JSON, code fence, invalid/empty/None → ESCALATE)
  - Brain/Hands/Planner._call (model ensure, response extraction)
- [x] `tests/test_infra_modules.py` — Infrastructure unit tests (40 tests)
  - ToolRegistry: register, get, list, remove, overwrite, defaults
  - ToolInstaller: approval blocking, no-approval install
  - SkillValidator: 4-condition check (recurrence, generality, correctability, no-duplicate)
  - LoopTracker: start, record_switch, record_fail, get_record
  - CriticRunner._classify_error: all 8 error patterns + unknown + empty + first-match
  - CriticRunner.format_report: success/failure formatting
  - SkillManager: load_verified, _extract_keywords, evaluate_and_patch

### Scripts (created, validated on GPU hardware)
- [x] `scripts/validate_hardware.sh` — per-role VRAM measurement
- [x] `scripts/profile_swap.sh` — swap latency min/max/avg
- [x] `scripts/stress_test.py` — async stress test

### Hardware Validation Results (Sat Feb 21 2026)

**GPU Configuration:**
- GPU0: NVIDIA GeForce RTX 3060, 12288 MiB
- GPU1: NVIDIA GeForce RTX 4070 SUPER, 12282 MiB
- Total VRAM: ~24.5 GB

**Model VRAM Usage (validate_hardware.sh — 4/4 PASSED):**
| Role | Model | Load Time | VRAM GPU0 | VRAM GPU1 | Total |
|------|-------|-----------|-----------|-----------|-------|
| Planner | GLM-4.7-Flash-Q4_K_M | 72s | 10862 MiB | 11508 MiB | 22.4 GB |
| Brain | Qwen3-14B-Q5_K_M | 27s | 9132 MiB | 9404 MiB | 18.5 GB (ctx=40K) |
| Hands | Devstral-24B-Q4_K_M | 27s | 10930 MiB | 11288 MiB | 22.2 GB (ctx=80K) |
| Judge | DeepSeek-R1-14B-Q4_K_M | 19s | 7617 MiB | 6718 MiB | 14.3 GB (ctx=64K) |

**Swap Latency (profile_swap.sh — 3 cycles per role):**
| Role | Avg | Min | Max |
|------|-----|-----|-----|
| Planner | 73.87s | 71.06s | 77.09s |
| Brain | 14.73s | 7.51s | 26.57s |
| Hands | 16.15s | 8.51s | 28.61s |
| Judge | 10.98s | 5.66s | 19.78s |

**Stress Test (stress_test.py — 3 cycles, 12 swaps):**
- Success Rate: 100% (12/12)
- Avg swap time via Python ModelSwapManager: ~3s

### Completed
- [x] Run `./scripts/validate_hardware.sh all` — 4/4 PASSED
- [x] Run `./scripts/profile_swap.sh 3` — All roles profiled
- [x] Run `python3 scripts/stress_test.py --cycles 3` — 100% success
- [x] Re-download corrupted Hands model (14GB, was truncated at blk.39)

### Remaining
- [x] Memory leak detection (Valgrind, heaptrack) — No leaks found in Rust module

---

## Sprint 8: MCP + LLM Tool Calling Integration

### Implementation

| 파일 | 역할 | 상태 |
|------|------|------|
| `mcp_client.py` | MCP 프로토콜 통신 (stdio JSON-RPC) | ✅ 완료 |
| `mcp_registry.py` | Registry 탐색, 설치 명령 | ✅ 완료 |
| `tool_adapter.py` | OpenAI function calling 변환 | ✅ 완료 |

### 검증된 기능

```
┌─────────────────────────────────────────────────────────────────┐
│  1. MCP Tools → OpenAI Schema 변환                              │
│     MCPToolAdapter.get_tools_schema()                           │
│     → 14개 tools를 function schema로 변환                       │
│                                                                 │
│  2. LLM tool_calls                                              │
│     llama-server가 tools 파라미터 지원                          │
│     → LLM이 함수 호출 반환                                      │
│                                                                 │
│  3. MCP Tool 실행                                               │
│     adapter.execute_tool_calls()                                │
│     → list_directory, read_file 등 실행 성공                    │
│                                                                 │
│  4. Tool Calling Loop                                           │
│     ToolEnabledLLM.call_with_tools()                            │
│     → Tool 호출 → 실행 → 결과 전달 → 최종 응답                  │
│                                                                 │
│  5. Hands 통합                                                   │
│     Hands(tool_adapter=adapter)                                 │
│     → 코드 생성 중 MCP tools 사용 가능                          │
└─────────────────────────────────────────────────────────────────┘
```

### E2E 테스트 결과

| 항목 | 상태 |
|------|------|
| MCP Registry 탐색 | ✅ PASS |
| MCP Client 통신 | ✅ PASS (14 tools) |
| LLM tool_calls | ✅ PASS |
| MCP tool 실행 | ✅ PASS |
| Tool 결과 전달 | ✅ PASS |
| Skill.md 로드 | ✅ PASS |
| **All Tests** | **131/131 PASS** |

---

## Sprint 7: Request Processing Pipeline (Phase 3.5)

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        사용자 요청                              │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    RequestAnalyzer (Brain)                      │
│  1. 난이도 분석 (Simple vs Complex)                             │
│  2. 필요한 정보 확인 → 역질문 생성                              │
│  3. 필요한 도구 확인 → 도구 요청                                │
└────────────────────────────┬────────────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
         ▼                   ▼                   ▼
    ┌─────────┐      ┌─────────────┐      ┌─────────────┐
    │ Simple  │      │ 정보 부족    │      │   Complex   │
    │ Path    │      │ 역질문      │      │    Path     │
    └────┬────┘      └──────┬──────┘      └──────┬──────┘
         │                  │                    │
         ▼                  ▼                    ▼
    Brain 직접         사용자 응답 대기      Planner → Full Loop
    처리                    │                    │
         │                  │                    ▼
         │                  │              도구 필요?
         │                  │                    │
         │                  │             ┌──────┴──────┐
         │                  │            YES           NO
         │                  │             │             │
         │                  │             ▼             ▼
         │                  │        도구 요청     작업 계속
         │                  │             │
         │                  │             ▼
         │                  │        사용자 승인
         │                  │             │
         │                  │             ▼
         │                  │        ToolInstaller
         │                  │             │
         └──────────────────┴─────────────┘
                                   │
                                   ▼
                        ┌─────────────────────┐
                        │    FileWriter       │
                        │  파일 시스템 저장   │
                        └─────────────────────┘
                                   │
                                   ▼
                        /pyvis_memory/workspace/{project_id}/
```

### Implementation Checklist

#### 7.1 RequestAnalyzer Module
- [x] Create `pyvis/orchestration/request_analyzer.py`
  - [x] `TaskComplexity` enum (SIMPLE, COMPLEX)
  - [x] `ToolStatus` enum (NOT_NEEDED, NEEDED_APPROVED, NEEDED_PENDING)
  - [x] `AnalysisResult` dataclass
  - [x] `RequestAnalyzer` class
    - [x] `analyze(user_request, available_tools)` → AnalysisResult
    - [x] `handle_simple_task(user_request)` → dict

#### 7.2 WorkspaceManager & FileWriter
- [x] Create `pyvis/execution/file_writer.py`
  - [x] `WorkspaceManager` class
    - [x] `create_project(structure)` → Path
    - [x] `write_file(relative_path, content)` → Path
    - [x] `read_file(relative_path)` → str | None
    - [x] `list_files(pattern)` → list[Path]
  - [x] `FileWriter` class
    - [x] `save_code(file_path, code)` → dict
    - [x] `save_multiple(files)` → list[dict]

#### 7.3 SessionManager Enhancement
- [x] Update `pyvis/orchestration/session_manager.py`
  - [x] Integrate RequestAnalyzer
  - [x] Add Simple Path handling
  - [x] Add Complex Path handling
  - [x] Add clarification loop (역질문)
  - [x] Add tool request/approval flow
  - [x] Integrate FileWriter for output
  - [x] Add MCP/Skill discovery integration

#### 7.4 LoopController Enhancement
- [x] Update `pyvis/orchestration/loop_controller.py`
  - [x] Add FileWriter integration
  - [x] Add file_path handling per task
  - [x] Save generated code to workspace
  - [x] Track created files in context

#### 7.5 Tests
- [x] Create `tests/test_request_analyzer.py`
- [x] Create `tests/test_file_writer.py`
- [x] Update `tests/test_e2e_loop.py`
- [x] All 131 tests passing

#### 7.6 MCP/Skill Auto-Discovery
- [x] Create `pyvis/mcp/mcp_registry.py`
  - [x] `MCPRegistryExplorer` — Search MCP servers from official registry
  - [x] `MCPToolDiscovery` — Discover skills from various sources
  - [x] `install_server()` — Auto-install MCP servers
  - [x] Integration with SessionManager

### Official MCP Sources

| Source | URL | Description |
|--------|-----|-------------|
| MCP Registry | https://registry.modelcontextprotocol.io/ | Official server registry |
| GitHub | https://github.com/modelcontextprotocol/servers | Reference implementations |
| AWS MCP | https://github.com/awslabs/mcp | AWS official servers |
| Docker MCP | https://github.com/docker/mcp-servers | Docker official servers |

### Available MCP Servers (Official)

| Server | Install Command | Use Case |
|--------|-----------------|----------|
| filesystem | `npx @modelcontextprotocol/server-filesystem` | File operations |
| git | `npx @modelcontextprotocol/server-git` | Git operations |
| github | `npx @modelcontextprotocol/server-github` | GitHub API |
| fetch | `npx @modelcontextprotocol/server-fetch` | Web content fetching |
| brave-search | `npx @modelcontextprotocol/server-brave-search` | Web search |
| slack | `npx @modelcontextprotocol/server-slack` | Slack integration |
| google-maps | `npx @modelcontextprotocol/server-google-maps` | Maps API |
| memory | `npx @modelcontextprotocol/server-memory` | Knowledge graph |
| puppeteer | `npx @modelcontextprotocol/server-puppeteer` | Browser automation |

### Path Decision Matrix

| Condition | Path | Handler |
|-----------|------|---------|
| Simple + No tools needed | Fast Path | Brain handles directly |
| Simple + Tools needed | Fast Path + Tool Install | Brain + ToolInstaller |
| Complex + Info sufficient | Full Loop | Planner → Hands → ... |
| Complex + Info insufficient | Clarification | Brain asks questions |
| Complex + Tools needed | Tool Request | Ask user → Install → Full Loop |

### Output Structure

```
/pyvis_memory/workspace/
├── project_20260221_120000/
│   ├── src/
│   │   ├── main.py
│   │   ├── utils.py
│   │   └── config.py
│   ├── tests/
│   │   └── test_main.py
│   ├── requirements.txt
│   └── README.md
└── project_20260221_130000/
    └── ...
```

---

## Sprint 9: Feedback Loop Enhancement

### Problem
피드백 루프에서 revise() 호출 시 Judge 평가 결과와 PASS 기준이 전달되지 않아 코드 수정이 비효율적이었다.

### Changes

#### LoopContext
- [x] Add `judge_result: dict` field to store Judge evaluation

#### LoopController
- [x] Store judge result in EVALUATE step
- [x] Pass judge_result + pass_criteria + skill_context to hands.revise()

#### Hands.revise()
- [x] Add parameters: judge_result, pass_criteria, skill_context
- [x] Include Judge feedback in prompt (verdict, score, reason)
- [x] Include PASS criteria explicitly
- [x] Include Skill guidelines
- [x] Use `_call_with_tools()` for MCP tool access

### Before vs After

| Aspect | Before | After |
|--------|--------|-------|
| Judge feedback | ❌ 없음 | ✅ verdict, score, reason |
| PASS criteria | ❌ 없음 | ✅ 명시적 기준 목록 |
| Skill context | ❌ 없음 | ✅ 관련 스킬 가이드라인 |
| MCP tools | ❌ _call() 사용 | ✅ _call_with_tools() 사용 |
| File path | ❌ 없음 | ✅ task에서 추출 |

### Test Results
- [x] All 131 tests passing

---

## Sprint 10: Tool Discovery Logic Enhancement

### Problem
기존 로직은 항상 Registry 검색을 먼저 시도. 이미 설치된 도구가 있어도 외부 검색부터 함.

### Solution
Brain이 사용 가능한 도구 목록을 받아 지능적으로 판단.

### Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    개선된 도구 탐색 플로우                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. SessionManager.get_available_tools()                        │
│     ├── MCP: filesystem:read_file, filesystem:write_file, ...  │
│     └── Skills: skill:code_generator, skill:test_skill, ...    │
│                                                                 │
│  2. RequestAnalyzer.analyze(request, available_tools)           │
│     → Brain에게 도구 목록과 요청을 함께 전달                    │
│                                                                 │
│  3. Brain 판단:                                                 │
│     ├── tool_status = NOT_NEEDED                                │
│     │   └── 도구 없이 진행                                      │
│     ├── tool_status = ALREADY_AVAILABLE                         │
│     │   └── available_tools_to_use = [...]                      │
│     │   └── 바로 작업 진행                                       │
│     └── tool_status = NEEDED_PENDING                            │
│         └── required_tools = [...]                              │
│         └── Registry 검색 → 설치 요청                           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Changes

| 파일 | 변경 내용 |
|------|-----------|
| `request_analyzer.py` | ToolStatus.ALREADY_AVAILABLE 추가, available_tools_to_use 필드 추가 |
| `session_manager.py` | get_available_tools() 추가, Brain 중심 로직으로 단순화 |

### Test Results
- [x] All 131 tests passing

---

## Future Enhancements (Planned)

### 단기 (현재 구현됨)
- ✅ Planner가 단일 계획 생성
- ✅ Judge가 PASS/REVISE/ESCALATE 판정
- ✅ Brain이 에스컬레이션 처리

### 중기 (ToT - Tree of Thoughts)

Planner가 복수 경로를 평가 후 선택:

```python
# Planner가 방법 A, B, C를 각각 생성하고
# Judge가 가장 유망한 경로를 선택
# 실패 시 다음 경로로 백트래킹

class ToTPlanner:
    async def generate_branches(self, task: str, n: int = 3) -> list[Plan]:
        """Generate multiple plan candidates."""
        branches = []
        for i in range(n):
            plan = await self.plan(task, temperature=0.3 + i*0.2)
            branches.append(plan)
        return branches
    
    async def evaluate_branches(self, branches: list[Plan]) -> tuple[Plan, list[Plan]]:
        """Judge evaluates each branch, return best + fallbacks."""
        scores = []
        for plan in branches:
            score = await self.judge.evaluate_plan(plan)
            scores.append((score, plan))
        scores.sort(reverse=True)
        return scores[0][1], [p for _, p in scores[1:]]

# Loop:
# 1. planner.generate_branches(task, n=3) → [plan_a, plan_b, plan_c]
# 2. judge.evaluate_branches(branches) → (best_plan, fallbacks)
# 3. execute(best_plan)
# 4. if fail: backtrack to fallback[0]
```

**구현 필요 사항:**
- [ ] ToTPlanner 클래스
- [ ] Judge.evaluate_plan() 메서드
- [ ] 백트래킹 로직
- [ ] 컨텍스트 내 복수 계획 유지

### 장기 (ReAct Tree)

서브골 트리 자동 구성:

```
                    [Main Goal]
                    /    |    \
              [Sub1]  [Sub2]  [Sub3]
               /  \      |       
          [Sub1a][Sub1b][Sub2a]   
```

```python
class ReActTree:
    """ReAct-style reasoning tree for complex tasks."""
    
    async def build_tree(self, goal: str) -> TreeNode:
        root = TreeNode(goal=goal)
        
        while not root.is_complete():
            # ReAct: Reason → Act → Observe
            thought = await self.reason(root.current_context())
            action = await self.decide_action(thought)
            result = await self.execute(action)
            observation = self.observe(result)
            
            if observation.success:
                root.advance()
            else:
                root.backtrack()
        
        return root
```

**구현 필요 사항:**
- [ ] TreeNode 클래스
- [ ] ReAct 추론 루프
- [ ] 서브골 분해 로직
- [ ] 트리 기반 컨텍스트 관리

### Planner CoT (Chain of Thought) 구조

현재 Planner 프롬프트 개선:

```
[Current]
User: "Create a web scraper"
Planner: {"plan": "...", "todo_list": [...]}

[Enhanced with CoT]
User: "Create a web scraper"
Planner:
  1. [THOUGHT] What are the requirements?
     - Need to scrape HTML
     - Store data in JSON
     - Handle pagination
  
  2. [REASONING] 
     - Use requests + BeautifulSoup
     - Modular design for extensibility
  
  3. [PLAN]
     - file_structure: [scraper/main.py, scraper/parsers.py]
     - todo_list: [...]
```

**구현 필요 사항:**
- [ ] Planner 프롬프트에 CoT 구조 추가
- [ ] reasoning_output 필드 분리
- [ ] Judge가 reasoning도 평가

---

## Phase 5+ (Reserved)

- [ ] Interface layer: Audio (STT/TTS via Whisper), Vision (screen capture), Telegram Bot
- [ ] Web service expansion

---

## Code Review & Fixes (Post-Implementation)

### Rust Core — All Fixed
- [x] C1: Removed stale `model_group()`, every role switch now sets `requires_server_restart`
- [x] C2: Added `Planner` to `ModelRole` enum + `switch_to_planner()` + updated tests
- [x] Removed `requires_kv_reset` from `SwitchResult` (server restart handles KV reset)
- [x] Added `display_name()` method with role-to-model mapping
- [x] Removed unused `rayon` dependency
- [x] Cleaned up unused `pub use` re-exports in mod.rs files
- [x] Suppressed PyO3 `non_local_definitions` warnings
- [x] Updated `pyvis_core.pyi` type stub (2-tuple, added `switch_to_planner`)
- [x] Zero cargo warnings, 8/8 tests passing

### Python / Shell — All Fixed
- [x] C4: Dockerfile — moved `pip install` before `USER sandbox` so it runs as root
- [x] I1: critic_runner.py — stdout/stderr now fetched separately (not combined)
- [x] I2: critic_runner.py — container cleanup moved to `finally` (no leak on timeout)
- [x] I4: swap_manager.py — reuse single httpx.AsyncClient for health checks
- [x] I6: skill_validator.py — implemented all 4 conditions from spec (recurrence, generality, correctability, no-duplicate)
- [x] I7: Removed duplicate `system/` prompt dir (identical to `pyvis/ai/prompts/`)
- [x] I8: start_model.sh — health check now checks `"status":"ok"` not just `"status"`
- [x] M3: hands.py — replaced fragile `.replace()` with regex code fence stripping
- [x] M4: main.py — added SIGINT/SIGTERM handler for clean shutdown

---

## Documentation

- [x] Fix YAML indentation in unified_node.yaml
- [x] Fix Hands model path in start_model.sh
- [x] Add per-role config fields to unified_node.yaml
- [x] Update swap_manager.py (model path, fallback logic, config fields)
- [x] Update start_model.sh (ctx variables, translate Korean comments)
- [x] Translate TODO.md (Korean → English)
- [x] Translate Pyvis_v4.md (Korean → English, fix stale architecture references)
- [x] Cross-file consistency QA
- [x] Documentation accuracy QA
