"""
Pyvis v5.1 Symbol Extractor

Extracts public symbols (classes, functions, constants) from Python files
to reduce Hands context from 58K to 32K when Symbol extraction succeeds.

References: pyovis_v5_1.md section 7
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import ast
import re


@dataclass
class ClassSymbol:
    """Class symbol information"""

    name: str
    fields: List[str] = field(default_factory=list)
    methods: List[str] = field(default_factory=list)
    description: str = ""

    def to_summary(self) -> str:
        """Create summary string for Hands context"""
        methods_str = ", ".join(self.methods[:5])  # Limit to 5 methods
        if self.fields:
            fields_str = f" | fields: {', '.join(self.fields[:3])}"
        else:
            fields_str = ""
        return f"- class {self.name}: {self.description}{fields_str} | methods: {methods_str}"


@dataclass
class FunctionSymbol:
    """Function symbol information"""

    name: str
    signature: str
    return_type: str = ""
    description: str = ""
    is_async: bool = False

    def to_summary(self) -> str:
        """Create summary string for Hands context"""
        async_prefix = "async " if self.is_async else ""
        return_str = f" -> {self.return_type}" if self.return_type else ""
        return f"- {async_prefix}def {self.name}{self.signature}{return_str} # {self.description}"


@dataclass
class ConstantSymbol:
    """Constant symbol information"""

    name: str
    type_hint: str = ""
    value: str = ""
    description: str = ""

    def to_summary(self) -> str:
        """Create summary string for Hands context"""
        type_str = f": {self.type_hint}" if self.type_hint else ""
        value_str = (
            f" = {self.value[:30]}" if self.value and len(self.value) < 50 else ""
        )
        return f"- {self.name}{type_str}{value_str} # {self.description}"


@dataclass
class SymbolSummary:
    """Complete symbol summary for a file"""

    file_path: str
    classes: List[ClassSymbol] = field(default_factory=list)
    functions: List[FunctionSymbol] = field(default_factory=list)
    constants: List[ConstantSymbol] = field(default_factory=list)

    def to_context_string(self) -> str:
        """Convert to context string for Hands prompt"""
        lines = [f"## 의존성 심볼 — {self.file_path}\n"]

        if self.classes:
            lines.append("### 클래스")
            for cls in self.classes:
                lines.append(cls.to_summary())
            lines.append("")

        if self.functions:
            lines.append("### 함수/메서드")
            for func in self.functions:
                lines.append(func.to_summary())
            lines.append("")

        if self.constants:
            lines.append("### 상수/타입")
            for const in self.constants:
                lines.append(const.to_summary())
            lines.append("")

        if not any([self.classes, self.functions, self.constants]):
            lines.append("없음")

        return "\n".join(lines)

    def estimate_tokens(self) -> int:
        """Estimate token count of context string"""
        context = self.to_context_string()
        # Rough estimate: 1 token ≈ 4 characters
        return len(context) // 4


class SymbolExtractor:
    """
    Extracts public symbols from Python source code using AST parsing.

    Usage:
        extractor = SymbolExtractor()
        summary = await extractor.extract_from_file("path/to/file.py", code)
        context = summary.to_context_string()

        # Or extract from string
        summary = extractor.extract_from_ast(code, "file.py")
    """

    def __init__(self, brain_client=None):
        """
        Initialize Symbol Extractor.

        Args:
            brain_client: Optional Brain client for LLM-based description generation
        """
        self.brain = brain_client

    def extract_from_ast(
        self, code: str, file_path: str = "unknown.py"
    ) -> SymbolSummary:
        """
        Extract symbols from Python code using AST parsing.

        Args:
            code: Python source code
            file_path: File path for reference

        Returns:
            SymbolSummary with extracted symbols
        """
        summary = SymbolSummary(file_path=file_path)

        try:
            tree = ast.parse(code)
        except SyntaxError:
            # If AST parsing fails, return empty summary
            return summary

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                summary.classes.append(self._extract_class(node))
            elif isinstance(node, ast.FunctionDef) or isinstance(
                node, ast.AsyncFunctionDef
            ):
                summary.functions.append(self._extract_function(node))
            elif isinstance(node, ast.Assign) or isinstance(node, ast.AnnAssign):
                const = self._extract_constant(node)
                if const:
                    summary.constants.append(const)

        return summary

    def _extract_class(self, node: ast.ClassDef) -> ClassSymbol:
        """Extract class symbol"""
        cls = ClassSymbol(
            name=node.name,
            description=f"Base classes: {[base.id for base in node.bases if isinstance(base, ast.Name)]}",
        )

        for item in node.body:
            if isinstance(item, ast.FunctionDef) or isinstance(
                item, ast.AsyncFunctionDef
            ):
                # Only include public methods (not starting with _)
                if not item.name.startswith("_") or item.name in (
                    "__init__",
                    "__str__",
                    "__repr__",
                ):
                    cls.methods.append(item.name)
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                if not item.target.id.startswith("_"):
                    cls.fields.append(item.target.id)
            elif isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("_"):
                        cls.fields.append(target.id)

        return cls

    def _extract_function(self, node) -> FunctionSymbol:
        """Extract function symbol"""
        is_async = isinstance(node, ast.AsyncFunctionDef)

        # Build signature
        args = []
        for arg in node.args.args:
            arg_str = arg.arg
            if arg.annotation:
                arg_str += f": {ast.unparse(arg.annotation)}"
            args.append(arg_str)

        # Add *args and **kwargs
        if node.args.vararg:
            args.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            args.append(f"**{node.args.kwarg.arg}")

        signature = f"({', '.join(args)})"

        # Get return type
        return_type = ""
        if node.returns:
            return_type = ast.unparse(node.returns)

        return FunctionSymbol(
            name=node.name,
            signature=signature,
            return_type=return_type,
            is_async=is_async,
            description=self._get_docstring(node),
        )

    def _extract_constant(self, node) -> Optional[ConstantSymbol]:
        """Extract constant/variable symbol"""
        if isinstance(node, ast.AnnAssign):
            # Typed assignment: NAME: type = value
            if isinstance(node.target, ast.Name) and node.target.id.isupper():
                type_hint = ""
                if node.annotation:
                    type_hint = ast.unparse(node.annotation)

                value = ""
                if node.value:
                    value = ast.unparse(node.value)

                return ConstantSymbol(
                    name=node.target.id, type_hint=type_hint, value=value
                )

        elif isinstance(node, ast.Assign):
            # Simple assignment: NAME = value
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    value = ""
                    if node.value:
                        value = ast.unparse(node.value)

                    return ConstantSymbol(name=target.id, value=value)

        return None

    def _get_docstring(self, node) -> str:
        """Extract docstring from function/class"""
        docstring = ast.get_docstring(node)
        if docstring:
            # Return first line only
            return docstring.split("\n")[0][:100]
        return ""

    async def extract_with_descriptions(
        self, code: str, file_path: str = "unknown.py"
    ) -> SymbolSummary:
        """
        Extract symbols with LLM-generated descriptions.

        Args:
            code: Python source code
            file_path: File path for reference

        Returns:
            SymbolSummary with AI-generated descriptions
        """
        # First extract symbols using AST
        summary = self.extract_from_ast(code, file_path)

        # If Brain client available, generate descriptions
        if self.brain:
            await self._generate_descriptions(summary, code)

        return summary

    async def _generate_descriptions(self, summary: SymbolSummary, code: str):
        """Generate descriptions using Brain LLM"""
        # Prepare prompt for description generation
        symbols_list = []
        for cls in summary.classes:
            symbols_list.append(f"class {cls.name}")
        for func in summary.functions:
            symbols_list.append(f"def {func.name}")

        if not symbols_list:
            return

        prompt = f"""Analyze this Python code and provide one-line descriptions for each symbol.

