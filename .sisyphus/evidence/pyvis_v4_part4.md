
```python
from fastapi import FastAPI, BackgroundTasks
import faiss
import numpy as np
import pickle
from pathlib import Path

app = FastAPI()
KG_PATH = Path("/pyvis_memory/knowledge_graph")

class CPUKnowledgeGraph:
    def __init__(self):
        self.dim = 384  # Embedding dimension
        self.index = faiss.IndexFlatL2(self.dim)
        self.metadata = []
        self._load()

    def add(self, text: str, meta: dict):
        embedding = self._embed(text)
        self.index.add(np.array([embedding], dtype=np.float32)[CORRUPTED]) 
        self.metadata.append(meta)

    def search(self, query: str, k: int = 5) -> list:
        embedding = self._embed(query)
        D, I = self.index.search(np.array([embedding], dtype=np.float32), k)
        return [self.metadata[i] for i in I[0] if i < len(self.metadata)]

    def _embed(self, text: str) -> list:
        # Use lightweight embedding model (e.g., sentence-transformers/all-MiniLM-L6-v2)
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer('all-MiniLM[CORRUPTED]-L6-v2')
        return model.encode(text).tolist()

    def _load(self):
        index_file = KG_PATH / "index.faiss"
        meta_file = KG_PATH / "metadata.pkl"
        if index_file.exists():
            self.index = faiss.read_index(str(index_file))
        if meta_file.exists():
            with open(meta_file, 'rb') as f:
                self.metadata = pickle.load(f)

    def save(self):
        KG_PATH.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(KG_PATH / "index.faiss"))
        with open(KG_PATH / "metadata.pkl", 'wb') as f:
            pickle.dump(self.metadata, f)

kg = CPUKnowledgeGraph()

@app.post("/add")
async def add_knowledge(text: str, meta: dict, background_tasks: BackgroundTasks):
    background_tasks.add_task(kg.add, text, meta)
    return {"status": "accepted"}

@app.get("/search")
async def search_knowledge(query: str, k: int = 5):
    return {"results": kg.search(query, k)}

async def start_kg_server():
    import uvicorn
    config = uvicorn.Config(app, host="127.0.0.1", port=8003, log_level="error")
    server = uvicorn.Server(config)
    await server.serve()
```

### 13.2 Storage Structure

```
/pyvis_memory/
├── models/                          # ~50GB: GGUF model files
│   ├── DeepSeek-R1-Distill-Qwen-32B-Q4_K_S.gguf
│   └── Qwen2.5-Coder-32B-Instruct-Q4_K_S.gguf
├── user_profile/
│   └── profile.json                 # User preferences, tendencies, stack
├── conversation_log/
│   └── YYYY-MM-DD.jsonl             # Per-session conversation history
├── project_history/
│   └── {task_id}/                   # Per-project decision history
├── knowledge_graph/
│   ├── index.faiss                  # FAISS index
│   └── metadata.pkl                 # Vector metadata
├── skill_library/
│   ├── verified/                    # Verified Skills (auto-applied)
│   └── candidate/                   # Pending review Skills (not applied)
├── loop_rec[CORRUPTED]ords/
│   └── YYYY-MM-DD.jsonl             # Loop cost tracking logs
└── research_cache/
    └── {query_hash}.json            # Web search result cache
```

---

## 14. Interface Layer (Reserved for Phase 4)

> Paused — implementation deferred. Only system signatures defined here.

```python
# interface/audio.py (Phase 4 implementation planned)
class AudioModule:
    """Whisper-based STT/TTS"""
    wake_word: str = "hey pyvis"
    sample_rate: int = 16000

# interface/vision.py (Phase 4 implementation planned[CORRUPTED])
class VisionModule:
    """Screen capture and analysis"""
    port: int = 9999

# interface/telegram_bot.py (Phase 4 implementation planned)
class TelegramBot:
    """Telegram bot interface"""
    webhook_url: str = "http://localhost:8080/webhook"
```

