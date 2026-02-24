---

## 6. Python Orchestration Layer

### 6.1 Main Entry Point (`pyvis/main.py`)

```python
import asyncio
import uvloop
from pyvis.orchestration.session_manager import SessionManager
from pyvis.memory.kg_server import start_kg_server
from pyvis.tracking.loop_tracker import LoopTracker
import pyvis_core  # Rust bindings

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

async def main():
    # Initialize Rust core
    task_queue = pyvis_core.PyPriorityQueue()
    model_swap = pyvis_core.PyModelSwap()

    # Start KG server (isolated on cores 0,1)
    kg_task = asyncio.create_task(start_kg_server())

    # Initialize loop tracker
    tracker = LoopTracker()

    # Start session manager
    session = SessionManager(task_queue, model_swap, tracker)
    await session.run()

if __name__ == "__main__":
    uvloop.run(main())
```

### 6.2 Loop Controller (`orchestration/loop_controller.py`)

```python
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

class LoopStep(Enum):
    PLAN      = "plan"
    BUILD     = "build"
    CRITIQUE  = "critique"
    EVALUATE  = "evaluate"
    REVISE    = "revise"
    ENRICH    = "enrich"
    COMPLETE  = "complete"
    ESCALATE  = "escalate"

class JudgeVerdict(Enum):
    PASS      = "PASS"       # Score 90 or above
    REVISE    = "REVISE"     # Score 70~90
    ENRICH    = "ENRICH"     # Score below 70
    ESCALATE  = "ESCALATE"   # Unable to judge or exceeded N attempts

@dataclass
class LoopContext:
    task_id: str
    task_description: str
    plan: Optional[str] = None
    todo_list: list = field(default_factory=list)
    pass_criteria: dict = field(default_factory=dict)
    self_fix_scope: dict = field(default_factory=dict)
    current_task_index: int = 0
    loop_count: int = 0
    max_loops: int = 5           # Default, configurable via config
    consecutive_fails: int = 0
    max_consecutive_fails: int = 3
    fail_reasons: list = field(default_factory=list)
    current_step: LoopStep = LoopStep.PLAN
    score: int = 0

class ResearchLoopController:
    def __init__(self, brain, hands, judge, critic, tracker, skill_manager):
        self.brain = brain
        self.hands = hands
        self.judge = judge
        self.critic = critic
        self.tracker = tracker
        self.skill_manager = skill_manager

    async def run(self, ctx: LoopContext) -> dict:
        """
        Main loop.
        Brain only appears at the beginning (PLAN) and end (COMPLETE/ESCALATE).
        Intermediate loops are handled autonomously by Hands + Critic + Judge.
        """
        self.tracker.start(ctx.task_id, ctx.task_description)

        while ctx.current_step != LoopStep.COMPLETE:

            # ── PLAN: Brain call (first time only) ──────────────────────
            if ctx.current_step == LoopStep.PLAN:
                plan_output = await self.brain.plan(ctx)
                ctx.plan = plan_output["plan"]
                ctx.todo_list = plan_output["todo_list"]
                ctx.pass_criteria = plan_output["pass_criteria"]
                ctx.self_fix_scope = plan_output["self_fix_scope"]
                ctx.current_step = LoopStep.BUILD
                # Brain → Hands switch (1 time)
                self.tracker.record_switch("brain_to_hands")

            # ── BUILD: Hands code generation ───────────────────────────
            elif ctx.current_step == LoopStep.BUILD:
                current_task = ctx.todo_list[ctx.current_task_index]
                skill_context = self.skill_manager.load_verified(ctx.task_description)
                code = await self.hands.build(current_task, ctx.plan, skill_context)
                ctx.current_code = code
                ctx.current_step = LoopStep.CRITIQUE

            # ── CRITIQUE: Critic execution ────────────────────────────
            elif ctx.current_step == LoopStep.CRITIQUE:
                result = await self.critic.execute(ctx.current_code)
                ctx.critic_result = result
                ctx.current_step = LoopStep.EVALUATE

            # ── EVALUATE: Judge evaluation (after KV Cache reset) ────
            elif ctx.current_step == LoopStep.EVALUATE:
                verdict = await self.judge.evaluate(
                    task=ctx.todo_list[ctx.current_task_index],
                    pass_criteria=ctx.pass_criteria,
                    critic_result=ctx.critic_result,
                    loop_count=ctx.loop_count
                )
                ctx.score = verdict.score
                ctx.loop_count += 1

                if verdict.verdict == JudgeVerdict.PASS:
                    ctx.current_task_index += 1
                    ctx.consecutive_fails = 0
                    if ctx.current_task_index >= len(ctx.todo_list):
                        ctx.current_step = LoopStep.COMPLETE
                    else:
                        ctx.current_step = LoopStep.BUILD

                elif verdict.verdict == JudgeVerdict.REVISE:
                    ctx.consecutive_fails += 1
                    ctx.fail_reasons.append(verdict.reason)
                    ctx.current_step = self._check_escalation(ctx)

                elif verdict.verdict == JudgeVerdict.ENRICH:
                    ctx.consecutive_fails += 1
                    ctx.fail_reasons.append(verdict.reason)
                    ctx.current_step = self._check_escalation(ctx)

                elif verdict.verdict == JudgeVerdict.ESCALATE:
                    ctx.current_step = LoopStep.ESCALATE

            # ── REVISE/ENRICH: Hands autonomous revision ──────────────
            elif ctx.current_step in (LoopStep.REVISE, LoopStep.ENRICH):
                # Hands revises without Brain involvement
                current_task = ctx.todo_list[ctx.current_task_index]
                can_self_fix = self._can_self_fix(ctx)

                if can_self_fix:
                    code = await self.hands.revise(
                        current_task, ctx.current_code,
                        ctx.critic_result, ctx.self_fix_scope
                    )
                    ctx.current_code = code
                    ctx.current_step = LoopStep.CRITIQUE
                else:
                    ctx.current_step = LoopStep.ESCALATE

            # ── ESCALATE: Brain re-invocation ───────────────────────────
            elif ctx.current_step == LoopStep.ESCALATE:
                if ctx.loop_count >= ctx.max_loops:
                    # Report to human
                    return self._human_escalation(ctx)

                # Brain classifies the cause and revises the plan
                escalation_result = await self.brain.handle_escalation(ctx)
                if escalation_result["action"] == "revise_plan":
                    ctx.plan = escalation_result["new_plan"]
                    ctx.todo_list = escalation_result["new_todo"]
                    ctx.pass_criteria = escalation_result["new_criteria"]
                    ctx.consecutive_fails = 0
                    ctx.current_step = LoopStep.BUILD
                else:
                    return self._human_escalation(ctx)

        # ── COMPLETE: Brain final review ────────────────────────────
        # Hands/Judge → Brain switch (1 time)
        self.tracker.record_switch("hands_to_brain")
        final_result = await self.brain.final_review(ctx)

        # Save loop record + Skill reinforcement decision
        self.tracker.finish(ctx, final_result)
        await self.skill_manager.evaluate_and_patch(ctx, self.tracker.get_record(ctx.task_id))

        return final_result

    def _check_escalation(self, ctx: LoopContext) -> LoopStep:
        if ctx.consecutive_fails >= ctx.max_consecutive_fails:
            return LoopStep.ESCALATE
        if ctx.loop_count >= ctx.max_loops:
            return LoopStep.ESCALATE
        return LoopStep.REVISE

    def _can_self_fix(self, ctx: LoopContext) -> bool:
        """Check if the issue is within the self-fix scope"""
        error_type = ctx.critic_result.get("error_type", "")
        return error_type in ctx.self_fix_scope.get("allowed", [])

    def _human_escalation(self, ctx: LoopContext) -> dict:
        return {
            "status": "escalated",
            "task_id": ctx.task_id,
            "loop_count": ctx.loop_count,
            "fail_reasons": ctx.fail_reasons,
            "message": "Unable to resolve automatically. Human judgment is required."
        }
```

