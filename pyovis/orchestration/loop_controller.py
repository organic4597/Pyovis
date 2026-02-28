from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from pyovis.execution.file_writer import WorkspaceManager, FileWriter
    from pyovis.memory.graph_builder import KnowledgeGraphBuilder
from pyovis.execution.snapshot import WorkspaceSnapshot

logger = logging.getLogger(__name__)

MSG_HUMAN_ESCALATION = "자동 해결 불가. 사람의 판단이 필요합니다."


class LoopStep(Enum):
    PLAN = "plan"
    BUILD = "build"
    CRITIQUE = "critique"
    EVALUATE = "evaluate"
    REVISE = "revise"
    ENRICH = "enrich"
    COMPLETE = "complete"
    ESCALATE = "escalate"


class JudgeVerdict(Enum):
    PASS = "PASS"
    REVISE = "REVISE"
    ENRICH = "ENRICH"
    ESCALATE = "ESCALATE"


@dataclass
class LoopContext:
    task_id: str
    task_description: str
    plan: Optional[str] = None
    todo_list: list[dict] = field(default_factory=list)
    pass_criteria: dict = field(default_factory=dict)
    self_fix_scope: dict = field(default_factory=dict)
    current_task_index: int = 0
    loop_count: int = 0
    max_loops: int = 5
    consecutive_fails: int = 0
    max_consecutive_fails: int = 3
    fail_reasons: list[str] = field(default_factory=list)
    current_step: LoopStep = LoopStep.PLAN
    score: int = 0
    current_code: str | dict[str, str] | None = None
    critic_result: dict = field(default_factory=dict)
    judge_result: dict = field(default_factory=dict)
    project_id: str | None = None
    file_structure: list[str] = field(default_factory=list)
    created_files: list[dict] = field(default_factory=list)
    workspace: Optional["WorkspaceManager"] = None
    progress_callback: Optional[Callable[[str], Awaitable[None]]] = None
    reasoning_log: list[str] = field(default_factory=list)
    setup_commands: list[str] = field(default_factory=list)  # Hands가 반환한 pip install 명령

