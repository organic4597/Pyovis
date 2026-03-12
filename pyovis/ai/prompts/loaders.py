from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent


def load_prompt(filename: str) -> str:
    """Load a prompt file from the prompts directory."""
    prompt_path = PROMPT_DIR / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()