---

## 7. AI Engine — Brain / Hands / Judge

### 7.1 Brain (`ai/brain.py`)

```python
import httpx
from pyvis.ai.prompts import load_prompt
from pyvis.utils import stri[CORRUPTED]port json

BRAIN_API = "http://localhost:8001/v1/chat/completions"

class Brain:
    def __init__(self):
        self.system_prompt = load_prompt("brain_prompt.txt")
        self.client = httpx.AsyncClient(timeout=120.0)

    async def plan(self, ctx) -> dict:
        """
        Brain initial output:
        1. Plan document
        2. TODO List
        3. PASS criteria per Task
        4. Self-fix scope (items Hands can autonomously fix)
        """
        user_message = f"""
Task: {ctx.task_descripti[CORRUPTED] You must respond only in the following JSON format:
{{
  "plan": "Overall architecture and implementation plan (Markdown)",
  "todo_list": [
    {{"id": 1, "title": "Task title", "description": "Detailed description"}}
  ],
  "pass_criteria": {{
    "1": ["condition1", "condition2"],
    "2": ["condition1"]
  }},
  "self_fix_scope": {{
    "allowed": ["type_error", "syntax_error", "missing_import"],
    "escalate": ["architecture_change", "schema_change"]
  }}
}}
"""
        response = await self._call(user_message)
     [CORRUPTED]lean = strip_cot(response)
        return json.loads(clean)

    async def handle_escalation(self, ctx) -> dict:
        """Escalation cause analysis and plan revision"""
        user_message = f"""
Original plan: {ctx.plan}
Failure cause list: {json.dumps(ctx.fail_reasons, ensure_ascii=False)}
Loop count: {ctx.loop_count}
Last error: {ctx.critic_result.get('stderr', '')}

Classify the cause and respond in the following format:
{{
  "cause_type": "plan_error | implementation_error | environmen[CORRUPTED]error",
  "action": "revise_plan | human_escalation",
  "analysis": "Analysis content",
  "new_plan": "Revised plan (when action is revise_plan)",
  "new_todo": [...],
  "new_criteria": {{...}}
}}
"""
        response = await self._call(user_message)
        clean = strip_cot(response)
        return json.loads(clean)

    async def final_review(self, ctx) -> dict:
        """Final review"""
        response = await self._call(
            f"Review and summarize the final deliverables for the following task: {ctx.task_description}"
        )
        return {"status": "complete", "review": strip_cot(response)}

    async def _call(self, user_message: str) -> str:
        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_message}
            ],
            "temperature": 0.7,
            "max_tokens": 4096
        }
        resp = await self.client.post(BRAIN_API, json=payload)
        return resp.json()["choices"][0]["message"]["content"]
```

