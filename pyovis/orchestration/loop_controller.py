from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from pyovis.execution.file_writer import WorkspaceManager, FileWriter
    from pyovis.memory.graph_builder import KnowledgeGraphBuilder

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
    current_code: str | None = None
    critic_result: dict = field(default_factory=dict)
    judge_result: dict = field(default_factory=dict)
    project_id: str | None = None
    file_structure: list[str] = field(default_factory=list)
    created_files: list[dict] = field(default_factory=list)
    workspace: Optional["WorkspaceManager"] = None
    progress_callback: Optional[Callable[[str], Awaitable[None]]] = None
    reasoning_log: list[str] = field(default_factory=list)


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

            elif ctx.current_step == LoopStep.BUILD:
                current_task = ctx.todo_list[ctx.current_task_index]
                total = len(ctx.todo_list)
                idx = ctx.current_task_index + 1
                task_title = current_task.get(
                    "title", current_task.get("description", "")
                )[:50]
                await self._notify(
                    ctx, f"🔨 코드 작성 중... ({idx}/{total}) {task_title}"
                )
                skill_context = self.skill_manager.load_verified(ctx.task_description)
                ctx.current_code, reasoning = await self.hands.build(
                    current_task, ctx.plan, skill_context
                )
                if reasoning:
                    ctx.reasoning_log.append(f"[BUILD] {reasoning}")
                ctx.current_step = LoopStep.CRITIQUE

            elif ctx.current_step == LoopStep.CRITIQUE:
                await self._notify(ctx, "🧪 실행 테스트 중...")
                if ctx.current_code is None:
                    raise RuntimeError("No code to execute in critique step")
                result = await self.critic.execute(ctx.current_code)
                ctx.critic_result = {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.exit_code,
                    "execution_time": result.execution_time,
                    "error_type": result.error_type,
                }
                ctx.current_step = LoopStep.EVALUATE

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
                    "thought_process": verdict.thought_process,
                }

                # Store Judge thought process in Knowledge Graph
                if self.kg_builder and verdict.thought_process:
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
                        ctx.current_step = LoopStep.BUILD

                elif verdict.verdict in (
                    JudgeVerdict.REVISE.value,
                    JudgeVerdict.ENRICH.value,
                ):
                    ctx.consecutive_fails += 1
                    ctx.fail_reasons.append(verdict.reason)
                    ctx.current_step = self._check_escalation(ctx)

                elif verdict.verdict == JudgeVerdict.ESCALATE.value:
                    ctx.current_step = LoopStep.ESCALATE

            elif ctx.current_step in (LoopStep.REVISE, LoopStep.ENRICH):
                await self._notify(ctx, f"🔧 수정 중... (루프 {ctx.loop_count})")
                current_task = ctx.todo_list[ctx.current_task_index]
                if self._can_self_fix(ctx):
                    skill_context = self.skill_manager.load_verified(
                        ctx.task_description
                    )
                    ctx.current_code, reasoning = await self.hands.revise(
                        current_task,
                        ctx.current_code or "",
                        ctx.critic_result,
                        ctx.self_fix_scope,
                        ctx.judge_result,
                        ctx.pass_criteria,
                        skill_context,
                    )
                    if reasoning:
                        ctx.reasoning_log.append(f"[REVISE] {reasoning}")
                    ctx.current_step = LoopStep.CRITIQUE
                else:
                    ctx.current_step = LoopStep.ESCALATE

            elif ctx.current_step == LoopStep.ESCALATE:
                await self._notify(ctx, "⚠️ 에스컬레이션 처리 중...")
                if ctx.loop_count >= ctx.max_loops:
                    return self._human_escalation(ctx)

                escalation_result, reasoning = await self.brain.handle_escalation(ctx)
                if reasoning:
                    ctx.reasoning_log.append(f"[ESCALATE] {reasoning}")
                if escalation_result.get("action") == "revise_plan":
                    ctx.plan = escalation_result["new_plan"]
                    ctx.todo_list = escalation_result["new_todo"]
                    ctx.pass_criteria = escalation_result["new_criteria"]
                    ctx.consecutive_fails = 0
                    ctx.current_step = LoopStep.BUILD
                else:
                    return self._human_escalation(ctx)

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

            # Add the reasoning as a triplet
            await self.kg_builder.add_triplet(
                subject=f"task_{task_id}",
                predicate="judged_with_reasoning",
                object=verdict.thought_process[:2000],
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
