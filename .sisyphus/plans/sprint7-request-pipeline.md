# Sprint 7: Request Processing Pipeline

## TL;DR

> **Quick Summary**: 사용자 요청 처리 파이프라인 개선 — 난이도 분기, 역질문, 도구 요청, 파일 저장 기능 구현
> 
> **Deliverables**:
> - RequestAnalyzer 모듈 (난이도 분석, 역질문, 도구 필요성 판단)
> - WorkspaceManager & FileWriter 모듈 (파일 저장)
> - SessionManager 개선 (분기 처리)
> - LoopController 개선 (파일 저장 연동)
> 
> **Estimated Effort**: High
> **Parallel Execution**: YES — 3 waves

---

## Context

### Current Problem
현재 구현의 문제점:
1. **파일 저장 없음**: Hands가 생성한 코드가 메모리에만 저장되고 실제 파일로 저장되지 않음
2. **난이도 분기 없음**: 모든 요청이 동일한 Full Loop를 거침
3. **역질문 없음**: 정보 부족 시 사용자에게 질문하지 않음
4. **도구 요청 없음**: 필요한 도구를 자동으로 요청/설치하지 않음

### Architecture

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

### Path Decision Matrix

| Condition | Path | Handler |
|-----------|------|---------|
| Simple + No tools needed | Fast Path | Brain handles directly |
| Simple + Tools needed | Fast Path + Tool Install | Brain + ToolInstaller |
| Complex + Info sufficient | Full Loop | Planner → Hands → ... |
| Complex + Info insufficient | Clarification | Brain asks questions |
| Complex + Tools needed | Tool Request | Ask user → Install → Full Loop |

---

## Work Objectives

### Must Have
1. RequestAnalyzer 모듈 — 난이도 분석, 역질문, 도구 필요성
2. WorkspaceManager & FileWriter — 실제 파일 저장
3. SessionManager 분기 처리 — Simple/Complex Path
4. LoopController 파일 저장 연동

### Must NOT Have (Guardrails)
- 기존 AI 역할 로직 수정 금지 (brain.py, hands.py, judge.py, planner.py)
- 모델 스왑 아키텍처 변경 금지
- Docker 샌드박스 보안 설정 변경 금지

---

## TODOs

### Wave 1 — Core Modules (parallel)

- [ ] 1. Create WorkspaceManager & FileWriter
  - **File**: `pyvis/execution/file_writer.py`
  - **Classes**:
    - `WorkspaceManager`: 프로젝트 디렉토리 관리
    - `FileWriter`: 파일 저장/읽기
  - **Methods**:
    - `create_project(structure)` → Path
    - `write_file(relative_path, content)` → Path
    - `read_file(relative_path)` → str | None
    - `save_code(file_path, code)` → dict
    - `save_multiple(files)` → list[dict]
  - **Output**: `/pyvis_memory/workspace/{project_id}/`

- [ ] 2. Create RequestAnalyzer
  - **File**: `pyvis/orchestration/request_analyzer.py`
  - **Classes**:
    - `TaskComplexity` enum (SIMPLE, COMPLEX)
    - `ToolStatus` enum (NOT_NEEDED, NEEDED_APPROVED, NEEDED_PENDING)
    - `AnalysisResult` dataclass
    - `RequestAnalyzer` class
  - **Methods**:
    - `analyze(user_request, available_tools)` → AnalysisResult
    - `handle_simple_task(user_request)` → dict
  - **Logic**:
    - Simple: 단일 파일, 5분 이내, 명확한 스펙
    - Complex: 다중 파일, 아키텍처 설계, 의존성 분석

- [ ] 3. Update Brain prompt for clarification
  - **File**: `pyvis/ai/prompts/brain_prompt.txt`
  - **Add**: 역질문 생성 가이드라인

### Wave 2 — Integration (sequential after Wave 1)

- [ ] 4. Update SessionManager
  - **File**: `pyvis/orchestration/session_manager.py`
  - **Changes**:
    - RequestAnalyzer 통합
    - Simple Path 처리
    - Complex Path 처리
    - 역질문 루프
    - 도구 요청/승인 플로우
    - FileWriter 통합

- [ ] 5. Update LoopController
  - **File**: `pyvis/orchestration/loop_controller.py`
  - **Changes**:
    - FileWriter 연동
    - file_path 처리
    - 생성 코드 저장
    - 파일 목록 추적

### Wave 3 — Tests & Documentation

- [ ] 6. Create tests
  - **File**: `tests/test_request_analyzer.py`
  - **File**: `tests/test_file_writer.py`
  - **Scenarios**:
    - 난이도 분류 테스트
    - 역질문 생성 테스트
    - 도구 필요성 감지
    - 파일 저장 테스트

- [ ] 7. Update TODO.md
  - Add Sprint 7 section with architecture and checklist

---

## Success Criteria

```bash
# RequestAnalyzer test
python3 -c "
from pyvis.orchestration.request_analyzer import RequestAnalyzer, TaskComplexity
# ... test logic
"

# FileWriter test
python3 -c "
from pyvis.execution.file_writer import WorkspaceManager, FileWriter
ws = WorkspaceManager('test_project')
ws.create_project(['src/main.py', 'requirements.txt'])
ws.write_file('src/main.py', 'print(\"hello\")')
print(ws.get_project_info())
"
```

### Final Checklist
- [ ] RequestAnalyzer가 Simple/Complex 구분
- [ ] 역질문 생성 기능 작동
- [ ] 도구 필요성 판단 기능 작동
- [ ] FileWriter가 실제 파일 저장
- [ ] SessionManager 분기 처리 작동
- [ ] 테스트 통과
