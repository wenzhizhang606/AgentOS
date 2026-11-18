"""Container entry point for the HumanEval pure-function subset."""

import ast
import builtins
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import signal
import sys


ALLOWED_MODULES = frozenset({
    "__future__", "array", "bisect", "collections", "copy", "decimal", "enum",
    "fractions", "functools", "heapq", "itertools", "math", "operator", "re",
    "statistics", "string", "typing",
})
BLOCKED_NAMES = frozenset({
    "breakpoint", "compile", "delattr", "dir", "eval", "exec", "getattr",
    "globals", "help", "input", "locals", "open", "setattr", "vars",
})


class RuntimeLimit(Exception):
    pass


def elapsed(signum, frame):
    raise RuntimeLimit()


def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name.split(".")[0] not in ALLOWED_MODULES:
        raise ImportError("Module is outside the algorithmic Python subset")
    return builtins.__import__(name, globals, locals, fromlist, level)


def algorithmic_code(code):
    for node in ast.walk(code):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] not in ALLOWED_MODULES for alias in node.names):
                return False
        if isinstance(node, ast.ImportFrom):
            if node.level or not node.module or node.module.split(".")[0] not in ALLOWED_MODULES:
                return False
            if any(alias.name == "*" or alias.name.startswith("_") for alias in node.names):
                return False
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            return False
        if isinstance(node, ast.Name) and (node.id.startswith("__") or node.id in BLOCKED_NAMES):
            return False
    return True


def judge(request):
    entry = request["entry_point"]
    test = compile(request["test"], "<benchmark checks>", "exec")
    try:
        tree = ast.parse(request["candidate"], filename="<candidate>")
        code = compile(tree, "<candidate>", "exec")
    except SyntaxError:
        return "invalid_python"
    if not algorithmic_code(tree):
        return "unsupported_code"
    allowed_builtins = {
        name: value for name, value in vars(builtins).items()
        if not name.startswith("_") and name not in BLOCKED_NAMES
    }
    allowed_builtins["__import__"] = safe_import
    allowed_builtins["__build_class__"] = builtins.__build_class__
    scope = {"__builtins__": allowed_builtins, "__name__": "candidate"}
    verifier = {}
    exec(test, verifier)
    signal.signal(signal.SIGALRM, elapsed)
    signal.setitimer(signal.ITIMER_REAL, 3)
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            exec(code, scope)
            candidate = scope.get(entry)
            if not callable(candidate):
                return "missing_entry_point"
            verifier["check"](candidate)
    except AssertionError:
        return "test_failed"
    except RuntimeLimit:
        return "runtime_timeout"
    except BaseException:
        return "runtime_error"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    return "passed"


if __name__ == "__main__":
    print(json.dumps({"verdict": judge(json.load(sys.stdin))}))
