"""Classify files by language and stream category (code / doc / config / test / other)."""

from pathlib import PurePosixPath

# Extension -> language. Extend as we add target languages.
LANGUAGE_BY_EXT = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".sh": "shell",
}

DOC_EXTS = {".md", ".rst", ".txt", ".adoc"}
CONFIG_EXTS = {".toml", ".cfg", ".ini", ".yaml", ".yml", ".json", ".env"}
CONFIG_NAMES = {"dockerfile", "makefile", ".gitignore", ".dockerignore", "pyproject.toml"}


def detect_language(path: str) -> str | None:
    return LANGUAGE_BY_EXT.get(PurePosixPath(path).suffix.lower())


def classify(path: str) -> str:
    """Return the stream category for a file path."""
    p = PurePosixPath(path)
    name = p.name.lower()
    ext = p.suffix.lower()
    parts = {part.lower() for part in p.parts}

    # tests
    if "tests" in parts or "test" in parts or name.startswith("test_") or name.endswith("_test.py"):
        return "test"
    # docs
    if ext in DOC_EXTS or "docs" in parts or "doc" in parts or name.startswith("readme"):
        return "doc"
    # code
    if ext in LANGUAGE_BY_EXT:
        return "code"
    # config
    if ext in CONFIG_EXTS or name in CONFIG_NAMES:
        return "config"
    return "other"
