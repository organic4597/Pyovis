"""
Pyovis v4.0 — Model Swap Manager

Dual GPU (RTX 4070S + RTX 3060) parallel, single model loaded at a time.
Brain <-> Hands/Judge switching via llama-server restart.

Usage:
    swap = ModelSwapManager()
    await swap.ensure_model("brain")   # Load Brain model (skip if already loaded)
    await swap.ensure_model("hands")   # Swap to Hands model
    await swap.shutdown()              # Shut down server
"""

import asyncio
import json
import logging
import os
import subprocess
import time
import signal
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class ModelRole(str, Enum):
    PLANNER = "planner"
    BRAIN = "brain"
    HANDS = "hands"
    JUDGE = "judge"


@dataclass
class SwapRecord:
    timestamp: float
    from_role: Optional[str]
    to_role: str
    load_time_sec: float
    success: bool
    error: Optional[str] = None


@dataclass
class SwapManagerConfig:
    llama_server_bin: str = "/Pyvis/llama.cpp/build/bin/llama-server"
    port: int = 8001
    host: str = "0.0.0.0"
    threads: int = 4

    # v5.1: Dual context policy for Hands
    ctx_size: int = 114688
    ctx_size_planner: int = 32768
    ctx_size_brain: int = 32768
    ctx_size_judge: int = 16384

    # Hands dual mode: 32K (symbol OK) / 58K (fallback)
    ctx_size_hands_normal: int = 32768
    ctx_size_hands_fallback: int = 58368

    n_gpu_layers: int = 60
    n_gpu_layers_hands: int = 40
    n_gpu_layers_planner: int = 60
    split_mode: str = "layer"
    tensor_split: str = "0.55,0.45"

    # v5.1: KV Cache policy per role
    cache_type_k: str = "q8_0"
    cache_type_v: str = "q8_0"
    cache_type_k_hands_normal: str = "q8_0"
    cache_type_v_hands_normal: str = "q8_0"
    cache_type_k_hands_fallback: str = "q4_0"
    cache_type_v_hands_fallback: str = "q4_0"

    cpu_affinity: str = "4,5,6,7"
    health_check_timeout: int = 90
    health_check_interval: float = 1.0
    shutdown_timeout: int = 15
    log_dir: str = "/pyovis_memory/logs"
    swap_log: str = "/pyovis_memory/logs/swap.jsonl"
    warmup_timeout: int = 120

    models: dict = field(
        default_factory=lambda: {
            "planner": "/pyovis_memory/models/Qwen3-14B-Q5_K_M.gguf",
            "brain": "/pyovis_memory/models/Qwen3-14B-Q5_K_M.gguf",
            "hands": "/pyovis_memory/models/Qwen3-14B-Q5_K_M.gguf",
            "judge": "/pyovis_memory/models/DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf",
        }
    )

    jinja_roles: set = field(default_factory=lambda: {"hands"})

    fallbacks: dict = field(
        default_factory=lambda: {
            "planner": "brain",
            "hands": "brain",
        }
    )

    # Backward compatibility aliases (v4.0 tests)
    ctx_size_hands: int = field(default=16384)
    cache_type_k_brain: str = field(default="q4_0")
    cache_type_v_brain: str = field(default="q4_0")