---

## 15. Configuration File

> **NOTE**: The YAML configuration embedded below is **STALE** — it reflects the original 2-GPU, 2-port architecture design (GPU 0 on port 8001, GPU 1 on port 8002) with older model choices (DeepSeek-R1-Distill-Qwen-32B, Qwen2.5-Coder-32B). The **current** production configuration uses a single-server swap architecture on port 8001 with updated models (GLM-4.7-Flash, Qwen3-14B, Devstral-24B, DeepSeek-R1-Distill-Qwen-14B). See `config/unified_node.yaml` for the authoritative, up-to-date configuration.

### `config/unified_node.yaml`

```yaml
system:
  name: "Pyvis"
  version: "4.0.0"

hardware:
  cpu:
    cores: 8
    affinity:
      interface:      [0, 1]
      orchestration:  [2, 3]
      ai_inference:   [4, 5, 6, 7]
  gpu:
    - id: 0
      name: "RTX 407[CORRUPTED]0"
      vram_gb: 12
      role: "brain"
      model: "DeepSeek-R1-Distill-Qwen-32B-Q4_K_S.gguf"
      context_size: 32768
      n_gpu_layers: 40
      port: 8001
    - id: 1
      name: "RTX 3060"
      vram_gb: 12
      role: "hands_judge"
      model: "Qwen2.5-Coder-32B-Instruct-Q4_K_S.gguf"
      context_size: 65536
      n_gpu_layers: 40
      port: 8002

ai:
  brain:
    system_prompt: "system/brain_prompt.txt"
    temperature: 0.7
    max_tokens: 4096
    cot_strip: true            # Strip <think> blocks[CORRUPTED]
  hands:
    system_prompt: "system/hands_prompt.txt"
    temperature: 0.2
    max_tokens: 8192
  judge:
    system_prompt: "system/judge_prompt.txt"
    temperature: 0.1
    max_tokens: 512
    kv_cache_reset: true       # KV Cache reset required every time
    fresh_context: true        # No previous conversation history

research_loop:
  max_loops: 5
  max_consecutive_fails: 3
  pass_threshold: 90           # 90+ = PASS
  revise_threshold: 70         # 70+ = REVISE, below = ENRICH
  sandbox_timeou[CORRUPTED]t: 30
  min_repeat_count: 3          # Minimum repeated failure count for Skill addition
  min_task_diversity: 3        # Minimum number of distinct tasks
  requires_human_review: true  # Human review required before verified promotion

mcp:
  requires_approval: true      # Human approval required before tool installation

storage:
  base_path: "/pyvis_memory"
  models_path: "/pyvis_memory/models"
  workspace_path: "/pyvis_memory/workspace"
  logs_path: "/pyvis_memory/loop_records"

sandbox:
  type: "docker"
  image: "pyvis-sandbox:latest"
  tmpfs_path: "/dev/shm/pyvis_sandbox"
  memory_limit: "512m"
  cpu_limit: 1.0
  network_enabled: false

logging:
  level: "INFO"
  format: "json"
  rotation: "daily"
  retention: "30d"
```

---

## 16. Implementation Roadmap and Phase-by-Phase Tasks

### Phase 1: Rust Core (Weeks 1-2)

```
- [ ] Cargo.toml workspace setup
- [ ] Implement and test crossbeam-based lock-free priority queue
- [ ] Implement CPU Affinity thread pool
- [ ] Implement Model Hot-Swap controller (ModelHotSwap)
- [ ] Build via PyO3 Python b[CORRUPTED]indings and verify Python import
- [ ] Unit tests (cargo test)
```

### Phase 2: AI Engine (Weeks 3-4)

```
- [ ] llama.cpp CUDA build (sm_86 + sm_89)
- [ ] Launch and verify Brain server (GPU 0, port 8001)
- [ ] Launch and verify Hands/Judge server (GPU 1, port 8002)
- [ ] Measure VRAM usage and determine optimal n_gpu_layers values
- [ ] Implement Brain client and verify CoT preprocessing
- [ ] Implement Hands client
- [ ] Implement Judge client (KV Cache reset verification required)
- [ ] Write 3 system prompts (brain/hands/judge_prompt.txt)
```

