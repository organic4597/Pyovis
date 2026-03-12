"""
Tests for Pyvis v5.1 Symbol Extractor

Tests cover:
- AST-based symbol extraction
- Class/function/constant extraction
- Context string formatting
- Token estimation
"""

import importlib

pytest = importlib.import_module("pytest")
from pyovis.orchestration.symbol_extractor import (
    SymbolExtractor,
    SymbolSummary,
    ClassSymbol,
    FunctionSymbol,
    ConstantSymbol,
    extract_symbols_quick,
    get_hands_context_config,
)


class TestClassSymbol:
    """Test ClassSymbol"""

    def test_class_symbol_creation(self):
        cls = ClassSymbol(
            name="UserService",
            fields=["name", "email"],
            methods=["get_user", "update_user"],
            description="Manages user operations",
        )

        assert cls.name == "UserService"
        assert len(cls.fields) == 2
        assert len(cls.methods) == 2

    def test_class_symbol_to_summary(self):
        cls = ClassSymbol(
            name="Auth",
            fields=["token", "user_id"],
            methods=["login", "logout", "refresh"],
            description="Authentication handler",
        )

        summary = cls.to_summary()

        assert "class Auth" in summary
        assert "Authentication handler" in summary
        assert "fields:" in summary
        assert "methods:" in summary


class TestFunctionSymbol:
    """Test FunctionSymbol"""

    def test_function_symbol_creation(self):
        func = FunctionSymbol(
            name="calculate_total",
            signature="(items: list, tax: float)",
            return_type="float",
            description="Calculate total with tax",
            is_async=False,
        )

        assert func.name == "calculate_total"
        assert func.return_type == "float"
        assert func.is_async is False

    def test_function_symbol_to_summary_sync(self):
        func = FunctionSymbol(
            name="get_user",
            signature="(user_id: int)",
            return_type="dict",
            is_async=False,
        )

        summary = func.to_summary()

        assert "def get_user" in summary
        assert "user_id: int" in summary
        assert "-> dict" in summary
        assert "async" not in summary

    def test_function_symbol_to_summary_async(self):
        func = FunctionSymbol(
            name="fetch_data", signature="(url: str)", return_type="str", is_async=True
        )

        summary = func.to_summary()

        assert "async def fetch_data" in summary
        assert "url: str" in summary


class TestConstantSymbol:
    """Test ConstantSymbol"""

    def test_constant_symbol_creation(self):
        const = ConstantSymbol(
            name="MAX_RETRIES",
            type_hint="int",
            value="3",
            description="Maximum retry count",
        )

        assert const.name == "MAX_RETRIES"
        assert const.type_hint == "int"
        assert const.value == "3"

    def test_constant_symbol_to_summary(self):
        const = ConstantSymbol(
            name="API_VERSION",
            type_hint="str",
            value="v1.0",
            description="Current API version",
        )

        summary = const.to_summary()

        assert "API_VERSION" in summary
        assert "str" in summary
        assert "Current API version" in summary


class TestSymbolSummary:
    """Test SymbolSummary"""

    def test_symbol_summary_creation(self):
        summary = SymbolSummary(file_path="test.py")

        assert summary.file_path == "test.py"
        assert len(summary.classes) == 0
        assert len(summary.functions) == 0
        assert len(summary.constants) == 0

    def test_symbol_summary_to_context_string_empty(self):
        summary = SymbolSummary(file_path="empty.py")
        context = summary.to_context_string()

        assert "의존성 심볼" in context
        assert "empty.py" in context
        assert "없음" in context

    def test_symbol_summary_to_context_string_with_symbols(self):
        summary = SymbolSummary(file_path="test.py")
        summary.classes.append(
            ClassSymbol(name="TestClass", description="A test class")
        )
        summary.functions.append(FunctionSymbol(name="test_func", signature="()"))
        summary.constants.append(ConstantSymbol(name="CONST", value="1"))

        context = summary.to_context_string()

        assert "클래스" in context
        assert "함수/메서드" in context
        assert "상수/타입" in context
        assert "TestClass" in context
        assert "test_func" in context
        assert "CONST" in context

    def test_symbol_summary_estimate_tokens(self):
        summary = SymbolSummary(file_path="test.py")
        summary.classes.append(ClassSymbol(name="LargeClass", description="x" * 100))

        tokens = summary.estimate_tokens()
        assert tokens > 0


