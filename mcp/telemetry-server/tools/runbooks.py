"""list_runbooks / read_runbook -- Class 0 (harmless read) tools.

Serves plain markdown files from the top-level runbooks/ directory. This is the one
tool in this package that isn't backed by ClickHouse -- it's local filesystem access,
read-only, scoped to a single directory (no path traversal: topics are matched against
a pre-scanned filename list, never opened by a caller-supplied path).
"""

from __future__ import annotations

import os
import re

from tools.common import ToolInputError

_DEFAULT_DIR = os.environ.get("RUNBOOKS_DIR", "runbooks")


def _runbook_files(runbooks_dir: str) -> list[str]:
    if not os.path.isdir(runbooks_dir):
        return []
    return sorted(f for f in os.listdir(runbooks_dir) if f.endswith(".md"))


def _title_of(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("#"):
                    return line.lstrip("#").strip()
    except OSError:
        pass
    return os.path.splitext(os.path.basename(path))[0]


def _normalize(topic: str) -> str:
    return re.sub(r"[\s_-]+", "", topic.lower())


async def list_runbooks(runbooks_dir: str | None = None) -> dict:
    """List every runbook available to read_runbook, with its topic key and title."""
    directory = runbooks_dir or _DEFAULT_DIR
    files = _runbook_files(directory)
    runbooks = []
    for f in files:
        topic = os.path.splitext(f)[0]
        runbooks.append({"topic": topic, "title": _title_of(os.path.join(directory, f))})
    return {"runbooks": runbooks}


async def read_runbook(topic: str, runbooks_dir: str | None = None) -> dict:
    """Read one runbook's full markdown content by topic (e.g. 'db-pool-exhaustion').

    Matching is forgiving of case, spaces, hyphens, and underscores, so 'DB Pool
    Exhaustion' and 'db_pool_exhaustion' both resolve to the same file.
    """
    directory = runbooks_dir or _DEFAULT_DIR
    files = _runbook_files(directory)
    if not files:
        raise ToolInputError(f"no runbooks found in {directory!r}")

    target = _normalize(topic)
    for f in files:
        stem = os.path.splitext(f)[0]
        if _normalize(stem) == target:
            with open(os.path.join(directory, f), encoding="utf-8") as fh:
                content = fh.read()
            return {"topic": stem, "content": content}

    available = [os.path.splitext(f)[0] for f in files]
    raise ToolInputError(f"no runbook matching {topic!r}; available topics: {', '.join(available)}")