### 7.2 Hands (`ai/hands.py`)

```python
import httpx
from pyvis.ai.prompts import load_prompt

HANDS_API = "http://localhost:8002/v1/chat/completions"

class Hands:
    def __init__(self):
        self.system_prompt = load_prompt("hands_prompt.txt")
        self.client = httpx.AsyncClient(timeout=180.0)

    async def build(self, task: dict, plan: str, skill_context: str) -> str:
        """Code generation based on plan"""
        user_messe[CORRUPTED] = f"""
Full plan:
{plan}

Current Task to implement:
{task['title']}: {task['description']}

Skill rules to apply:
{skill_context}

Implement only the code corresponding to the current Task in the plan above.
"""
        return await self._call(user_message)

    async def revise(self, task: dict, prev_code: str,
                     critic_result: dict, self_fix_scope: dict) -> str:
        """Code regeneration based on revision instructions"""
        user_message = f"""
Task: {task['title']}
Previous code:
{prev_cod[CORRUPTED]

Execution error:
{critic_result.get('stderr', 'None')}

Standard output:
{critic_result.get('stdout', 'None')}

Allowed self-fix scope: {self_fix_scope.get('allowed', [])}
Fix the above error. Changes outside the allowed scope are prohibited.
"""
        return await self._call(user_message)

    async def _call(self, user_message: str) -> str:
        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role[CORRUPTED]content": user_message}
            ],
            "temperature": 0.2,
            "max_tokens": 8192
        }
        resp = await self.client.post(HANDS_API, json=payload)
        return resp.json()["choices"][0]["message"]["content"]
```

### 7.3 Judge (`ai/judge.py`)

