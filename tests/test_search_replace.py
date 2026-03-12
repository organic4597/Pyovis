"""
Pyovis v4.0 — Search/Replace Block Parser & Applier Tests

Comprehensive tests for the Aider-style SEARCH/REPLACE block system.
Covers: parse_blocks, apply_search_replace, format_metrics,
        matching hierarchy (exact → whitespace → fuzzy → FAIL),
        all-or-nothing semantics, and edge cases.
"""

from __future__ import annotations

import pytest

from pyovis.execution.search_replace import (
    ApplyResult,
    apply_search_replace,
    format_metrics,
    parse_blocks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _block(search: str, replace: str) -> str:
    """Build a single SEARCH/REPLACE block string."""
    return f"<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE"


def _wrap_response(*blocks: str, preamble: str = "Here are the changes:\n") -> str:
    """Wrap one or more blocks into a fake LLM response."""
    return preamble + "\n".join(blocks)


SAMPLE_CODE = """\
def greet(name):
    print(f"Hello, {name}!")

def farewell(name):
    print(f"Goodbye, {name}!")
"""


# ---------------------------------------------------------------------------
# Tests — parse_blocks
# ---------------------------------------------------------------------------


class TestParseBlocks:
    def test_zero_blocks(self):
        """No S/R blocks in plain text → empty list."""
        result = parse_blocks("Just a normal response with no blocks.")
        assert result == []

    def test_single_block(self):
        """Parse a single well-formed block."""
        response = _block("old line", "new line")
        blocks = parse_blocks(response)
        assert len(blocks) == 1
        assert blocks[0].search == "old line"
        assert blocks[0].replace == "new line"
        assert blocks[0].block_index == 0

    def test_multiple_blocks(self):
        """Parse multiple blocks, preserving order and indices."""
        response = _wrap_response(
            _block("search_a", "replace_a"),
            _block("search_b", "replace_b"),
            _block("search_c", "replace_c"),
        )
        blocks = parse_blocks(response)
        assert len(blocks) == 3
        assert [b.search for b in blocks] == ["search_a", "search_b", "search_c"]
        assert [b.replace for b in blocks] == ["replace_a", "replace_b", "replace_c"]
        assert [b.block_index for b in blocks] == [0, 1, 2]

    def test_malformed_missing_replace_marker(self):
        """Block without >>>>>>> REPLACE marker → not parsed."""
        bad = "<<<<<<< SEARCH\nold\n=======\nnew\nNO_END_MARKER"
        assert parse_blocks(bad) == []

    def test_malformed_missing_equals(self):
        """Block without ======= separator → not parsed."""
        bad = "<<<<<<< SEARCH\nold\nnew\n>>>>>>> REPLACE"
        assert parse_blocks(bad) == []

    def test_multiline_search_and_replace(self):
        """Blocks can contain multiple lines."""
        search = "line 1\nline 2\nline 3"
        replace = "new 1\nnew 2"
        blocks = parse_blocks(_block(search, replace))
        assert len(blocks) == 1
        assert blocks[0].search == search
        assert blocks[0].replace == replace

    def test_empty_llm_response(self):
        """Empty string → no blocks."""
        assert parse_blocks("") == []

    def test_extra_angle_brackets(self):
        """Delimiters with more than 3 chevrons still work (regex 3+)."""
        response = "<<<<<<<<<< SEARCH\nold\n==========\nnew\n>>>>>>>>>> REPLACE"
        blocks = parse_blocks(response)
        assert len(blocks) == 1
        assert blocks[0].search == "old"
        assert blocks[0].replace == "new"


# ---------------------------------------------------------------------------
# Tests — apply_search_replace: exact match
# ---------------------------------------------------------------------------


class TestApplyExactMatch:
    def test_single_block_exact(self):
        """Single exact-match replacement."""
        code = 'print("hello")\nprint("world")\n'
        response = _block('print("hello")', 'print("hi")')
        result = apply_search_replace(code, response)

        assert result.success is True
        assert 'print("hi")' in result.new_code
        assert 'print("hello")' not in result.new_code
        assert result.blocks_parsed == 1
        assert result.blocks_applied == 1
        assert result.match_types == ["exact"]

    def test_multiple_blocks_exact(self):
        """Two exact-match blocks applied together."""
        result = apply_search_replace(
            SAMPLE_CODE,
            _wrap_response(
                _block('print(f"Hello, {name}!")', 'print(f"Hi, {name}!")'),
                _block('print(f"Goodbye, {name}!")', 'print(f"Bye, {name}!")'),
            ),
        )
        assert result.success is True
        assert 'print(f"Hi, {name}!")' in result.new_code
        assert 'print(f"Bye, {name}!")' in result.new_code
        assert result.blocks_parsed == 2
        assert result.blocks_applied == 2


# ---------------------------------------------------------------------------
# Tests — apply_search_replace: whitespace-normalized match
# ---------------------------------------------------------------------------


class TestApplyWhitespaceMatch:
    def test_trailing_whitespace_match(self):
        """Search with trailing whitespace difference still matches."""
        code = "def foo():   \n    pass\n"
        # Search text has no trailing whitespace on first line
        response = _block("def foo():\n    pass", "def bar():\n    pass")
        result = apply_search_replace(code, response)

        assert result.success is True
        assert "def bar():" in result.new_code
        assert result.match_types[0] == "whitespace"


# ---------------------------------------------------------------------------
# Tests — apply_search_replace: fuzzy match
# ---------------------------------------------------------------------------


class TestApplyFuzzyMatch:
    def test_fuzzy_match_above_threshold(self):
        """Slightly different text matches via fuzzy (ratio > 0.8)."""
        code = "def calculate_total(items):\n    total = sum(items)\n    return total\n"
        # Search text has minor difference (missing underscore → fuzzy match)
        search = "def calculate_total(items):\n    total = sum(item)\n    return total"
        replace = "def calculate_total(items):\n    total = sum(i.price for i in items)\n    return total"
        response = _block(search, replace)
        result = apply_search_replace(code, response)

        assert result.success is True
        assert "fuzzy" in result.match_types[0]
        assert "sum(i.price for i in items)" in result.new_code


# ---------------------------------------------------------------------------
# Tests — apply_search_replace: failure cases
# ---------------------------------------------------------------------------


class TestApplyFailures:
    def test_empty_search_block(self):
        """Block with only whitespace as search text → fail."""
        response = _block("   \n  ", "new content")
        result = apply_search_replace("some code", response)

        assert result.success is False
        assert result.new_code == "some code"
        assert "empty_search" in result.fail_reason

    def test_no_match(self):
        """Search text not found anywhere → fail."""
        response = _block("this text does not exist", "replacement")
        result = apply_search_replace(SAMPLE_CODE, response)

        assert result.success is False
        assert result.new_code == SAMPLE_CODE
        assert "no_match" in result.fail_reason

    def test_ambiguous_multi_match(self):
        """Search text matches multiple locations → fail."""
        code = "x = 1\nx = 1\nx = 1\n"
        response = _block("x = 1", "x = 2")
        result = apply_search_replace(code, response)

        assert result.success is False
        assert result.new_code == code
        assert "ambiguous_match" in result.fail_reason

    def test_overlapping_edits(self):
        """Two blocks whose matched regions overlap → fail."""
        code = "AAABBBCCC"
        # Both blocks search for overlapping regions
        response = _wrap_response(
            _block("AAABBB", "XXX"),
            _block("BBBCCC", "YYY"),
        )
        result = apply_search_replace(code, response)

        assert result.success is False
        assert result.new_code == code
        assert "overlapping" in result.fail_reason

    def test_no_blocks_found(self):
        """LLM response with no valid blocks → fail with no_blocks_found."""
        result = apply_search_replace("some code", "No blocks here, sorry.")

        assert result.success is False
        assert result.new_code == "some code"
        assert result.fail_reason == "no_blocks_found"
        assert result.blocks_parsed == 0

    def test_empty_llm_response(self):
        """Empty LLM response → no blocks found."""
        result = apply_search_replace("code", "")

        assert result.success is False
        assert result.new_code == "code"
        assert result.fail_reason == "no_blocks_found"


# ---------------------------------------------------------------------------
# Tests — all-or-nothing semantics
# ---------------------------------------------------------------------------


class TestAllOrNothing:
    def test_one_block_fails_entire_operation_fails(self):
        """If second block has no match, first block is NOT applied."""
        code = "alpha\nbeta\ngamma\n"
        response = _wrap_response(
            _block("alpha", "ALPHA"),  # would match
            _block("nonexistent", "XXX"),  # no match → fail
        )
        result = apply_search_replace(code, response)

        assert result.success is False
        # Original code must be unchanged
        assert result.new_code == code
        assert "alpha" in result.new_code  # still original
        assert "no_match" in result.fail_reason

    def test_all_blocks_must_match(self):
        """Three blocks, middle one fails → nothing applied."""
        code = "line1\nline2\nline3\n"
        response = _wrap_response(
            _block("line1", "LINE1"),
            _block("missing_line", "NOPE"),
            _block("line3", "LINE3"),
        )
        result = apply_search_replace(code, response)

        assert result.success is False
        assert result.new_code == code


# ---------------------------------------------------------------------------
# Tests — silent corruption defense
# ---------------------------------------------------------------------------


class TestCorruptionDefense:
    def test_search_still_present_after_replace(self):
        """If search text appears elsewhere after replace, report corruption risk."""
        # Code has 'foo' in two places; search matches only one (non-adjacent)
        # but after replacement, 'foo' is still present in the other place
        code = "foo = 1\nbar = 2\n"
        # Search matches "foo = 1", replace with something containing "foo"
        response = _block("foo = 1", "foo = 1\nfoo = extra")
        result = apply_search_replace(code, response)

        # The search text "foo = 1" is still present in new_code → corruption check
        assert result.success is False
        assert "search_still_present" in result.fail_reason


# ---------------------------------------------------------------------------
# Tests — format_metrics
# ---------------------------------------------------------------------------


class TestFormatMetrics:
    def test_output_shape_success(self):
        """format_metrics returns dict with expected keys."""
        r = ApplyResult(
            success=True,
            new_code="code",
            blocks_parsed=2,
            blocks_applied=2,
            fallback_triggered=False,
            fail_reason="",
            match_types=["exact", "whitespace"],
        )
        m = format_metrics(r)

        assert isinstance(m, dict)
        assert m["sr_blocks_parsed"] == 2
        assert m["sr_blocks_applied"] == 2
        assert m["sr_fallback_triggered"] is False
        assert m["sr_fail_reason"] == ""
        assert m["sr_match_types"] == ["exact", "whitespace"]

    def test_output_shape_failure(self):
        """format_metrics correctly reflects failure state."""
        r = ApplyResult(
            success=False,
            new_code="original",
            blocks_parsed=1,
            blocks_applied=0,
            fail_reason="no_match_block_0",
        )
        m = format_metrics(r)

        assert m["sr_blocks_parsed"] == 1
        assert m["sr_blocks_applied"] == 0
        assert m["sr_fail_reason"] == "no_match_block_0"
        assert m["sr_match_types"] == []

    def test_all_expected_keys_present(self):
        """Verify exactly the documented keys are present."""
        r = ApplyResult(success=True, new_code="")
        m = format_metrics(r)
        expected_keys = {
            "sr_blocks_parsed",
            "sr_blocks_applied",
            "sr_fallback_triggered",
            "sr_fail_reason",
            "sr_match_types",
        }
        assert set(m.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Tests — Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_code_string(self):
        """Applying blocks to empty code with no match → fail."""
        response = _block("something", "replacement")
        result = apply_search_replace("", response)

        assert result.success is False
        assert result.new_code == ""

    def test_empty_replacement_deletion(self):
        """Block with empty replacement = deletion of matched text."""
        code = "keep_this\ndelete_this\nkeep_that\n"
        response = _block("delete_this\n", "")
        result = apply_search_replace(code, response)

        assert result.success is True
        assert "delete_this" not in result.new_code
        assert "keep_this" in result.new_code
        assert "keep_that" in result.new_code

    def test_noop_block_search_equals_replace(self):
        """Block where search == replace is a no-op, skips corruption check."""
        code = "x = 1\ny = 2\n"
        response = _block("x = 1", "x = 1")
        result = apply_search_replace(code, response)

        # search == replace → no-op, should pass (corruption check skipped)
        assert result.success is True
        assert result.new_code == code

    def test_multiline_replacement(self):
        """Replace single line with multiple lines."""
        code = "def stub():\n    pass\n"
        response = _block("    pass", "    x = 1\n    y = 2\n    return x + y")
        result = apply_search_replace(code, response)

        assert result.success is True
        assert "x = 1" in result.new_code
        assert "return x + y" in result.new_code
        assert "pass" not in result.new_code

    def test_block_with_special_regex_chars(self):
        """Search text containing regex special chars still works (literal match)."""
        code = 'pattern = re.compile(r"[a-z]+")\n'
        response = _block(
            'pattern = re.compile(r"[a-z]+")',
            'pattern = re.compile(r"[A-Z]+")',
        )
        result = apply_search_replace(code, response)

        assert result.success is True
        assert "[A-Z]+" in result.new_code

    def test_preserve_surrounding_code(self):
        """Edits only affect matched regions, rest is untouched."""
        code = "header\n\ndef target():\n    old_body\n\nfooter\n"
        response = _block("    old_body", "    new_body")
        result = apply_search_replace(code, response)

        assert result.success is True
        assert result.new_code.startswith("header\n")
        assert result.new_code.endswith("footer\n")
        assert "new_body" in result.new_code
