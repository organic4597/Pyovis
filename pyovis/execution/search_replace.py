"""
Pyovis — Search/Replace Block Parser & Applier

Aider-style search/replace blocks for incremental code editing.
Used by Hands.revise() to avoid full-file rewrites.

Block format:
    <<<<<<< SEARCH
    exact lines from original code
    =======
    replacement lines
    >>>>>>> REPLACE

Matching hierarchy: exact → whitespace-normalized → fuzzy (>0.8) → FAIL
All-or-nothing: all blocks must match or entire operation fails.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

logger = logging.getLogger(__name__)

# Parser regex: matches SEARCH/REPLACE blocks with 3+ delimiter chars
_BLOCK_RE = re.compile(
    r"<{3,}\s*SEARCH\n(.*?)\n={3,}\n(.*?)\n>{3,}\s*REPLACE",
    flags=re.DOTALL,
)


@dataclass
class SearchReplaceBlock:
    """A single search/replace edit block."""

    search: str
    replace: str
    block_index: int = 0


@dataclass
class ApplyResult:
    """Result of applying search/replace blocks to code."""

    success: bool
    new_code: str
    blocks_parsed: int = 0
    blocks_applied: int = 0
    fallback_triggered: bool = False
    fail_reason: str = ""
    match_types: list[str] = field(default_factory=list)


def parse_blocks(llm_response: str) -> list[SearchReplaceBlock]:
    """Parse search/replace blocks from LLM response text.

    Returns:
        List of SearchReplaceBlock objects in order of appearance.
    """
    blocks: list[SearchReplaceBlock] = []

    for i, match in enumerate(_BLOCK_RE.finditer(llm_response)):
        search_text = match.group(1)
        replace_text = match.group(2)
        blocks.append(
            SearchReplaceBlock(
                search=search_text,
                replace=replace_text,
                block_index=i,
            )
        )

    return blocks


def _normalize_whitespace(text: str) -> str:
    """Normalize whitespace for fuzzy matching.

    Strips trailing whitespace per line, normalizes line endings.
    Preserves leading whitespace (indentation).
    """
    lines = text.splitlines()
    return "\n".join(line.rstrip() for line in lines)


def _find_match(code: str, search: str) -> tuple[Optional[int], Optional[int], str]:
    """Find where search text appears in code.

    Tries matching hierarchy:
      1. Exact match
      2. Whitespace-normalized match
      3. Fuzzy match (ratio > 0.8)

    Returns:
        (start_index, end_index, match_type) or (None, None, "none")
    """
    # 1. Exact match
    idx = code.find(search)
    if idx != -1:
        return idx, idx + len(search), "exact"

    # 2. Whitespace-normalized match
    norm_code = _normalize_whitespace(code)
    norm_search = _normalize_whitespace(search)

    if norm_search and norm_search in norm_code:
        # Find the position in original code by scanning line-by-line
        norm_idx = norm_code.find(norm_search)
        start, end = _map_normalized_position(code, norm_code, norm_idx, norm_search)
        if start is not None:
            return start, end, "whitespace"

    # 3. Fuzzy match — slide a window over code lines
    search_lines = search.splitlines()
    code_lines = code.splitlines()
    search_len = len(search_lines)

    if search_len == 0:
        return None, None, "none"

    best_ratio = 0.0
    best_start_line = -1

    for i in range(len(code_lines) - search_len + 1):
        window = "\n".join(code_lines[i : i + search_len])
        ratio = SequenceMatcher(None, search, window).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_start_line = i

    if best_ratio > 0.8 and best_start_line >= 0:
        # Convert line indices to character positions
        start_pos = sum(len(line) + 1 for line in code_lines[:best_start_line])
        end_pos = sum(
            len(line) + 1 for line in code_lines[: best_start_line + search_len]
        )
        # Adjust for potential missing trailing newline
        if end_pos > 0 and end_pos <= len(code) + 1:
            end_pos = min(end_pos, len(code))
            # Trim trailing newline from end_pos if the original didn't end with one
            if end_pos > start_pos and code[end_pos - 1] == "\n":
                end_pos -= 1
            return start_pos, end_pos, f"fuzzy({best_ratio:.2f})"

    return None, None, "none"


def _map_normalized_position(
    original: str, normalized: str, norm_idx: int, norm_search: str
) -> tuple[Optional[int], Optional[int]]:
    """Map a position from normalized text back to original text.

    Uses line-based mapping for robustness.
    """
    # Count which line the normalized match starts on
    norm_before = normalized[:norm_idx]
    start_line = norm_before.count("\n")

    # Count how many lines the search covers
    search_line_count = norm_search.count("\n") + 1

    original_lines = original.splitlines(keepends=True)

    if start_line + search_line_count > len(original_lines):
        return None, None

    start_pos = sum(len(line) for line in original_lines[:start_line])
    end_pos = sum(
        len(line) for line in original_lines[: start_line + search_line_count]
    )

    # Strip trailing newline from end if it's just a separator
    if end_pos > start_pos and end_pos <= len(original):
        if original[end_pos - 1] == "\n":
            end_pos -= 1

    return start_pos, end_pos

    return None, None


def _check_overlaps(
    edits: list[tuple[int, int, str]],
) -> bool:
    """Check if any edits overlap. Edits are (start, end, replacement).

    Returns True if overlaps detected.
    """
    sorted_edits = sorted(edits, key=lambda e: e[0])
    for i in range(len(sorted_edits) - 1):
        if sorted_edits[i][1] > sorted_edits[i + 1][0]:
            return True
    return False


def apply_search_replace(code: str, llm_response: str) -> ApplyResult:
    """Apply search/replace blocks from LLM response to code.

    All-or-nothing semantics:
    - All blocks must parse and match successfully
    - If any block fails to match, the entire operation fails
    - Overlapping edits cause failure

    Args:
        code: Current source code string.
        llm_response: Raw LLM response containing S/R blocks.

    Returns:
        ApplyResult with success status and new code (or original on failure).
    """
    blocks = parse_blocks(llm_response)

    if not blocks:
        return ApplyResult(
            success=False,
            new_code=code,
            blocks_parsed=0,
            blocks_applied=0,
            fail_reason="no_blocks_found",
        )

    result = ApplyResult(
        success=False,
        new_code=code,
        blocks_parsed=len(blocks),
    )

    # Phase 1: Find all matches (validate before applying)
    edits: list[tuple[int, int, str]] = []  # (start, end, replacement)

    for block in blocks:
        # Empty search = insertion at top (special case)
        if not block.search.strip():
            result.fail_reason = f"empty_search_block_{block.block_index}"
            logger.warning(f"S/R block {block.block_index}: empty SEARCH text")
            return result

        start, end, match_type = _find_match(code, block.search)

        if start is None or end is None:
            result.fail_reason = f"no_match_block_{block.block_index}"
            logger.warning(
                f"S/R block {block.block_index}: no match found "
                f"(search={block.search[:80]!r}...)"
            )
            return result

        # Verify search matches exactly once (no ambiguous multi-match)
        if match_type == "exact":
            # Check for duplicate matches
            second_idx = code.find(block.search, start + 1)
            if second_idx != -1:
                result.fail_reason = f"ambiguous_match_block_{block.block_index}"
                logger.warning(
                    f"S/R block {block.block_index}: search text matches "
                    f"multiple locations"
                )
                return result

        edits.append((start, end, block.replace))
        result.match_types.append(match_type)

    # Phase 2: Check for overlapping edits
    if _check_overlaps(edits):
        result.fail_reason = "overlapping_edits"
        logger.warning("S/R blocks have overlapping regions")
        return result

    # Phase 3: Apply all edits (bottom-up to preserve positions)
    new_code = code
    for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        new_code = new_code[:start] + replacement + new_code[end:]

    # Phase 4: Verify applied replacements are no longer matchable as SEARCH
    for block in blocks:
        if block.search == block.replace:
            continue  # No-op block, skip verification
        if block.search in new_code:
            result.fail_reason = f"search_still_present_block_{block.block_index}"
            logger.warning(
                f"S/R block {block.block_index}: search text still present "
                f"after replacement (silent corruption risk)"
            )
            return result

    result.success = True
    result.new_code = new_code
    result.blocks_applied = len(blocks)
    return result


def format_metrics(result: ApplyResult) -> dict:
    """Format ApplyResult as metrics dict for JSONL logging.

    Keys: sr_blocks_parsed, sr_blocks_applied, sr_fallback_triggered,
          sr_fail_reason, sr_match_types
    """
    return {
        "sr_blocks_parsed": result.blocks_parsed,
        "sr_blocks_applied": result.blocks_applied,
        "sr_fallback_triggered": result.fallback_triggered,
        "sr_fail_reason": result.fail_reason,
        "sr_match_types": result.match_types,
    }