```python
import httpx
from pyvis.ai.prompts import load_prompt
from dataclasses import dataclass
import json
import re

JUDGE_API = "http://localhost:8002/v1/chat/completions"

@dataclass
class JudgeResult:
    verdict: str      # PASS / REVISE / ENRICH / ESCALATE
    score: int        # 0~100
    reason: str
    error_type: str   # For determining Hands autonomous fix eligibility

class Judge:
    def __init__(self):
        self.system_prompt = load_prompt("judge_prompt.txt")
        self.client = httpx.AsyncClient(timeout=60.0)

    async def evaluate(self, task: dict, pass_criteria: dict,
                       critic_result: dict, loop_count: int) -> JudgeResult:
        """
        Key point: No previous conversation history. Fresh context every time.
        [CORRUPTED]       Does not include Hands' code or thought process.
        Judges solely based on plan requirements + execution results.
        """
        criteria = pass_criteria.get(str(task["id"]), [])

        user_message = f"""
Task: {task['title']}
PASS criteria:
{chr(10).join(f'- {c}' for c in criteria)}

Execution results:
- Exit code: {critic_result.get('exit_code', -1)}
- Execution time: {critic_result.get('execution_time', 0):.2f} seconds
- Standard output: {critic_result.get('stdout', 'None')[:500]}
- Error: [CORRUPTED]lt.get('stderr', 'None')[:500]}
- Current loop count: {loop_count}

If all PASS criteria are met, verdict is PASS.
If partially unmet, REVISE (score 70 or above) or ENRICH (score below 70).
If unable to judge or repeated failures, ESCALATE.

You must respond only in the following JSON format:
{{"verdict": "PASS|REVISE|ENRICH|ESCALATE", "score": 0-100,
  "reason": "Judgment basis", "error_type": "Error type (null if none)"}}
"""
        # Call with fresh context, no previous conversation
        response = await sel[CORRUPTED]_call_fresh(user_message)
        return self._parse(response)

    async def _call_fresh(self, user_message: str) -> str:
        """Fresh context every time — no previous conversation history"""
        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user",   "content": user_message}
            ],
            "temperature": 0.1,
            "max_tokens": 512
        }
        resp = await se[CORRUPTED]lient.post(JUDGE_API, json=payload)
        return resp.json()["choices"][0]["message"]["content"]

    def _parse(self, response: str) -> JudgeResult:
        try:
            data = json.loads(re.sub(r'```json|```', '', response).strip())
            return JudgeResult(
                verdict=data["verdict"],
                score=int(data["score"]),
                reason=data["reason"],
                error_type=data.get("error_type")
            )
        except Exception:
            return JudgeResult(verdict="ESCALATE", score=0,
                               reason="Failed to parse Judge response", error_type=None)
```

---

## 8. Self-Evaluation Loop Design

### 8.1 Overall Flow Diagram

```
Human: Task Input
        │
        ▼
[Brain] Plan + TODO + PASS Criteria + Fix Scope (1 time)
        │
        ▼ ← Switch 1 time (Brain → Hands/Judge)
        │
┌──────────────────────────────────────[CORRUPTED]───┐
│           Autonomous Loop (No Brain)               │
│                                                    │
│  For each Task in TODO:                            │
│                                                    │
│  [Hands/Builder] Generate Task N code              │
│         │                                          │
│         ▼                                          │
│  [Critic] Docker Sandbox Execution                 │
│         │
│         ▼                                          │
│  [Judge] Memory Reset → Compare against PASS criteria │
│         │                                          │
│   ┌─────┼──────────┬──────────┐                    │
│  PASS  REVISE   ENRICH   ESCALATE                  │
│   │     │         │          │                     │
│  Next  Self-fix? Self-fix?  Call Brain             │
│  Task  ┌─┴──┐    ┌─┴[CORRUPTED]   │               │
│       Yes  No   Yes  No   (Exception switch)       │
│       │    │    │    │                              │
│     Regen ESC  Regen ESC                           │
│                                                    │
│  All Tasks PASS → Loop Exit                        │
└────────────────────────────────────────────────────┘
        │
        ▼ ← Switch 1 time (Hands/Judge → Brain)
        │
[Brain] Final Review + Loop Record Analysis + Skill Reinforcement Decision
        │
        ▼
Long-Term Memory Storage → Deliver to Human

Total model switches: Minimum 2 fixed (+ 1 per escalation)
```

### 8.2 Scoring Criteria

| Score | Verdict | Action |
|---|---|---|
| 90~100 | PASS | Proceed to next Task |
| 70~89 | REVISE | Hands autonomous fix (within fix scope) |
| 0~69 | ENRICH | Hands autonomous fix (within scope) or Brain escalation |
| - | ESCE[CORRUPTED] | Brain re-invocation |

### 8.3 Escalation Conditions

| Condition | Criteria | Action |
|---|---|---|
| Consecutive Failures | Same Task fails 3 times consecutively | Call Brain → Cause classification |
| Max Loops | Total loops exceed 5 | Report to human |
| Unable to Judge | Judge ESCALATE | Call Brain |
| Fix Scope Exceeded | Architecture change deemed necessary | Call Brain |
