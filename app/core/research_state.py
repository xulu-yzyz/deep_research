from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ResearchStatus = Literal["planning", "researching", "critiquing", "writing", "done", "failed"]


@dataclass
class Evidence:
    source: str
    title: str
    url: str
    snippet: str
    credibility: float = 0.5


@dataclass
class ResearchTask:
    id: str
    question: str
    reason: str
    status: Literal["pending", "running", "done", "failed"] = "pending"
    answer: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    confidence: float = 0.0
    observations: list[dict[str, Any]] = field(default_factory=list)

@dataclass
class ResearchState:
    uid: int
    topic: str
    domain: str
    user_request: str = ""
    preferences: dict[str, Any] = field(default_factory=dict)
    memory_context: str = ""

    status: ResearchStatus = "planning"
    plan: list[str] = field(default_factory=list)
    tasks: list[ResearchTask] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    report: str = ""

    iteration: int = 0
    max_iterations: int = 8

    working_memory: dict[str, Any] = field(default_factory=dict)
    session_constraints: dict[str, Any] = field(default_factory=dict)

    def pending_tasks(self) -> list[ResearchTask]:
        return [t for t in self.tasks if t.status == "pending"]

    def is_done(self) -> bool:
        return self.status == "done" or self.iteration >= self.max_iterations