Code:
```python
{code[:2000]}  # Limit code context
```

Symbols to describe:
{", ".join(symbols_list)}

Respond in JSON format:
{{
  "descriptions": {{
    "symbol_name": "one-line description"
  }}
}}
"""

        try:
            response = await self.brain.call(prompt)
            # Parse response and update descriptions
            # (Implementation depends on Brain API)
        except Exception:
            # If description generation fails, continue without descriptions
            pass

    def format_for_hands(self, summary: SymbolSummary) -> str:
        """
        Format symbol summary for Hands prompt insertion.

        Args:
            summary: SymbolSummary object

        Returns:
            Formatted context string
        """
        return summary.to_context_string()

    def estimate_context_reduction(
        self, full_code: str, summary: SymbolSummary
    ) -> Dict[str, Any]:
        """
        Estimate context reduction from using symbol summary.

        Args:
            full_code: Original full code
            summary: Extracted symbol summary

        Returns:
            Dictionary with reduction statistics
        """
        full_tokens = len(full_code) // 4
        summary_tokens = summary.estimate_tokens()

        return {
            "full_code_tokens": full_tokens,
            "summary_tokens": summary_tokens,
            "reduction_tokens": full_tokens - summary_tokens,
            "reduction_percent": ((full_tokens - summary_tokens) / full_tokens * 100)
            if full_tokens > 0
            else 0,
            "symbols_extracted": {
                "classes": len(summary.classes),
                "functions": len(summary.functions),
                "constants": len(summary.constants),
            },
        }


def extract_symbols_quick(code: str, file_path: str = "unknown.py") -> str:
    """
    Quick symbol extraction without Brain client.

    Args:
        code: Python source code
        file_path: File path for reference

    Returns:
        Formatted context string for Hands
    """
    extractor = SymbolExtractor()
    summary = extractor.extract_from_ast(code, file_path)
    return extractor.format_for_hands(summary)


# Convenience function for SwapManager integration


def get_hands_context_config(symbol_extraction_success: bool) -> Dict[str, Any]:
    """
    Get Hands context configuration based on symbol extraction status.

    Args:
        symbol_extraction_success: Whether symbol extraction succeeded

    Returns:
        Dictionary with ctx_size and kv_cache settings
    """
    if symbol_extraction_success:
        # Normal mode: 32K context, q8_0 KV cache
        return {"ctx_size": 32768, "kv_cache": "q8_0", "mode": "normal"}
    else:
        # Fallback mode: 58K context, q4_0 KV cache
        return {"ctx_size": 58368, "kv_cache": "q4_0", "mode": "fallback"}
