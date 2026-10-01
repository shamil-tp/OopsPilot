"""Static syntax checks of pushed files: deterministic, free, and they never execute code.

Each reviewed Python, JavaScript or TypeScript file of a push is fetched at the pushed commit and
only *parsed*: Python with the standard library's `ast`, JS/JSX/TS/TSX with tree-sitter. Nothing is
installed, imported, built or run, and the project's own tool configuration is never loaded. A file
that does not parse becomes a critical finding (a syntax error stops the whole file from loading).

If the tree-sitter packages are not installed, JavaScript and TypeScript files are simply not
checked; Python is always checked.
"""

import ast
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_FILES = 10
MAX_BYTES = 200_000

_LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
}
_LABEL = {"python": "Python", "javascript": "JavaScript", "typescript": "TypeScript", "tsx": "TSX"}
_parsers: dict[str, Any] = {}


def language(filename: str) -> str | None:
    dot = filename.rfind(".")
    return _LANGUAGES.get(filename[dot:].lower()) if dot != -1 else None


def _parser(lang: str) -> Any | None:
    """A tree-sitter parser, or None when the optional packages are missing."""
    if lang not in _parsers:
        try:
            from tree_sitter import Language, Parser

            if lang == "javascript":
                import tree_sitter_javascript as grammar

                _parsers[lang] = Parser(Language(grammar.language()))
            else:
                import tree_sitter_typescript as grammar

                factory = grammar.language_tsx if lang == "tsx" else grammar.language_typescript
                _parsers[lang] = Parser(Language(factory()))
        except Exception as exc:  # not installed or incompatible: skip, never fail the review
            logger.warning(
                "static_check_unavailable", extra={"lang": lang, "error": type(exc).__name__}
            )
            _parsers[lang] = None
    return _parsers[lang]


def _first_error_line(node: Any) -> int:
    """1-based line of the first ERROR or MISSING node (depth-first)."""
    stack = [node]
    while stack:
        current = stack.pop()
        if current.is_error or current.is_missing:
            return int(current.start_point[0]) + 1
        stack.extend(reversed(current.children))
    return int(node.start_point[0]) + 1


def check(filename: str, source: str) -> dict[str, Any] | None:
    """A finding (stored like an AI finding, with source "static") or None if the file parses."""
    lang = language(filename)
    if lang is None:
        return None
    detail = ""
    if lang == "python":
        try:
            ast.parse(source, filename=filename)
            return None
        except SyntaxError as exc:
            line, detail = exc.lineno or 1, exc.msg
        except ValueError:  # e.g. null bytes: not a source file we can judge
            return None
    else:
        parser = _parser(lang)
        if parser is None:
            return None
        root = parser.parse(source.encode("utf-8", "replace")).root_node
        if not root.has_error:
            return None
        line = _first_error_line(root)
    name = _LABEL[lang]
    return {
        "severity": "critical",
        "category": "bug",
        "file": filename,
        "line": line,
        "title": f"Syntax error: {detail}"[:160] if detail else f"{name} syntax error",
        "explanation": (
            f"This file does not parse as valid {name} near line {line}. A syntax error stops the "
            "whole file from loading, so the page or service built from it fails."
        ),
        "recommendation": (
            f"Fix the syntax near line {line} (look for a missing or extra bracket, parenthesis, "
            "quote or comma), then build or run the project locally before pushing."
        ),
        "source": "static",
    }


def checkable(files: list[dict[str, Any]]) -> list[str]:
    """Reviewed files worth parsing: known language, not deleted, at most MAX_FILES."""
    names = [
        str(f["filename"])
        for f in files
        if f.get("status") != "removed" and language(str(f.get("filename", "")))
    ]
    return names[:MAX_FILES]
