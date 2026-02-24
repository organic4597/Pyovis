# Docs Refresh + Role-Based Context Separation

## TL;DR

> **Quick Summary**: Refresh all project documentation to reflect the current PYVIS v4.0 state, then implement per-role `ctx_size` values with multi-level fallback policies across three config/code files.
> 
> **Deliverables**:
> - Updated `Pyvis_v4.md` design spec reflecting current architecture
> - Updated `TODO.md` reflecting completed/pending items
> - New `ai:` section in `unified_node.yaml` with per-role ctx_size + fallback values
> - Updated `swap_manager.py` with `ctx_size_brain`, fallback fields, and fallback cascade logic in `_ctx_size_for_role()`
> - Updated `start_model.sh` with new ctx_size values matching the config
> - Fixed YAML indentation bug in `unified_node.yaml` (hands/judge misaligned under model_swap)
> - Fixed Hands model discrepancy: `start_model.sh` references Qwen3-14B but config/docs reference Devstral-24B
> 
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 3 waves
> **Critical Path**: Task 1 (YAML fix) → Task 5 (unified_node.yaml ai section) → Task 6 (swap_manager.py) → Task 7 (start_model.sh) → Task 9 (cross-file QA)

---

## Context

### Current Status (2026-02-21)
- **Wave 1–3 tasks completed**: Tasks 1–9 are done (docs refresh, config updates, QA).
- **Final verification completed**: F1–F3 approved with evidence saved in `.sisyphus/evidence/`.
- **Evidence present**: task-1, task-4, task-8, task-9, and F1–F3 reports plus translation parts.
- **Note on ctx_size values**: Final chosen values are 65536/114688/57344/32768 (planner/brain/hands/judge),
  used consistently across all three config files. No fallback chains.
- **Brain KV cache**: q4_0 (all other roles: q8_0). Saves VRAM for Brain's large 114K context.
- **KV quantization**: Brain=q4_0, others=q8_0. No q4_0 for Hands/Planner/Judge.
- **Token downscale / fallback cascade**: REMOVED. No fallback ctx_size logic anywhere.

### Original Request
User wants to: (1) refresh all existing project documentation to reflect the current PYVIS v4.0 state, and (2) implement role-based `ctx_size` separation with multi-level fallback policies. Plan and todo documents must be `.md` files. All documentation in English.

### Interview Summary
**Key Discussions**:
- **Doc scope**: All project docs + vendored llama.cpp docs. Exclude `.venv` package docs.
- **Config scope**: `unified_node.yaml` + `swap_manager.py` + `start_model.sh` — all three must stay synchronized.
- **Test strategy**: No automated tests (doc/config changes). QA scenarios only.
- **Language**: English for all documentation.
- **Role-based ctx_size values** (user-proposed):
  - planner: 32768 (fallback: 16384)
  - brain: 32768 (fallback: 16384)
  - hands: 58368 (fallback_1: 40960, fallback_2: 32768)
  - judge: 16384 (fallback: 8192)

### Research Findings
- **Current ctx_size values** (from code):
  - planner: 81920, brain: 114688 (default), hands: 40960, judge: 81920
- **YAML indentation bug**: `unified_node.yaml` lines 78-89 — `hands:` and `judge:` role configs are misaligned. `hands:` is at the same level as `ai:` (should be nested under `ai:`), and `judge:` is nested under `hands:` (should be a sibling).
- **Hands model discrepancy**: `start_model.sh` line 27 sets `HANDS_MODEL` to Qwen3-14B (same as brain), but `unified_node.yaml` line 49 and design docs reference Devstral-24B.
- **No fallback mechanism exists**: `_ctx_size_for_role()` (L289-296) is a simple dispatch with no fallback logic.
- **No `ctx_size_brain` field**: `SwapManagerConfig` has `ctx_size_planner`, `ctx_size_judge`, `ctx_size_hands` but brain uses the generic `ctx_size` default.
- **KV cache**: q8_0 for both k and v across all configs.

---

## Work Objectives

### Core Objective
Bring all project documentation up to date with the current PYVIS v4.0 implementation, then add per-role context window sizing with graceful fallback policies so the system can degrade memory usage when VRAM is constrained.

### Concrete Deliverables
- `Pyvis_v4.md` — updated design spec reflecting actual architecture
- `TODO.md` — updated checklist with completed items checked off
- `config/unified_node.yaml` — fixed indentation + new per-role ctx_size section with fallbacks
- `pyvis/ai/swap_manager.py` — new config fields + fallback cascade in `_ctx_size_for_role()`
- `scripts/start_model.sh` — updated ctx variables + Hands model path fix

### Definition of Done
- [ ] All three config files have consistent per-role ctx_size values
- [ ] `_ctx_size_for_role()` implements fallback cascade logic
- [ ] `unified_node.yaml` YAML is valid (no indentation errors)
- [ ] `start_model.sh` Hands model path matches `unified_node.yaml`
- [ ] `Pyvis_v4.md` reflects current architecture
- [ ] `TODO.md` reflects current completion state

### Must Have
- Per-role ctx_size values with fallback chains across all 3 config files
- Fallback cascade logic in `_ctx_size_for_role()` (try primary → fallback_1 → fallback_2)
- Fix YAML indentation bug (hands/judge under ai section)
- Fix Hands model path discrepancy in start_model.sh
- Updated documentation in English