class ModelSwapManager:
    """
    Manages llama-server process for model swapping.

    - Only one model loaded at a time
    - ensure_model() guarantees the required model is loaded
    - Blocks requests during swap (asyncio.Lock)
    - Logs swap history to JSONL
    - Falls back to alternate model if primary unavailable
    """

    def __init__(self, config: Optional[SwapManagerConfig] = None):
        self.config = config or SwapManagerConfig()
        self._current_role: Optional[ModelRole] = None
        self._process: Optional[subprocess.Popen] = None
        self._lock = asyncio.Lock()
        self._swap_count = 0
        self._base_url = f"http://localhost:{self.config.port}"

        Path(self.config.log_dir).mkdir(parents=True, exist_ok=True)
        self._http_client = httpx.AsyncClient(timeout=5.0)

    @property
    def current_role(self) -> Optional[ModelRole]:
        return self._current_role

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def api_url(self) -> str:
        return f"{self._base_url}/v1/chat/completions"

    @property
    def health_url(self) -> str:
        return f"{self._base_url}/health"

    async def ensure_model(self, role: str) -> bool:
        target = ModelRole(role)

        async with self._lock:
            if self._current_role == target and self.is_running:
                if await self._health_check(
                    retries=3
                ) and await self._verify_model_identity(target.value):
                    logger.info("⚡ 모델 '%s' 이미 로드됨 → 스킵", target.value)
                    return True
                else:
                    logger.warning(
                        "⚠️ 모델 '%s' 로드됨이나 비정상 → 재시작", target.value
                    )

            if (
                self._current_role is None
                and await self._health_check(retries=3)
                and await self._verify_model_identity(target.value)
            ):
                self._current_role = target
                return True

            model_path = self.config.models[target.value]
            if not Path(model_path).exists():
                if await self._health_check(
                    retries=3
                ) and await self._verify_model_identity(target.value):
                    self._current_role = target
                    return True
                fallback_role = self.config.fallbacks.get(target.value)
                if fallback_role:
                    logger.warning(
                        f"Model file missing for '{target.value}', "
                        f"falling back to '{fallback_role}'"
                    )
                    target = ModelRole(fallback_role)
                else:
                    logger.error(
                        f"Model file missing for '{target.value}' and no fallback configured"
                    )
                    return False

            return await self._swap_to(target)

    async def _swap_to(self, target: ModelRole) -> bool:
        """Perform actual model swap."""
        start_time = time.time()
        from_role = self._current_role.value if self._current_role else None

        logger.info("🔄 모델 교체: %s → %s", from_role, target.value)

        # 1. Stop existing server
        await self._stop_server()

        # 2. Start new server
        model_path = self.config.models[target.value]
        if not Path(model_path).exists():
            error = f"Model file not found: {model_path}"
            logger.error(error)
            self._log_swap(
                from_role, target.value, time.time() - start_time, False, error
            )
            return False

        try:
            ctx_size = self._ctx_size_for_role(target)
            self._start_server(model_path, target.value, ctx_size)
        except Exception as e:
            error = f"Failed to start server: {e}"
            logger.error(error)
            self._log_swap(
                from_role, target.value, time.time() - start_time, False, error
            )
            return False

        # 3. Wait for health check
        if await self._wait_for_ready():
            # 4. Verify the server is actually serving the expected model
            if not await self._verify_model_identity(target.value):
                load_time = time.time() - start_time
                error = f"Identity mismatch: server not serving '{target.value}'"
                logger.error("❌ %s", error)
                self._log_swap(from_role, target.value, load_time, False, error)
                await self._stop_server()
                return False
            load_time = time.time() - start_time
            self._current_role = target
            self._swap_count += 1
            logger.info(
                "🔄 모델 '%s' 준비 완료 (%.1f초, swap #%d)",
                target.value,
                load_time,
                self._swap_count,
            )
            self._log_swap(from_role, target.value, load_time, True)
            return True
        else:
            load_time = time.time() - start_time
            error = "Health check timeout"
            logger.error(
                "❌ 모델 '%s' 준비 실패 (%.1f초 경과)", target.value, load_time
            )
            self._log_swap(from_role, target.value, load_time, False, error)
            await self._stop_server()
            return False

    def _start_server(self, model_path: str, role: str, ctx_size: int):
        n_gpu_layers = self._ngl_for_role(role)
        cache_k = (
            self.config.cache_type_k_brain
            if role == "brain"
            else self.config.cache_type_k
        )
        cache_v = (
            self.config.cache_type_v_brain
            if role == "brain"
            else self.config.cache_type_v
        )

        cmd = [
            "taskset",
            "-c",
            self.config.cpu_affinity,
            self.config.llama_server_bin,
            "-m",
            model_path,
            "--alias",
            role,
            "-ngl",
            str(n_gpu_layers),
            "--ctx-size",
            str(ctx_size),
            "--cache-type-k",
            cache_k,
            "--cache-type-v",
            cache_v,
            "--split-mode",
            self.config.split_mode,
            "--tensor-split",
            self.config.tensor_split,
            "--parallel",
            "1",
            "--threads",
            str(self.config.threads),
            "--port",
            str(self.config.port),
            "--host",
            self.config.host,
        ]

        if role in self.config.jinja_roles:
            cmd.append("--jinja")

        log_file = Path(self.config.log_dir) / f"{role}.log"
        with open(log_file, "w") as lf:
            self._process = subprocess.Popen(
                cmd,
                stdout=lf,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
            )

        logger.info(f"Started llama-server PID={self._process.pid} for {role}")

    async def _kill_port_occupant(self) -> None:
        """Kill any process occupying our port (handles stale processes from
        previous bot restarts where self._process reference was lost)."""
        try:
            result = subprocess.run(
                ["fuser", "-k", "-TERM", f"{self.config.port}/tcp"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                logger.info(f"Killed stale process on port {self.config.port}")
                await asyncio.sleep(1)  # brief wait for port release
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass  # fuser not available or no process on port

    async def _stop_server(self):
        # Kill any process using our port first (handles stale processes from
        # previous runs or cases where self._process reference was lost)
        await self._kill_port_occupant()

        if self._process is None:
            return

        if self._process.poll() is not None:
            # Process already dead, clean up zombie
            try:
                os.waitpid(self._process.pid, os.WNOHANG)
            except (ChildProcessError, OSError):
                pass
            self._process = None
            self._current_role = None
            return

        logger.info(f"Stopping llama-server PID={self._process.pid}")
        # Kill entire process group (setsid was used at launch)
        try:
            os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            self._process.terminate()

        for _ in range(self.config.shutdown_timeout):
            if self._process.poll() is not None:
                break
            await asyncio.sleep(1)
        else:
            logger.warning("Force killing llama-server")
            try:
                os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                self._process.kill()
            self._process.wait()

        # Clean up zombie process
        try:
            os.waitpid(self._process.pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass

        self._process = None
        self._current_role = None

        # Allow GPU memory to be released
        await asyncio.sleep(2)

    async def _wait_for_ready(self) -> bool:
        deadline = time.time() + self.config.health_check_timeout
        while time.time() < deadline:
            if self._process and self._process.poll() is not None:
                rc = self._process.returncode
                logger.error(f"Server process died with exit code {rc}")
                # 로그 파일에서 마지막 N줄 추출해 원인 힌트 제공
                try:
                    log_path = (
                        Path(self.config.log_dir)
                        / f"{self._current_role.value if self._current_role else 'unknown'}.log"
                    )
                    if log_path.exists():
                        lines = log_path.read_text().splitlines()
                        tail = "\n".join(lines[-20:])
                        logger.error(f"Last log lines:\n{tail}")
                except Exception:
                    pass
                return False

            if await self._health_check(retries=1):
                return True

            await asyncio.sleep(self.config.health_check_interval)

        logger.error(
            f"Health check timed out after {self.config.health_check_timeout}s"
        )
        return False

    async def _health_check(self, retries: int = 1) -> bool:
        for _ in range(retries):
            try:
                resp = await self._http_client.get(self.health_url)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "ok":
                        return True
            except Exception:
                pass
            if retries > 1:
                await asyncio.sleep(1)
        return False

    async def _verify_model_identity(self, expected_alias: str) -> bool:
        """Verify that llama-server is serving the expected model.

        Queries GET /props and checks the ``model_alias`` field against
        *expected_alias*.  This guards against the case where the server
        process is alive and healthy but is still (or again) running a
        different model — something ``/health`` alone cannot detect.

        Returns True if the alias matches or if /props is unreachable
        (non-blocking degraded-mode: liveness already confirmed by
        _wait_for_ready, so we only hard-fail on a confirmed mismatch).
        """
        props_url = f"{self._base_url}/props"
        try:
            resp = await self._http_client.get(props_url)
            if resp.status_code != 200:
                logger.warning(
                    "⚠️ /props returned HTTP %d — skipping identity check",
                    resp.status_code,
                )
                return True  # degraded mode: don't block on non-200
            data = resp.json()
            actual_alias = data.get("model_alias", "")
            if actual_alias == expected_alias:
                logger.debug("✅ 모델 아이덴티티 확인: alias='%s'", actual_alias)
                return True
            logger.error(
                "❌ 모델 아이덴티티 불일치: expected='%s', actual='%s'",
                expected_alias,
                actual_alias,
            )
            return False
        except Exception as exc:
            logger.warning("⚠️ /props 조회 실패 (%s) — 아이덴티티 검증 건너뜀", exc)
            return True  # degraded mode: can't reach /props, don't block

    def _ctx_size_for_role(self, role: ModelRole) -> int:
        if role == ModelRole.PLANNER:
            return self.config.ctx_size_planner
        if role == ModelRole.BRAIN:
            return self.config.ctx_size_brain
        if role == ModelRole.JUDGE:
            return self.config.ctx_size_judge
        if role == ModelRole.HANDS:
            return self.config.ctx_size_hands_normal
        return self.config.ctx_size

    def _ngl_for_role(self, role: str) -> int:
        if role == "hands":
            return self.config.n_gpu_layers_hands
        if role == "planner":
            return self.config.n_gpu_layers_planner
        return self.config.n_gpu_layers

    def _log_swap(
        self,
        from_role: Optional[str],
        to_role: str,
        load_time: float,
        success: bool,
        error: Optional[str] = None,
    ):
        record = {
            "timestamp": time.time(),
            "from": from_role,
            "to": to_role,
            "load_time_sec": round(load_time, 2),
            "success": success,
            "swap_count": self._swap_count,
        }
        if error:
            record["error"] = error

        try:
            swap_log = Path(self.config.swap_log)
            swap_log.parent.mkdir(parents=True, exist_ok=True)
            with open(swap_log, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.warning(f"Failed to write swap log: {e}")

    async def shutdown(self):
        async with self._lock:
            await self._stop_server()
            await self._http_client.aclose()
            logger.info("ModelSwapManager shutdown complete")

    def get_stats(self) -> dict:
        return {
            "current_role": self._current_role.value if self._current_role else None,
            "is_running": self.is_running,
            "swap_count": self._swap_count,
            "port": self.config.port,
            "pid": self._process.pid if self._process else None,
        }

    async def wait_for_health(
        self, role: str, port: int = 8001, timeout: int = 120
    ) -> None:
        """
        llama-server 가 실제 HTTP 요청에 응답할 때까지 대기 (헬스체크).
        """
        logger.info(f"🏥 [{role}] 헬스체크 시작 (port={port}, timeout={timeout}s)...")
        start_time = asyncio.get_event_loop().time()
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    resp = await client.get(f"http://localhost:{port}/", timeout=5.0)
                    if resp.status_code == 200:
                        logger.info(f"✅ [{role}] 헬스체크 성공 (응답받음)")
                        return
                except Exception:
                    pass
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > timeout:
                    logger.error(f"❌ [{role}] 헬스체크 타임아웃 ({timeout}초 초과)")
                    raise TimeoutError(
                        f"[{role}] 헬스체크 실패: {timeout}초 동안 응답 없음"
                    )
                await asyncio.sleep(0.5)

    def wait_for_health_sync(
        self, role: str, port: int = 8001, timeout: int = 120
    ) -> None:
        """
        [동기] llama-server 가 실제 HTTP 요청에 응답할 때까지 대기.
        """
        import time
        import urllib.request
        import urllib.error

        logger.info(f"🏥 [{role}] 헬스체크 시작 (port={port}, timeout={timeout}s)...")
        start_time = time.time()
        url = f"http://localhost:{port}/"

        while True:
            try:
                req = urllib.request.urlopen(url, timeout=5)
                if req.status == 200:
                    logger.info(f"✅ [{role}] 헬스체크 성공 (응답받음)")
                    return
            except Exception:
                pass  # 연결 안 되면 계속 대기

            # 타임아웃 체크
            elapsed = time.time() - start_time
            if elapsed > timeout:
                logger.error(f"❌ [{role}] 헬스체크 타임아웃 ({timeout}초 초과)")
                raise TimeoutError(
                    f"[{role}] 헬스체크 실패: {timeout}초 동안 응답 없음"
                )

            # 0.5 초 대기 후 재시도
            time.sleep(0.5)
