# PYVIS v4.0 — TODO

> 의존성 순서에 따라 정리된 구현 체크리스트.
> 단일 서버 아키텍처: 듀얼 GPU 병렬 (분할 모드 레이어), 한 번에 하나의 모델 로드, 포트 8001.

---

## 스프린트 1: 인프라 기반

- [x] `/pyvis_memory` 디렉토리 구조 생성 (models, workspace, logs, skill_library 등)
- [x] `/pyvis_memory/models/` 에 모델 파일 다운로드
  - GLM-4.7-Flash-Q4_K_M.gguf (플래너, 18GB)
  - Qwen3-14B-Q5_K_M.gguf (브레인, 10GB)
  - mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf (핸즈, 14GB)
  - DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf (저지, 9GB)
- [x] `config/unified_node.yaml` 작성 (역할별 ctx_size, n_gpu_layers, jinja, fallback)
- [x] `scripts/start_model.sh` 작성 (planner/brain/hands/judge/swap/status 명령어)
- [x] `pyvis/ai/swap_manager.py` 작성 (폴백 로직을 포함한 모델 스왑)

---

## 스프린트 2: Rust 코어 레이어 (1단계)

- [ ] Cargo.toml 워크스페이스 설정
- [ ] 락-프리 우선순위 큐 구현 (crossbeam SegQueue, P0 정지 → P1 AI → P2 IO)
- [ ] CPU 어피니티 스레드 풀 구현 (AI 추론용 코어 4-7)
- [ ] 모델 핫스왑 컨트롤러 구현 (ModelHotSwap, SwitchResult)
- [ ] PyO3 바인딩 + maturin 빌드 (PyPriorityQueue, PyModelSwap)
- [ ] 유닛 테스트 (cargo test)

---

## 스프린트 3: AI 엔진 (2단계)

- [ ] CUDA를 포함한 llama.cpp 빌드 (sm_86 + sm_89 혼합 아키텍처)
- [ ] 역할별 서버 시작 검증:
  - 플래너: GLM-4.7-Flash, ctx 62400, ngl 60
  - 브레인: Qwen3-14B, ctx 114688, ngl 60
  - 핸즈: Devstral-24B, ctx 40960, ngl 40, --jinja
  - 저지: DeepSeek-R1-14B, ctx 81920, ngl 60
- [ ] 실제 VRAM 사용량 측정 및 n_gpu_layers 값 검증
- [ ] 브레인 클라이언트 구현 (plan, handle_escalation, final_review + CoT 제거)
- [ ] 핸즈 클라이언트 구현 (build, revise)
- [ ] 저지 클라이언트 구현 (새 컨텍스트로 평가, KV 캐시 초기화)
- [ ] 시스템 프롬프트 작성 (brain_prompt.txt, hands_prompt.txt, judge_prompt.txt)

---

## 스프린트 4: 오케스트레이션 코어 (3단계 — 핵심 경로)

- [ ] Docker 샌드박스 이미지 빌드 (pyvis-sandbox:latest)
- [ ] CriticRunner 구현 (Docker 샌드박스 실행 + 오류 분류)
- [ ] LoopController 구현 (상태 머신: 계획→빌드→비평→평가→수정/보강→완료/에스컬레이션)
- [ ] LoopTracker 구현 (루프별 JSONL 비용 추적)
- [ ] SkillManager + SkillValidator 구현 (검증됨/후보 분리, 4가지 조건 검사)
- [ ] SessionManager 구현

---

## 스프린트 5: 오케스트레이션 확장 (3단계 — 비핵심)

- [ ] MCP ToolRegistry + ToolInstaller 구현 (승인 모드 포함)
- [ ] KG 서버 구현 (FAISS CPU + FastAPI, 포트 8003)

---

## 스프린트 6: 통합 및 검증 (3단계 → 4단계)

- [ ] E2E 통합 테스트 (전체 루프를 통한 간단한 작업)
- [ ] 에스컬레이션 시나리오 테스트
- [ ] 메모리 누수 탐지 (Valgrind, heaptrack)
- [ ] 스트레스 테스트 (10회 연속 루프)
- [ ] 성능 프로파일링 (모델 스왑 시간 측정)
- [ ] 설정 기반 동작 검증 (unified_node.yaml)

---

## 5단계 이후 (예약됨)

- [ ] 인터페이스 레이어: 오디오 (Whisper를 통한 STT/TTS), 비전 (화면 캡처), 텔레그램 봇
- [ ] 웹 서비스 확장

---

## 문서화

- [x] unified_node.yaml의 YAML 들여쓰기 수정
- [x] start_model.sh의 핸즈 모델 경로 수정
- [x] unified_node.yaml에 역할별 설정 필드 추가
- [x] swap_manager.py 업데이트 (모델 경로, 폴백 로직, 설정 필드)
- [x] start_model.sh 업데이트 (ctx 변수, 한국어 주석 번역)
- [x] TODO.md 번역 (한국어 → 영어)
- [x] Pyvis_v4.md 번역 (한국어 → 영어, 오래된 아키텍처 참조 수정)
- [x] 파일 간 일관성 QA
- [x] 문서 정확성 QA