class ResearchLoopController:
    def __init__(
        self,
        brain,
        hands,
        judge,
        critic,
        tracker,
        skill_manager,
        planner=None,
        file_writer: Optional["FileWriter"] = None,
        workspace: Optional["WorkspaceManager"] = None,
        kg_builder: Optional["KnowledgeGraphBuilder"] = None,
    ):
        self.brain = brain
        self.hands = hands
        self.judge = judge
        self.critic = critic
        self.tracker = tracker
        self.skill_manager = skill_manager
        self.planner = planner
        self.file_writer = file_writer
        self.workspace = workspace
        self.kg_builder = kg_builder

    async def _notify(self, ctx: LoopContext, message: str) -> None:
        if ctx.progress_callback is not None:
            try:
                await ctx.progress_callback(message)
            except Exception:
                pass

    async def run(self, ctx: LoopContext) -> dict:
        self.tracker.start(ctx.task_id, ctx.task_description)

        if self.file_writer and self.workspace:
            ctx.workspace = self.workspace
            ctx.project_id = self.workspace.project_id

        while ctx.current_step != LoopStep.COMPLETE:
            # ============================================================
            # 1. PLAN 단계: 계획 수립
            # ============================================================
            if ctx.current_step == LoopStep.PLAN:
                await self._notify(ctx, "⚙️ 계획 수립 중...")
                if self.planner is not None:
                    plan_output, reasoning = await self.planner.plan(ctx)
                else:
                    plan_output, reasoning = await self.brain.plan(ctx)
                if reasoning:
                    ctx.reasoning_log.append(f"[PLAN] {reasoning}")
                ctx.plan = plan_output["plan"]
                ctx.todo_list = plan_output["todo_list"]
                ctx.pass_criteria = plan_output["pass_criteria"]
                ctx.self_fix_scope = plan_output["self_fix_scope"]
                ctx.file_structure = plan_output.get("file_structure", [])

                if self.workspace and ctx.file_structure:
                    self.workspace.create_project(ctx.file_structure)

                ctx.current_step = LoopStep.BUILD
                self.tracker.record_switch("brain_to_hands", ctx.task_id)

            # ============================================================
            # 2. BUILD 단계: 코드 생성 (핵심 수정 구역)
            #    [중요] todo_list 전체를 순회하며 코드 생성
            #    for 루프가 다 끝날 때까지 CRITIQUE로 절대 일찍 이동하지 않음
            # ============================================================
            elif ctx.current_step == LoopStep.BUILD:
                await self._notify(
                    ctx, f"🔨 코드 작성 시작 (총 {len(ctx.todo_list)} 단계)..."
                )
                logger.info(f"[DEBUG] BUILD 시작: todo_list 개수={len(ctx.todo_list)}")

                # 스킬 컨텍스트 로드 (한 번만)
                skill_context = self.skill_manager.load_verified(ctx.task_description)
                logger.info(f"[DEBUG] skill_context 로드 완료: {len(skill_context)} bytes")

                full_code_files: dict[str, str] = {}  # {file_path: code}
                all_success = True

                logger.info(f"[DEBUG] For 루프 시작 전")
                # [중요] todo_list 전체를 순회 (For Loop)
                for i, task in enumerate(ctx.todo_list):
                    idx = i + 1
                    total = len(ctx.todo_list)
                    task_title = task.get("title", task.get("description", ""))[:50]

                    # 진행 상황 알림
                    await self._notify(
                        ctx, f"🔨 코드 작성 중... ({idx}/{total}) {task_title}"
                    )
                    logger.info(f"[DEBUG] Task {idx}/{total} notify 완료")

                    try:
                        # Hands 모델에 코드 생성 요청
                        result = await self.hands.build(task, ctx.plan, skill_context)

                        # 결과 언패킹 (2-tuple 또는 3-tuple 모두 처리)
                        part_code = result[0] if len(result) > 0 else None
                        reasoning = result[1] if len(result) > 1 else None
                        exec_plan_dict = result[2] if len(result) > 2 else {}

                        # setup_commands 누적 (중복 제거)
                        if exec_plan_dict:
                            for cmd in exec_plan_dict.get("setup_commands", []):
                                if cmd not in ctx.setup_commands:
                                    ctx.setup_commands.append(cmd)
                        # Reasoning 기록
                        if reasoning:
                            ctx.reasoning_log.append(f"[BUILD-{idx}] {reasoning}")

                        # 코드 조각 축적 — file_path 기준으로 저장
                        if part_code:
                            fp = task.get("file_path", "output.py") if isinstance(task, dict) else "output.py"
                            full_code_files[fp] = part_code
                            logger.info(f"✅ {idx}/{total} 단계 코드 생성 완료 ({fp})")
                        else:
                            logger.warning(
                                f"⚠️ {idx}/{total} 단계에서 코드가 생성되지 않았습니다."
                            )

                    except Exception as e:
                        logger.error(f"❌ {idx}/{total} 단계 코드 생성 실패: {e}")
                        ctx.reasoning_log.append(f"[BUILD-ERROR-{idx}] {str(e)}")
                        all_success = False
                        # 에러가 나도 나머지 작업을 계속 진행

                # [중요] 모든 루프가 끝난 후 — 파일 목록을 ctx에 저장
                if full_code_files:
                    ctx.current_code = full_code_files  # dict {file_path: code}
                    logger.info(f"✅ 전체 코드 생성 완료 ({len(full_code_files)} 파일): {list(full_code_files.keys())}")
                else:
                    ctx.current_code = None
                    logger.error("❌ 생성된 코드가 없습니다.")

                # 다음 단계로 이동 (CRITIQUE)
                ctx.current_step = LoopStep.CRITIQUE
                ctx.current_task_index = 0  # 인덱스 초기화

            # ============================================================
            # 3. CRITIQUE 단계: 실행 및 테스트
            # ============================================================
            elif ctx.current_step == LoopStep.CRITIQUE:
                await self._notify(ctx, "🧪 실행 테스트 중...")
                if ctx.current_code is None:
                    raise RuntimeError("No code to execute in critique step")
                result = await self.critic.execute(ctx.current_code, allow_network=True, setup_commands=ctx.setup_commands or None)
                ctx.critic_result = {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.exit_code,
                    "execution_time": result.execution_time,
                    "error_type": result.error_type,
                }
                # 실행 결과를 텔레그램에 전송
                status_icon = "✅" if result.exit_code == 0 else "❌"
                parts = [
                    f"{status_icon} 실행 완료 (exit={result.exit_code}, {result.execution_time:.1f}s)",
                ]
                if result.stdout and result.stdout.strip():
                    parts.append(f"📤 stdout:\n```\n{result.stdout.strip()}\n```")
                if result.stderr and result.stderr.strip():
                    parts.append(f"⚠️ stderr:\n```\n{result.stderr.strip()}\n```")
                if not result.stdout and not result.stderr:
                    parts.append("_(출력 없음)_")
                full_msg = "\n".join(parts)
                # Telegram 메시지 한 건 최대 4096자 — 초과 시 분할 전송
                if len(full_msg) <= 4000:
                    await self._notify(ctx, full_msg)
                else:
                    await self._notify(ctx, parts[0])
                    for part in parts[1:]:
                        chunk = part
                        while chunk:
                            await self._notify(ctx, chunk[:4000])
                            chunk = chunk[4000:]
                ctx.current_step = LoopStep.EVALUATE

            # ============================================================
            # 4. EVALUATE 단계: Judge 평가
            # ============================================================
            elif ctx.current_step == LoopStep.EVALUATE:
                await self._notify(ctx, "⚖️ 평가 중...")
                verdict = await self.judge.evaluate(
                    task=ctx.todo_list[ctx.current_task_index],
                    pass_criteria=ctx.pass_criteria,
                    critic_result=ctx.critic_result,
                    loop_count=ctx.loop_count,
                )
                ctx.score = verdict.score
                ctx.loop_count += 1

                ctx.judge_result = {
                    "verdict": verdict.verdict,
                    "score": verdict.score,
                    "reason": verdict.reason,
                    "error_type": verdict.error_type,
                    "thought_process": getattr(verdict, "thought_process", None),
                }

                # Store Judge thought process in Knowledge Graph
                thought_process = getattr(verdict, "thought_process", None)
                if self.kg_builder and thought_process:
                    await self._store_judge_reasoning(
                        ctx=ctx,
                        task=ctx.todo_list[ctx.current_task_index],
                        verdict=verdict,
                    )

                if verdict.verdict == JudgeVerdict.PASS.value:
                    await self._save_current_code(ctx)

                    ctx.current_task_index += 1
                    ctx.consecutive_fails = 0
                    if ctx.current_task_index >= len(ctx.todo_list):
                        ctx.current_step = LoopStep.COMPLETE
                    else:
                        ctx.current_step = LoopStep.CRITIQUE

                elif verdict.verdict in (
                    JudgeVerdict.REVISE.value,
                    JudgeVerdict.ENRICH.value,
                ):
                    ctx.consecutive_fails += 1
                    ctx.fail_reasons.append(verdict.reason)
                    ctx.current_step = self._check_escalation(ctx)

                elif verdict.verdict == JudgeVerdict.ESCALATE.value:
                    ctx.current_step = LoopStep.ESCALATE

            # ============================================================
            # 5. REVISE / ENRICH 단계: 수정 및 보강
            # ============================================================
            elif ctx.current_step in (LoopStep.REVISE, LoopStep.ENRICH):
                await self._notify(ctx, f"\U0001f527 수정 중... (루프 {ctx.loop_count})")
                current_task = ctx.todo_list[ctx.current_task_index]
                if self._can_self_fix(ctx):
                    skill_context = self.skill_manager.load_verified(
                        ctx.task_description
                    )

                    # Save previous code for rollback
                    prev_code = ctx.current_code or ""
                    # dict 모드: 현재 task의 파일만 추출해 revise에 넘김
                    current_file_path = current_task.get("file_path", "output.py")
                    if isinstance(prev_code, dict):
                        prev_code_str = prev_code.get(current_file_path, next(iter(prev_code.values()), ""))
                    else:
                        prev_code_str = prev_code

                    ctx.current_code, reasoning = await self.hands.revise(
                        current_task,
                        prev_code_str,
                        ctx.critic_result,
                        ctx.self_fix_scope,
                        ctx.judge_result,
                        ctx.pass_criteria,
                        skill_context,
                    )
                    # dict 모드: 수정된 파일을 dict에 다시 병합
                    if isinstance(prev_code, dict) and isinstance(ctx.current_code, str):
                        merged = dict(prev_code)
                        merged[current_file_path] = ctx.current_code
                        ctx.current_code = merged

                    # Log S/R metrics if available
                    sr_metrics = getattr(self.hands, "_last_sr_metrics", None)
                    if sr_metrics:
                        ctx.reasoning_log.append(
                            f"[SR_METRICS] {sr_metrics}"
                        )
                        logger.info(f"S/R metrics: {sr_metrics}")

                    # Syntax validation: compile check (str인 경우에만)
                    if ctx.current_code and isinstance(ctx.current_code, str):
                        try:
                            compile(ctx.current_code, "<revise>", "exec")
                        except SyntaxError as e:
                            logger.warning(
                                f"S/R 결과 구문 오류: {e}, 이전 코드로 롤백"
                            )
                            ctx.current_code = prev_code
                            ctx.reasoning_log.append(
                                f"[REVISE_ROLLBACK] Syntax error after S/R: {e}"
                            )

                    if reasoning:
                        ctx.reasoning_log.append(f"[REVISE] {reasoning}")
                    ctx.current_step = LoopStep.CRITIQUE
                else:
                    ctx.current_step = LoopStep.ESCALATE

            # ============================================================
            # 6. ESCALATE 단계: 에스컬레이션
            # ============================================================
            elif ctx.current_step == LoopStep.ESCALATE:
                await self._notify(ctx, "⚠️ 에스컬레이션 처리 중...")
                logger.info(f"[DEBUG] ESCALATE: loop_count={ctx.loop_count}, max={ctx.max_loops}, consecutive_fails={ctx.consecutive_fails}")
                if ctx.loop_count >= ctx.max_loops:
                    return self._human_escalation(ctx)

                escalation_result, reasoning = await self.brain.handle_escalation(ctx)
                if reasoning:
                    ctx.reasoning_log.append(f"[ESCALATE] {reasoning}")
                logger.info(f"[DEBUG] Brain escalation_result: {escalation_result}")
                logger.info(f"[DEBUG] escalation_result.get('action') = {escalation_result.get('action')}")
                if escalation_result.get("action") == "revise_plan":
                    ctx.plan = escalation_result["new_plan"]
                    raw_todo = escalation_result["new_todo"] or []
                    ctx.todo_list = [
                        t if isinstance(t, dict)
                        else {"id": i + 1, "title": str(t), "description": str(t)}
                        for i, t in enumerate(raw_todo)
                    ]
                    ctx.pass_criteria = escalation_result["new_criteria"]
                    ctx.consecutive_fails = 0
                    ctx.current_task_index = 0  # Reset index for new todo_list
                    ctx.current_step = LoopStep.BUILD
                else:
                    logger.info(f"[DEBUG] Brain 반환: action != 'revise_plan', 사람 에스케일레이션 실행")
                    return self._human_escalation(ctx)

        # ============================================================
        # 루프 종료 후: 최종 리뷰 및 스킬 평가
        # ============================================================
        logger.info("🏁 최종 리뷰 시작...")
        self.tracker.record_switch("hands_to_brain", ctx.task_id)
        final_result, reasoning = await self.brain.final_review(ctx)
        if reasoning:
            ctx.reasoning_log.append(f"[FINAL] {reasoning}")

        logger.info("🏁 스킬 평가 시작...")
        self.tracker.finish(ctx, final_result)
        await self.skill_manager.evaluate_and_patch(
            ctx, self.tracker.get_record(ctx.task_id)
        )
        logger.info("🏁 결과 반환 완료")

        final_result["project_id"] = ctx.project_id
        final_result["created_files"] = ctx.created_files
        final_result["reasoning_log"] = ctx.reasoning_log
        if self.workspace:
            final_result["workspace_root"] = str(ctx.workspace.project_root)

        return final_result

    async def _save_current_code(self, ctx: LoopContext) -> None:
        if not self.file_writer or not ctx.current_code:
            return

        if isinstance(ctx.current_code, dict):
            # 다중 파일 모드: 각 파일을 개별 저장
            for fp, src in ctx.current_code.items():
                result = self.file_writer.save_code(fp, src)
                ctx.created_files.append(
                    {
                        "task_id": ctx.current_task_index,
                        "file_path": fp,
                        "saved_path": result.get("path"),
                        "size_bytes": result.get("size_bytes", 0),
                    }
                )
        else:
            current_task = ctx.todo_list[ctx.current_task_index]
            file_path = current_task.get("file_path", f"output_{ctx.current_task_index}.py")
            result = self.file_writer.save_code(file_path, ctx.current_code)
            ctx.created_files.append(
                {
                    "task_id": ctx.current_task_index,
                    "file_path": file_path,
                    "saved_path": result.get("path"),
                    "size_bytes": result.get("size_bytes", 0),
                }
            )

    def _check_escalation(self, ctx: LoopContext) -> LoopStep:
        if ctx.consecutive_fails >= ctx.max_consecutive_fails:
            return LoopStep.ESCALATE
        if ctx.loop_count >= ctx.max_loops:
            return LoopStep.ESCALATE
        return LoopStep.REVISE

    def _can_self_fix(self, ctx: LoopContext) -> bool:
        error_type = ctx.critic_result.get("error_type", "")
        return error_type in ctx.self_fix_scope.get("allowed", [])

    def _human_escalation(self, ctx: LoopContext) -> dict:
        return {
            "status": "escalated",
            "task_id": ctx.task_id,
            "loop_count": ctx.loop_count,
            "fail_reasons": ctx.fail_reasons,
            "message": MSG_HUMAN_ESCALATION,
            "project_id": ctx.project_id,
            "created_files": ctx.created_files,
            "reasoning_log": ctx.reasoning_log,
        }

    async def _store_judge_reasoning(
        self,
        ctx: LoopContext,
        task: dict,
        verdict,
    ) -> None:
        """Store Judge reasoning in Knowledge Graph for future reference."""
        try:
            task_id = ctx.task_id
            task_desc = task.get("description", "") or task.get("title", "")

            thought_process = getattr(verdict, "thought_process", None)
            if not thought_process:
                return

            # Add the reasoning as a triplet
            await self.kg_builder.add_triplet(
                subject=f"task_{task_id}",
                predicate="judged_with_reasoning",
                object=thought_process[:2000],
            )

            # Add the verdict as a triplet
            await self.kg_builder.add_triplet(
                subject=f"task_{task_id}",
                predicate="verdict",
                object=verdict.verdict,
            )

            # Add task description
            await self.kg_builder.add_triplet(
                subject=f"task_{task_id}",
                predicate="has_description",
                object=task_desc[:500],
            )

            logger.info(f"Stored Judge reasoning for task {task_id}")

        except Exception as e:
            logger.warning(f"Failed to store Judge reasoning in KG: {e}")