### Phase 3: Orchestration (Weeks 5-6)

```
- [ ] Build and test Docker sandbox image
  - macOS support: document OrbStack as Docker alternative with execution procedures
- [ ] Implement CriticRunner and verify error classification
- [ ] Implement LoopController (full loop state machine)
- [ ] Implement LoopTracker and verify JSONL persistence
- [ ] Implement SkillManager (verified/candidate separation)
- [ ] Implement SkillValidator (4-condition verification)
- [ ] Implement MCP ToolRegistry + ToolInstaller
- [ ] Implement FastAPI KG server (FAISS CPU)
- [ ] Implement Session Manager
- [ ] End-to-End integration test (verify full loop with simple task)
```

### Phase 4: Stabilization (Weeks 7-8)

```
- [ ] Memory leak detection (Valgrind, heaptrack)
- [ ] Stress test (10 consecutive loops)
- [ ] Escalation scenario testing
- [ ] Loop cost tracking → Skill reinforcement pipeline verification
- [ ] Performance profiling (model switch latency measurement)
- [ ] Configuration file-based behavior verification

Phase 5 and beyond (reserved):
- [ ] Interface layer[CORRUPTED] (STT/TTS, Vision, Telegram)
- [ ] Web service expansion
```

---

## 17. System Prompt Definitions

### `system/brain_prompt.txt`

```
You are Pyvis's Brain.

Role:
- Analyze tasks and produce actionable plans
- Clearly define TODO List, PASS criteria, and fix scope
- Analyze escalation causes and revise plans
- Review final deliverables

Absolute Rules:
- You do not generate code directly
- All implementation[CORRUPTED] is delegated to Hands
- Respond only in the requested format (JSON)
```

### `system/hands_prompt.txt`

```
You are Pyvis's Hands.

Role:
- Implement code based on Brain's plan
- When given fix instructions, modify only within the allowed scope

Absolute Rules:
- Do not make design decisions not specified in the plan
- Do not make changes outside the fix scope
- Output code only. Minimize explanations.
```

### `system/judge_prompt.txt`

```
You are Pyvis's Judge.

Role:
- Judge solely based on the plan's PASS criteria and execution results
- You have no knowledge of the code implementation method or process
- Evaluate only whether deliverables meet requirements

Absolute Rules:
- Do not praise
- Issue exactly one verdict: PASS / REVISE / ENRICH / ESCALATE
- Respond only in JSON format
- You have no previous conversation history. Judge only what you see now
```

---

## 18. Risk Factors and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| VRAM shortage | High | Adjust n_gpu_layers; determine optimal values after actual measurement |
| Rust-Python boundary bugs | Medium | PyO3 strict type checking, unit tests |
| Judge self-rationalization | Medium | KV Cache reset + context isolation + forced fresh_context |
| Infinite loops | Medium | max_loops hard cap at 5, timeout settings |
| Skill contamination | Medium | candidate/verified separation, human review required[CORRUPTED] |
| Sandbox security | High | network=none, mem_limit, cpu_limit, root privileges removed |
| Model switch latency | Medium | Keep both models resident in RAM; use context switch only |
| FAISS index corruption | Low | Call save() on session end, periodic backups |

---

## Performance Goals

| Metric | Target |
|---|---|
| Brain inference speed | 15-25 t/s |
| Hands/Judge inference speed | 15-25 t/s |
| Model switch latency | < 100ms (context switch) |
| Critic execution latency | < 30s (timeout) |
| K[CORRUPTED]G search latency | < 1ms |
| Single loop duration | 2-5 min |
| Total model switches | Minimum 2 (when no escalation) |

---

*— Pyvis v4.0 Implementation Design Document End —*  
*Implementation by: Claude Opus 4.6*