### Must NOT Have (Guardrails)
- Do NOT change model file paths in `unified_node.yaml` (those are correct)
- Do NOT modify any AI role behavior logic (brain.py, hands.py, judge.py, planner.py)
- Do NOT change tensor_split, n_gpu_layers, cache_type, or other server params unless directly related to ctx_size
- Do NOT add automated unit tests (QA scenarios only)
- Do NOT modify `.venv` package docs
- Do NOT change the single-server swap architecture
- Do NOT translate Korean comments in code files — only documentation files get English treatment

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: NO (not relevant — config/doc changes)
- **Automated tests**: None
- **Framework**: N/A

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

---

## Role Context Guidance (Planner / Brain / Hands)

### Planner (GLM)
- Produce a **full file list + build order + dependency graph** before execution.
- Explicitly answer: **"Which files are created in which order?"**

### Brain (Qwen3-14B)
- **Extract only required symbols** before writing: function signatures, variable types,
  struct/header definitions.
- Summarize those symbols for Hands (avoid full-file dumps).

### Hands (Devstral)
- **Focus on a single active file** at a time.
- Context package format:
  ```
  ┌─────────────────────────────┐
  │ Current file plan (1K~2K)   │
  │ Dependency symbol summary   │
  │ (1K~3K; not whole files)    │
  │ In-progress file content    │
  │ Error log (within loop)     │
  └─────────────────────────────┘
  ```
- Target actual usage: **10K–20K context**, leaving **~37K headroom** (total ctx: 57344).
- **ctx_size fixed at 57344 (56K). No fallback. No downscale.**
- **KV quantization: q8_0 (fixed, no q4_0).**

### Brain → Hands Dependency Symbol Summary Format

Brain extracts only required symbols before handing off to Hands.
**Never pass full files** — use the compact symbol summary format below:

```markdown
## Dependency Symbols (file: db/session.py)
- get_db() -> Generator[Session, None, None]
  Role: Provides DB session, used via FastAPI Depends

## Dependency Symbols (file: models/user.py)
- class User(Base)
  Fields: id: int, email: str, hashed_password: str
  Relations: tasks: List[Task]

## Dependency Symbols (file: core/security.py)
- verify_password(plain: str, hashed: str) -> bool
- get_password_hash(password: str) -> str
```

**Token savings**: Full file injection (~4K tokens) vs symbol summary (~300 tokens) → **~90% reduction**.
**File isolation**: After task completion, clear context — prevents cross-file contamination.

- **Config files**: Use Bash — `python -c "import yaml; yaml.safe_load(open(...))"` to validate YAML, grep to verify values
- **Python code**: Use Bash — `python -c "from pyvis.ai.swap_manager import SwapManagerConfig; ..."` to verify dataclass
- **Shell script**: Use Bash — `bash -n scripts/start_model.sh` for syntax check, grep for values
- **Documentation**: Use Bash — verify file exists, check line count, grep for key sections

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately — fixes + doc refresh, independent tasks):
├── Task 1: Fix unified_node.yaml indentation bug [quick]
├── Task 2: Update Pyvis_v4.md design spec [writing]
├── Task 3: Update TODO.md checklist [quick]
└── Task 4: Fix start_model.sh Hands model discrepancy [quick]

Wave 2 (After Wave 1 — role-based ctx_size implementation):
├── Task 5: Add per-role ctx_size + fallback section to unified_node.yaml (depends: 1) [unspecified-high]
├── Task 6: Update swap_manager.py config + fallback logic (depends: 5) [deep]
└── Task 7: Update start_model.sh ctx values (depends: 5) [quick]

Wave 3 (After Wave 2 — verification):
├── Task 8: Cross-file consistency QA (depends: 5, 6, 7) [unspecified-high]
└── Task 9: Documentation accuracy QA (depends: 2, 3) [unspecified-high]

Wave FINAL (After ALL tasks — independent review):
├── Task F1: Plan compliance audit [oracle]
├── Task F2: Code quality review [unspecified-high]
└── Task F3: Scope fidelity check [deep]

