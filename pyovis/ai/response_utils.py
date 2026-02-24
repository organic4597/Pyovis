import json
import re
from typing import Optional


THINK_PATTERN = r"<think[\s\S]*?</think\s*>"
THINK_EXTRACT_PATTERN = r"<think[\s\S]*?>([\s\S]*?)</think\s*>"

def strip_cot(text: str) -> str:
    return re.sub(THINK_PATTERN, "", text, flags=re.DOTALL).strip()


def extract_reasoning(message: dict) -> str:
    content = message.get("content") or ""
    think_match = re.search(THINK_EXTRACT_PATTERN, content, flags=re.DOTALL)
    if think_match:
        return think_match.group(1).strip()
    return message.get("reasoning_content") or ""


def summarize_thinking(thinking: str, max_chars: int = 500) -> str:
    if len(thinking) <= max_chars:
        return thinking
    half = max_chars // 2 - 20
    return f"{thinking[:half]}...[요약됨]...{thinking[-half:]}"


def message_text(message: dict) -> str:
    content = message.get("content") or ""
    if content.strip():
        return content
    return message.get("reasoning_content") or ""


def parse_json_message(message: dict, default: Optional[dict] = None) -> dict:
    text = message_text(message).strip()
    if not text:
        if default is not None:
            return default
        raise ValueError("Empty model response")
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        if default is not None:
            return default
        raise ValueError("No JSON object found in response")
    blob = match.group(0)
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        try:
            cleaned = re.sub(r",\s*(\}|\])", r"\1", blob)
            return json.loads(cleaned)
        except json.JSONDecodeError:
            if default is not None:
                return default
            raise
