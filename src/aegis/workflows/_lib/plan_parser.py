"""Parse a markdown plan into a task list.

Tasks are ``## Slice <N> — <title>`` headings (em-dash or ASCII hyphen).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Task:
    id: str
    title: str
    body: str


@dataclass
class Plan:
    title: str
    tasks: list[Task]


_TASK_RE = re.compile(
    r"^##\s+Slice\s+(\d+)\s+[\u2014\-]\s+(.+)$", re.MULTILINE)


def parse_plan(path: str | Path) -> Plan:
    text = Path(path).read_text(encoding="utf-8")
    title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else str(path)
    matches = list(_TASK_RE.finditer(text))
    tasks: list[Task] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        tasks.append(Task(
            id=f"slice-{m.group(1)}",
            title=m.group(2).strip(),
            body=body))
    return Plan(title=title, tasks=tasks)