Critical Path: Task 1 → Task 5 → Task 6 → Task 7 → Task 8 → F1-F3
Parallel Speedup: ~50% faster than sequential
Max Concurrent: 4 (Wave 1)
```

### Dependency Matrix

| Task | Depends On | Blocks |
|------|-----------|--------|
| 1 | — | 5 |
| 2 | — | 9 |
| 3 | — | 9 |
| 4 | — | 7, 8 |
| 5 | 1 | 6, 7, 8 |
| 6 | 5 | 8 |
| 7 | 4, 5 | 8 |
| 8 | 5, 6, 7 | F1-F3 |
| 9 | 2, 3 | F1-F3 |
| F1-F3 | 8, 9 | — |

### Agent Dispatch Summary

- **Wave 1**: **4 tasks** — T1 → `quick`, T2 → `writing`, T3 → `quick`, T4 → `quick`
- **Wave 2**: **3 tasks** — T5 → `unspecified-high`, T6 → `deep`, T7 → `quick`
- **Wave 3**: **2 tasks** — T8 → `unspecified-high`, T9 → `unspecified-high`
- **FINAL**: **3 tasks** — F1 → `oracle`, F2 → `unspecified-high`, F3 → `deep`

---

## TODOs

### Wave 1 — Fixes + Doc Refresh (all parallel, no dependencies)

- [ ] 1. Fix unified_node.yaml indentation bug (hands/judge under ai section)

  **What to do**:
  - Fix the YAML structure at lines 64-89 in `config/unified_node.yaml`
  - Currently `hands:` (line 78) is at the same indentation level as `ai:` (line 64) — it should be nested UNDER `ai:`
  - Currently `judge:` (line 83) is nested under `hands:` — it should be a SIBLING of `hands:` under `ai:`
  - The correct structure should be:
    ```yaml
    model_swap:
      ...
      ai:
        base_url: "http://localhost:8001"
        planner:
          ...
        brain:
          ...
        hands:
          ...
        judge:
          ...
    ```
  - Verify the fixed YAML parses correctly

  **Must NOT do**:
  - Do NOT change any values — only fix indentation/nesting
  - Do NOT add new fields (ctx_size comes in Task 5)
  - Do NOT modify any sections outside lines 64-89

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []
    - No specialized skills needed — simple indentation fix

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3, 4)
  - **Blocks**: Task 5 (needs correct YAML structure before adding ctx_size)
  - **Blocked By**: None

  **References**:
  - `config/unified_node.yaml:64-89` — The broken section. Lines 64-77 (ai→planner→brain) are correct. Lines 78-89 (hands→judge) have wrong indentation.
  - `config/unified_node.yaml:1-63` — Rest of file for context on indentation style (2-space indent throughout).

  **Acceptance Criteria**:

  ```
  Scenario: YAML parses successfully after fix
    Tool: Bash
    Steps:
      1. python3 -c "import yaml; data = yaml.safe_load(open('/Pyvis/config/unified_node.yaml')); print(yaml.dump(data['model_swap']['ai'], default_flow_style=False))"
      2. Verify output contains keys: base_url, planner, brain, hands, judge — all at same level
    Expected Result: All 5 keys present as direct children of model_swap.ai
    Failure Indicators: KeyError for 'hands' or 'judge' under model_swap.ai, or YAML parse error
    Evidence: .sisyphus/evidence/task-1-yaml-parse.txt

  Scenario: Judge is NOT nested under hands
    Tool: Bash
    Steps:
      1. python3 -c "import yaml; data = yaml.safe_load(open('/Pyvis/config/unified_node.yaml')); hands = data['model_swap']['ai']['hands']; assert 'judge' not in (hands or {}), 'judge is still nested under hands!'; print('OK: judge is correctly separated')"
    Expected Result: "OK: judge is correctly separated"
    Failure Indicators: AssertionError about judge nested under hands
    Evidence: .sisyphus/evidence/task-1-judge-separation.txt
  ```

  **Commit**: YES
  - Message: `fix(config): correct YAML indentation for hands/judge role configs`
  - Files: `config/unified_node.yaml`
  - Pre-commit: `python3 -c "import yaml; yaml.safe_load(open('/Pyvis/config/unified_node.yaml'))"`

- [ ] 2. Update Pyvis_v4.md design spec

  **What to do**:
  - Read the entire `Pyvis_v4.md` (1939 lines, Korean design spec)
  - Update it to reflect the current PYVIS v4.0 architecture:
    - Current model assignments (GLM-4.7-Flash for planner, Qwen3-14B for brain, Devstral-24B for hands, DeepSeek-R1-Distill for judge)
    - Current hardware config (dual GPU: RTX 4070 SUPER + RTX 3060, 12GB each)
    - Single-server swap architecture (not multi-server)
    - Role-based context separation (document the new per-role ctx_size values and fallback policy — reference upcoming config changes)
  - Translate any sections that are still in Korean to English (entire doc should be English per user requirement)
  - Preserve the overall document structure; update content within existing sections

  **Must NOT do**:
  - Do NOT delete sections — update them
  - Do NOT change the fundamental architectural decisions described
  - Do NOT add implementation details that don't exist yet (the ctx_size fallback is being implemented in this plan, so document it as "designed" not "implemented")

  **Recommended Agent Profile**:
  - **Category**: `writing`
  - **Skills**: []
    - Writing category handles documentation tasks. No specialized skills needed.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3, 4)
  - **Blocks**: Task 9 (documentation QA)
  - **Blocked By**: None

  **References**:
  - `Pyvis_v4.md` — The file to update. Read entire file for structure understanding.
  - `config/unified_node.yaml` — Source of truth for current config values.
  - `pyvis/ai/swap_manager.py:48-78` — SwapManagerConfig showing current defaults.
  - `scripts/start_model.sh:25-42` — Current model paths and ctx values.

  **Acceptance Criteria**:

  ```
  Scenario: Document exists and is non-empty
    Tool: Bash
    Steps:
      1. wc -l /Pyvis/Pyvis_v4.md
      2. Verify line count is >= 500 (substantial document, not accidentally truncated)
    Expected Result: Line count >= 500
    Failure Indicators: File missing or line count < 500
    Evidence: .sisyphus/evidence/task-2-doc-exists.txt

  Scenario: Key architecture sections present
    Tool: Bash
    Steps:
      1. grep -c "ctx_size\|context.*size\|fallback" /Pyvis/Pyvis_v4.md
      2. grep -c "RTX 4070\|RTX 3060\|dual.*GPU\|GPU" /Pyvis/Pyvis_v4.md
      3. grep -c "Devstral\|GLM\|Qwen3\|DeepSeek" /Pyvis/Pyvis_v4.md
    Expected Result: Each grep returns >= 1 match
    Failure Indicators: Any grep returns 0 — means key topic missing from doc
    Evidence: .sisyphus/evidence/task-2-sections-check.txt

  Scenario: No Korean text remains
    Tool: Bash
    Steps:
      1. python3 -c "import re; text=open('/Pyvis/Pyvis_v4.md').read(); korean=re.findall(r'[\uac00-\ud7af]+', text); print(f'Korean strings found: {len(korean)}'); [print(f'  - {k}') for k in korean[:10]]"
    Expected Result: "Korean strings found: 0"
    Failure Indicators: Any Korean strings found
    Evidence: .sisyphus/evidence/task-2-no-korean.txt
  ```

  **Commit**: YES
  - Message: `docs: update Pyvis_v4.md design spec to reflect current v4.0 architecture`
  - Files: `Pyvis_v4.md`

- [ ] 3. Update TODO.md checklist

  **What to do**:
  - Read the entire `TODO.md` (246 lines, Korean implementation checklist)
  - Cross-reference with actual codebase to determine which items are completed
  - Check off completed items (change `- [ ]` to `- [x]`)
  - Add any new items from this planning session (role-based ctx_size implementation)
  - Translate all content to English
  - Preserve overall structure

  **Must NOT do**:
  - Do NOT remove items — only update their status
  - Do NOT add speculative future items beyond what's in this plan

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 4)
  - **Blocks**: Task 9 (documentation QA)
  - **Blocked By**: None

  **References**:
  - `TODO.md` — The file to update. Read entire file.
  - `pyvis/` directory — Cross-reference to see which modules exist (completed items).
  - `config/unified_node.yaml` — Check which config items are done.

  **Acceptance Criteria**:

  ```
  Scenario: TODO.md has both checked and unchecked items
    Tool: Bash
    Steps:
      1. grep -c "\- \[x\]" /Pyvis/TODO.md
      2. grep -c "\- \[ \]" /Pyvis/TODO.md
    Expected Result: Both counts > 0 (some done, some pending)
    Failure Indicators: All checked or all unchecked — means not properly updated
    Evidence: .sisyphus/evidence/task-3-todo-status.txt

  Scenario: No Korean text remains
    Tool: Bash
    Steps:
      1. python3 -c "import re; text=open('/Pyvis/TODO.md').read(); korean=re.findall(r'[\uac00-\ud7af]+', text); print(f'Korean strings found: {len(korean)}')"
    Expected Result: "Korean strings found: 0"
    Evidence: .sisyphus/evidence/task-3-no-korean.txt
  ```

  **Commit**: YES
  - Message: `docs: update TODO.md completion status and translate to English`
  - Files: `TODO.md`

- [ ] 4. Fix start_model.sh Hands model path discrepancy

  **What to do**:
  - In `scripts/start_model.sh` line 27, change `HANDS_MODEL` from Qwen3-14B to Devstral-24B path:
    ```bash
    # FROM:
    HANDS_MODEL="/pyvis_memory/models/Qwen3-14B-Q5_K_M.gguf"
    # TO:
    HANDS_MODEL="/pyvis_memory/models/mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf"
    ```
  - This aligns with `unified_node.yaml` line 50 and the design docs which specify Devstral for Hands
  - Also update `HANDS_NGL` from 40 to appropriate value for Devstral (40 layers, keep at 40 — matches layer count)

  **Must NOT do**:
  - Do NOT change any other model paths
  - Do NOT change ctx values here (that's Task 7)
  - Do NOT change NGL for non-hands roles

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2, 3)
  - **Blocks**: Tasks 7, 8 (need correct model path before ctx update and QA)
  - **Blocked By**: None

  **References**:
  - `scripts/start_model.sh:25-37` — Model path and NGL variables. Line 27 is the wrong HANDS_MODEL.
  - `config/unified_node.yaml:48-51` — Correct Hands model reference (Devstral path and filename).
  - `pyvis/ai/swap_manager.py:73-78` — SwapManagerConfig models dict. NOTE: swap_manager also has Qwen3-14B for hands (line 76) — this should also be flagged but is covered by Task 6.

  **Acceptance Criteria**:

  ```
  Scenario: HANDS_MODEL points to Devstral
    Tool: Bash
    Steps:
      1. grep "HANDS_MODEL=" /Pyvis/scripts/start_model.sh
    Expected Result: HANDS_MODEL="/pyvis_memory/models/mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf"
    Failure Indicators: Still contains "Qwen3-14B"
    Evidence: .sisyphus/evidence/task-4-hands-model.txt

  Scenario: Shell syntax remains valid
    Tool: Bash
    Steps:
      1. bash -n /Pyvis/scripts/start_model.sh
    Expected Result: No output (clean syntax)
    Failure Indicators: Any syntax error output
    Evidence: .sisyphus/evidence/task-4-shell-syntax.txt
  ```

  **Commit**: YES
  - Message: `fix(scripts): correct Hands model path to Devstral-24B in start_model.sh`
  - Files: `scripts/start_model.sh`
  - Pre-commit: `bash -n scripts/start_model.sh`

### Wave 2 — Role-Based ctx_size Implementation (after Wave 1)

- [ ] 5. Add per-role ctx_size + fallback section to unified_node.yaml

  **What to do**:
  - Add a new `ctx_size` block under each role in the `models:` section of `config/unified_node.yaml` (lines 39-55)
  - Structure (final values — no fallback fields):
    ```yaml
    models:
      planner:
        file: "GLM-4.7-Flash-Q4_K_M.gguf"
        path: "/pyvis_memory/models/GLM-4.7-Flash-Q4_K_M.gguf"
        size_gb: 18
        ctx_size: 81920
        n_gpu_layers: 60
        jinja: false
        fallback: "brain"
      brain:
        file: "Qwen3-14B-Q5_K_M.gguf"
        path: "/pyvis_memory/models/Qwen3-14B-Q5_K_M.gguf"
        size_gb: 10
        ctx_size: 114688
        n_gpu_layers: 60
        jinja: false
        fallback: null
      hands:
        file: "mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf"
        path: "/pyvis_memory/models/mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf"
        size_gb: 14
        ctx_size: 57344   # 56K fixed — no fallback, no downscale
        n_gpu_layers: 40
        jinja: true
        fallback: "brain"
      judge:
        file: "DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf"
        path: "/pyvis_memory/models/DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf"
        size_gb: 9
        ctx_size: 81920
        n_gpu_layers: 60
        jinja: false
        fallback: null
    ```
  - KV quantization is set globally in `server:` section: `cache_type_k: "q8_0"`, `cache_type_v: "q8_0"` (already correct).

  **Must NOT do**:
  - Do NOT change model file paths or size_gb values
  - Do NOT modify the `model_swap.ai` section (role behavior configs)
  - Do NOT change cache_type or tensor_split settings
  - Do NOT remove the global ctx_size (keep as legacy fallback)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
    - Config change with careful YAML structure. No specialized skills needed but attention to detail is critical.

  **Parallelization**:
  - **Can Run In Parallel**: NO (sequential — needs Task 1 complete first)
  - **Parallel Group**: Wave 2
  - **Blocks**: Tasks 6, 7, 8 (swap_manager and start_model need to match these values)
  - **Blocked By**: Task 1 (YAML indentation must be fixed first)

  **References**:
  - `config/unified_node.yaml:39-55` — Current models section (no ctx_size per role yet).
  - `config/unified_node.yaml:29-37` — Current server section with global ctx_size: 114688.
  - Draft requirements: planner=81920, brain=114688, hands=57344 (FIXED — no fallback), judge=81920.

  **Acceptance Criteria**:

  ```
  Scenario: Per-role ctx_size values parse correctly
    Tool: Bash
    Steps:
      1. python3 -c "
         import yaml
         data = yaml.safe_load(open('/Pyvis/config/unified_node.yaml'))
         models = data['models']
         assert models['planner']['ctx_size'] == 81920, f'planner: {models[\"planner\"][\"ctx_size\"]}'
         assert models['brain']['ctx_size'] == 114688, f'brain: {models[\"brain\"][\"ctx_size\"]}'
         assert models['hands']['ctx_size'] == 57344, f'hands: {models[\"hands\"][\"ctx_size\"]}'
         assert models['judge']['ctx_size'] == 81920, f'judge: {models[\"judge\"][\"ctx_size\"]}'
         print('All per-role ctx_size values correct')
         "
    Expected Result: "All per-role ctx_size values correct"
    Failure Indicators: AssertionError with wrong value
    Evidence: .sisyphus/evidence/task-5-ctx-values.txt

  Scenario: YAML remains valid after changes
    Tool: Bash
    Steps:
      1. python3 -c "import yaml; yaml.safe_load(open('/Pyvis/config/unified_node.yaml')); print('YAML valid')"
    Expected Result: "YAML valid"
    Evidence: .sisyphus/evidence/task-5-yaml-valid.txt
  ```

  **Commit**: YES (group with Tasks 6, 7)
  - Message: `feat(config): add per-role ctx_size with multi-level fallback`
  - Files: `config/unified_node.yaml`, `pyvis/ai/swap_manager.py`, `scripts/start_model.sh`

- [ ] 6. Update swap_manager.py config fields + fallback cascade logic

  **What to do**:
  - **Part A — Update `SwapManagerConfig` dataclass** (lines 48-78):
    - Add `ctx_size_brain: int = 114688` field (currently brain uses generic `ctx_size` default)
    - Update existing fields to final values:
      - `ctx_size: int = 114688` → keep as legacy default (unchanged)
      - `ctx_size_planner: int = 81920` (unchanged)
      - `ctx_size_judge: int = 81920` (unchanged)
      - `ctx_size_hands: int = 40960` → `ctx_size_hands: int = 57344`
    - **NO fallback fields** — token downscale / fallback cascade is removed
    - Fix Hands model path in `models` dict (line 76): change from Qwen3-14B to Devstral path

  - **Part B — Update `_ctx_size_for_role()` method** (lines 289-296):
    - Add `brain` role dispatch. No fallback logic needed — simple dispatch only:
      ```python
      def _ctx_size_for_role(self, role: ModelRole) -> int:
          if role == ModelRole.PLANNER:
              return self.config.ctx_size_planner
          if role == ModelRole.BRAIN:
              return self.config.ctx_size_brain
          if role == ModelRole.JUDGE:
              return self.config.ctx_size_judge
          if role == ModelRole.HANDS:
              return self.config.ctx_size_hands
          return self.config.ctx_size
      ```

  - **Part C — No fallback retry logic needed**: ctx_size is fixed. No server retry on VRAM failure.

  **Must NOT do**:
  - Do NOT change the overall swap architecture
  - Do NOT modify `_log_swap()` signature
  - Do NOT change port, host, threads, cache_type, tensor_split, or GPU settings
  - Do NOT modify any methods in brain.py, hands.py, judge.py, planner.py

  **Recommended Agent Profile**:
  - **Category**: `deep`
  - **Skills**: [`python-refactor`]
    - `python-refactor`: Modifying a dataclass + method logic in production Python code. Need careful refactoring with backward compatibility.

  **Parallelization**:
  - **Can Run In Parallel**: NO (needs Task 5 values as reference)
  - **Parallel Group**: Wave 2 (sequential after Task 5)
  - **Blocks**: Task 8 (cross-file QA)
  - **Blocked By**: Task 5 (YAML values must be finalized first)

  **References**:
  - `pyvis/ai/swap_manager.py:48-78` — `SwapManagerConfig` dataclass. ALL fields shown. Need to add `ctx_size_brain` and all fallback list fields.
  - `pyvis/ai/swap_manager.py:289-296` — `_ctx_size_for_role()` method. Current simple dispatch. Must be rewritten with fallback cascade.
  - `pyvis/ai/swap_manager.py:81-287` — `ModelSwapManager` class. Find `_start_server()` or equivalent to add retry-with-fallback logic.
  - `pyvis/ai/swap_manager.py:1-47` — Imports and ModelRole enum. Check how ModelRole is defined for role_map keys.
  - `config/unified_node.yaml` (after Task 5) — Source of truth for all ctx_size values.

  **Acceptance Criteria**:

  ```
  Scenario: SwapManagerConfig has all new fields with correct defaults
    Tool: Bash
    Steps:
      1. python3 -c "
         import sys; sys.path.insert(0, '/Pyvis')
         from pyvis.ai.swap_manager import SwapManagerConfig
         c = SwapManagerConfig()
         assert c.ctx_size_planner == 81920, f'planner: {c.ctx_size_planner}'
         assert c.ctx_size_brain == 114688, f'brain: {c.ctx_size_brain}'
         assert c.ctx_size_hands == 57344, f'hands: {c.ctx_size_hands}'
         assert c.ctx_size_judge == 81920, f'judge: {c.ctx_size_judge}'
         print('All config fields correct')
         "
    Expected Result: "All config fields correct"
    Failure Indicators: ImportError (syntax issue) or AssertionError (wrong value)
    Evidence: .sisyphus/evidence/task-6-config-fields.txt

  Scenario: _ctx_size_for_role returns correct values
    Tool: Bash
    Steps:
      1. python3 -c "
         import sys; sys.path.insert(0, '/Pyvis')
         from pyvis.ai.swap_manager import ModelSwapManager, SwapManagerConfig
         mgr = ModelSwapManager.__new__(ModelSwapManager)
         mgr.config = SwapManagerConfig()
         from pyvis.ai.swap_manager import ModelRole
         assert mgr._ctx_size_for_role(ModelRole.PLANNER) == 81920
         assert mgr._ctx_size_for_role(ModelRole.BRAIN) == 114688
         assert mgr._ctx_size_for_role(ModelRole.HANDS) == 57344
         assert mgr._ctx_size_for_role(ModelRole.JUDGE) == 81920
         print('ctx_size dispatch correct')
         "
    Expected Result: "ctx_size dispatch correct"
    Evidence: .sisyphus/evidence/task-6-dispatch.txt
  ```

  **Commit**: YES (grouped with Task 5, 7)
  - Message: `feat(config): add per-role ctx_size with multi-level fallback`
  - Files: `pyvis/ai/swap_manager.py`

- [ ] 7. Update start_model.sh ctx_size values

  **What to do**:
  - Update the ctx_size variables at lines 39-42 of `scripts/start_model.sh`:
    ```bash
    # FROM:
    PLANNER_CTX=81920
    BRAIN_CTX=114688
    HANDS_CTX=40960
    JUDGE_CTX=81920

    # TO:
    # Per-role context sizes (fixed — no fallback)
    # KV quantization: q8_0 (cache-type-k/v set in start_server())
    PLANNER_CTX=81920
    BRAIN_CTX=114688
    HANDS_CTX=57344
    JUDGE_CTX=81920
    ```
  - Only HANDS_CTX changes (40960 → 57344). Other values remain the same.
  - Add comment noting KV q8_0 and no-fallback policy.

  **Must NOT do**:
  - Do NOT change model paths (already fixed in Task 4)
  - Do NOT change NGL values
  - Do NOT change SPLIT_MODE, TENSOR_SPLIT, or WARMUP_TIMEOUT
  - Do NOT implement fallback logic in the shell script (that's handled by swap_manager.py)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (needs Task 5 values finalized)
  - **Parallel Group**: Wave 2 (can run parallel with Task 6 after Task 5)
  - **Blocks**: Task 8 (cross-file QA)
  - **Blocked By**: Tasks 4 (model path fix), 5 (ctx_size values)

  **References**:
  - `scripts/start_model.sh:39-42` — Current ctx variables to update.
  - `config/unified_node.yaml` (after Task 5) — Source of truth for ctx_size values.

  **Acceptance Criteria**:

  ```
  Scenario: CTX values match unified_node.yaml
    Tool: Bash
    Steps:
      1. grep "PLANNER_CTX=" /Pyvis/scripts/start_model.sh
      2. grep "BRAIN_CTX=" /Pyvis/scripts/start_model.sh
      3. grep "HANDS_CTX=" /Pyvis/scripts/start_model.sh
      4. grep "JUDGE_CTX=" /Pyvis/scripts/start_model.sh
    Expected Result: PLANNER_CTX=81920, BRAIN_CTX=114688, HANDS_CTX=57344, JUDGE_CTX=81920
    Failure Indicators: Any value doesn't match expected
    Evidence: .sisyphus/evidence/task-7-ctx-values.txt

  Scenario: Shell script syntax valid
    Tool: Bash
    Steps:
      1. bash -n /Pyvis/scripts/start_model.sh
    Expected Result: No output (clean syntax)
    Evidence: .sisyphus/evidence/task-7-shell-syntax.txt
  ```

  **Commit**: YES (grouped with Task 5, 6)
  - Message: `feat(config): add per-role ctx_size with multi-level fallback`
  - Files: `scripts/start_model.sh`

### Wave 3 — Verification (after Wave 2)

- [ ] 8. Cross-file consistency QA for ctx_size values

  **What to do**:
  - Verify all three config files have identical per-role ctx_size values:
    - `config/unified_node.yaml` models.{role}.ctx_size
    - `pyvis/ai/swap_manager.py` SwapManagerConfig defaults
    - `scripts/start_model.sh` {ROLE}_CTX variables
  - Verify fallback values match between YAML and Python
  - Verify Hands model path is consistent across all three files
  - Verify n_gpu_layers values are consistent
  - Create a summary report of all values for evidence

  **Must NOT do**:
  - Do NOT make any changes — this is verification only
  - If inconsistencies found, report them (do NOT auto-fix)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 9)
  - **Parallel Group**: Wave 3
  - **Blocks**: Final verification wave
  - **Blocked By**: Tasks 5, 6, 7 (all config changes must be done)

  **References**:
  - `config/unified_node.yaml` — YAML config (after Tasks 1, 5)
  - `pyvis/ai/swap_manager.py` — Python config (after Task 6)
  - `scripts/start_model.sh` — Shell variables (after Tasks 4, 7)

  **Acceptance Criteria**:

  ```
  Scenario: All per-role ctx_size values match across 3 files
    Tool: Bash
    Steps:
      1. python3 -c "
         import yaml, re, sys
         sys.path.insert(0, '/Pyvis')
         
         # YAML values
         data = yaml.safe_load(open('/Pyvis/config/unified_node.yaml'))
         yaml_vals = {r: data['models'][r]['ctx_size'] for r in ['planner','brain','hands','judge']}
         
         # Python values
         from pyvis.ai.swap_manager import SwapManagerConfig
         c = SwapManagerConfig()
         py_vals = {'planner': c.ctx_size_planner, 'brain': c.ctx_size_brain, 'hands': c.ctx_size_hands, 'judge': c.ctx_size_judge}
         
         # Shell values
         sh = open('/Pyvis/scripts/start_model.sh').read()
         sh_vals = {}
         for role in ['PLANNER','BRAIN','HANDS','JUDGE']:
             m = re.search(rf'{role}_CTX=(\d+)', sh)
             sh_vals[role.lower()] = int(m.group(1)) if m else None
         
         all_match = True
         for role in ['planner','brain','hands','judge']:
             y, p, s = yaml_vals[role], py_vals[role], sh_vals[role]
             match = y == p == s
             print(f'{role}: yaml={y} py={p} sh={s} -> {\"MATCH\" if match else \"MISMATCH\"} ')
             if not match: all_match = False
         
         print(f'\\nOverall: {\"ALL MATCH\" if all_match else \"MISMATCHES FOUND\"} ')
         sys.exit(0 if all_match else 1)
         "
    Expected Result: planner=81920, brain=114688, hands=57344, judge=81920 — all MATCH
    Failure Indicators: Any role shows "MISMATCH"
    Evidence: .sisyphus/evidence/task-8-cross-file-consistency.txt

  Scenario: Hands model path consistent across all files
    Tool: Bash
    Steps:
      1. grep "Devstral" /Pyvis/config/unified_node.yaml
      2. grep "Devstral" /Pyvis/pyvis/ai/swap_manager.py
      3. grep "Devstral" /Pyvis/scripts/start_model.sh
    Expected Result: All three files reference the Devstral GGUF path
    Failure Indicators: Any file missing Devstral reference or still has Qwen3 for hands
    Evidence: .sisyphus/evidence/task-8-hands-model-consistency.txt
  ```

  **Commit**: NO (verification only)

- [ ] 9. Documentation accuracy QA

  **What to do**:
  - Verify `Pyvis_v4.md` accurately reflects:
    - Current model assignments per role
    - Hardware configuration (dual GPU)
    - Per-role ctx_size values (the new ones from this plan)
    - Single-server swap architecture
  - Verify `TODO.md` has:
    - Some items checked off (completed work)
    - Role-based ctx_size items listed
    - No Korean text remaining
  - Verify both files are in English

  **Must NOT do**:
  - Do NOT make changes — verification only

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 8)
  - **Parallel Group**: Wave 3
  - **Blocks**: Final verification wave
  - **Blocked By**: Tasks 2, 3 (doc updates must be done)

  **References**:
  - `Pyvis_v4.md` — Updated design spec (after Task 2)
  - `TODO.md` — Updated checklist (after Task 3)
  - `config/unified_node.yaml` — Source of truth for current values

  **Acceptance Criteria**:

  ```
  Scenario: Pyvis_v4.md references current models and ctx values
    Tool: Bash
    Steps:
      1. grep -c "32768\|58368\|16384" /Pyvis/Pyvis_v4.md
      2. grep -c "Devstral\|GLM.*Flash\|Qwen3.*14B\|DeepSeek.*R1" /Pyvis/Pyvis_v4.md
      3. grep -c "RTX 4070\|RTX 3060" /Pyvis/Pyvis_v4.md
    Expected Result: All grep counts >= 1
    Failure Indicators: Any count is 0 — key information missing
    Evidence: .sisyphus/evidence/task-9-doc-accuracy.txt

  Scenario: No Korean text in either document
    Tool: Bash
    Steps:
      1. python3 -c "
         import re
         for f in ['/Pyvis/Pyvis_v4.md', '/Pyvis/TODO.md']:
             text = open(f).read()
             korean = re.findall(r'[\uac00-\ud7af]+', text)
             status = 'CLEAN' if not korean else f'KOREAN FOUND ({len(korean)} strings)'
             print(f'{f}: {status}')
         "
    Expected Result: Both files show "CLEAN"
    Failure Indicators: Either file shows "KOREAN FOUND"
    Evidence: .sisyphus/evidence/task-9-no-korean.txt
  ```

  **Commit**: NO (verification only)

---

## Final Verification Wave

> 3 review agents run in PARALLEL. ALL must APPROVE. Rejection → fix → re-run.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, grep for values). For each "Must NOT Have": search codebase for forbidden changes — reject with file:line if found. Check evidence files exist in `.sisyphus/evidence/`. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Validate YAML with Python yaml parser. Run `bash -n start_model.sh`. Import SwapManagerConfig and verify fields. Check for syntax errors, inconsistent values, typos. Verify no stray debug prints or commented-out code added.
  Output: `YAML [VALID/INVALID] | Shell [VALID/INVALID] | Python [VALID/INVALID] | VERDICT`

- [ ] F3. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff (git diff). Verify 1:1 — everything in spec was built, nothing beyond spec was built. Check "Must NOT do" compliance: no model path changes in yaml, no AI role behavior changes, no tensor_split changes. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **Wave 1 commits** (can be individual or grouped):
  - `fix(config): correct YAML indentation for hands/judge role configs` — unified_node.yaml
  - `docs: update Pyvis_v4.md design spec to reflect current architecture` — Pyvis_v4.md
  - `docs: update TODO.md completion status` — TODO.md
  - `fix(scripts): correct Hands model path in start_model.sh` — start_model.sh
- **Wave 2 commit** (group — synchronized change):
  - `feat(config): add per-role ctx_size with multi-level fallback` — unified_node.yaml, swap_manager.py, start_model.sh
- **Wave 3**: No commits (QA only)

---

## Success Criteria

### Verification Commands
```bash
# YAML validity
python3 -c "import yaml; yaml.safe_load(open('/Pyvis/config/unified_node.yaml'))"  # Expected: no error

# Shell syntax
bash -n /Pyvis/scripts/start_model.sh  # Expected: no error

# Python import + ctx_size check
python3 -c "from pyvis.ai.swap_manager import SwapManagerConfig; c = SwapManagerConfig(); print(c.ctx_size_planner, c.ctx_size_brain, c.ctx_size_hands, c.ctx_size_judge)"
# Expected: 81920 114688 57344 81920

# Cross-file consistency: hands ctx matches
grep -q "HANDS_CTX=57344" /Pyvis/scripts/start_model.sh && echo "OK" || echo "MISMATCH"
python3 -c "import yaml; d=yaml.safe_load(open('/Pyvis/config/unified_node.yaml')); print('OK' if d['models']['hands']['ctx_size']==57344 else 'MISMATCH')"
```

### Final Checklist
- [ ] All per-role ctx_size values consistent across 3 files
- [ ] Fallback cascade logic implemented and documented
- [ ] YAML indentation fixed and valid
- [ ] Hands model path consistent across start_model.sh and unified_node.yaml
- [ ] Pyvis_v4.md updated with current architecture
- [ ] TODO.md reflects current completion state
- [ ] All QA evidence files present in `.sisyphus/evidence/`
