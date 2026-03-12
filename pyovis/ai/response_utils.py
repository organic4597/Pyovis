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


def _find_first_json_object(text: str):
    """Find the first balanced JSON object in text (brace-counting, not greedy regex)."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                # Return a match-like object for backward compatibility
                class _Match:
                    def __init__(self, s, start, end):
                        self._s = s
                        self._start = start
                        self._end = end
                    def group(self, n=0):
                        return self._s[self._start:self._end]
                return _Match(text, start, i + 1)
    return None

def parse_json_message(message: dict, default: Optional[dict] = None) -> dict:
    text = message_text(message).strip()
    if not text:
        if default is not None:
            return default
        raise ValueError("Empty model response")
    # 첫 번째 유효 JSON 객체 매칭 (그리디 정규식 방지)
    match = _find_first_json_object(text)
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