class TestSymbolExtractor:
    """Test SymbolExtractor"""

    def test_extractor_creation(self):
        extractor = SymbolExtractor()
        assert extractor is not None

    def test_extract_from_ast_simple(self):
        code = """
class UserService:
    def __init__(self):
        self.name = ""
    
    def get_user(self, user_id: int) -> dict:
        \"\"\"Get user by ID\"\"\"
        return {}

def helper_function():
    pass

MAX_USERS = 100
"""
        extractor = SymbolExtractor()
        summary = extractor.extract_from_ast(code, "test.py")

        assert summary.file_path == "test.py"
        assert len(summary.classes) == 1
        assert len(summary.functions) >= 1
        assert len(summary.constants) >= 1

    def test_extract_class(self):
        code = """
class AuthManager:
    def __init__(self):
        self.token = None
    
    def login(self, username: str, password: str) -> bool:
        pass
    
    def logout(self):
        pass
    
    def _private_method(self):
        pass
"""
        extractor = SymbolExtractor()
        summary = extractor.extract_from_ast(code, "auth.py")

        assert len(summary.classes) == 1
        cls = summary.classes[0]
        assert cls.name == "AuthManager"
        assert "login" in cls.methods
        assert "logout" in cls.methods
        assert "_private_method" not in cls.methods  # Private methods excluded

    def test_extract_function(self):
        code = """
async def fetch_data(url: str, timeout: int = 30) -> str:
    \"\"\"Fetch data from URL\"\"\"
    pass

def calculate(a: int, b: int) -> int:
    return a + b
"""
        extractor = SymbolExtractor()
        summary = extractor.extract_from_ast(code, "funcs.py")

        assert len(summary.functions) == 2

        fetch_func = next(f for f in summary.functions if f.name == "fetch_data")
        assert fetch_func.is_async is True
        assert "url: str" in fetch_func.signature
        assert fetch_func.return_type == "str"

        calc_func = next(f for f in summary.functions if f.name == "calculate")
        assert calc_func.is_async is False

    def test_extract_constant(self):
        code = """
MAX_RETRIES: int = 3
API_VERSION = "v1.0"
DATABASE_URL: str = "postgresql://localhost"
_private_var = "should not be extracted"
"""
        extractor = SymbolExtractor()
        summary = extractor.extract_from_ast(code, "constants.py")

        # Uppercase constants should be extracted
        assert len(summary.constants) >= 2
        names = [c.name for c in summary.constants]
        assert "MAX_RETRIES" in names
        assert "API_VERSION" in names
        assert "_private_var" not in names

    def test_extract_with_syntax_error(self):
        code = "def broken(\n"  # Invalid syntax
        extractor = SymbolExtractor()
        summary = extractor.extract_from_ast(code, "broken.py")

        # Should return empty summary, not crash
        assert summary.file_path == "broken.py"
        assert len(summary.classes) == 0
        assert len(summary.functions) == 0

    def test_get_docstring(self):
        code = """
def documented_func():
    \"\"\"This is a docstring.
    Multiple lines.
    \"\"\"
    pass
"""
        extractor = SymbolExtractor()
        summary = extractor.extract_from_ast(code, "docs.py")

        assert len(summary.functions) == 1
        func = summary.functions[0]
        assert "This is a docstring" in func.description


class TestExtractSymbolsQuick:
    """Test extract_symbols_quick function"""

    def test_extract_symbols_quick(self):
        code = """
class Test:
    def method(self):
        pass

CONST = 42
"""
        context = extract_symbols_quick(code, "quick.py")

        assert "Test" in context
        assert "method" in context
        assert "CONST" in context

    def test_extract_graph(self):
        code = """
class Service:
    def run(self):
        pass

VALUE = 1
"""
        extractor = SymbolExtractor()
        graph = extractor.extract_graph(code, "service.py")

        assert graph["module"]["id"] == "module:service.py"
        assert any(
            symbol["name"] == "Service" and symbol["kind"] == "class"
            for symbol in graph["symbols"]
        )
        assert any(
            symbol["name"] == "run" and symbol["kind"] == "method"
            for symbol in graph["symbols"]
        )
        assert any(
            symbol["name"] == "VALUE" and symbol["kind"] == "constant"
            for symbol in graph["symbols"]
        )
        assert any(edge["relation"] == "defines" for edge in graph["edges"])


class TestGetHandsContextConfig:
    """Test get_hands_context_config function"""

    def test_normal_mode(self):
        config = get_hands_context_config(symbol_extraction_success=True)

        assert config["ctx_size"] == 32768
        assert config["kv_cache"] == "q8_0"
        assert config["mode"] == "normal"

    def test_fallback_mode(self):
        config = get_hands_context_config(symbol_extraction_success=False)

        assert config["ctx_size"] == 58368
        assert config["kv_cache"] == "q4_0"
        assert config["mode"] == "fallback"


class TestContextReduction:
    """Test context reduction estimation"""

    def test_estimate_reduction(self):
        full_code = (
            """
class LargeClass:
    def method1(self): pass
    def method2(self): pass
    # ... many more lines
"""
            * 100
        )  # Make it large

        extractor = SymbolExtractor()
        summary = extractor.extract_from_ast(full_code, "large.py")

        reduction = extractor.estimate_context_reduction(full_code, summary)

        assert reduction["full_code_tokens"] > 0
        assert reduction["summary_tokens"] > 0
        assert reduction["reduction_tokens"] > 0
        assert reduction["reduction_percent"] > 0
        assert "symbols_extracted" in reduction


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
