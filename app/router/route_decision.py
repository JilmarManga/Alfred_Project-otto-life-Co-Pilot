from dataclasses import dataclass, field
from typing import List, Optional

from app.agents.base_agent import BaseAgent


@dataclass
class Disambiguation:
    """Two candidate agents matched — ask the user which one they meant."""
    candidates: List[str] = field(default_factory=list)  # agent class names


@dataclass
class RouteDecision:
    """Return type of `route()`.

    Exactly one of `agent` / `disambiguation` is set in normal flow.
    `agent` carries the resolved BaseAgent when routing is unambiguous.
    `disambiguation` carries the candidate list when two or more agents match
    and the pipeline must ask the user to choose.
    """
    agent: Optional[BaseAgent] = None
    disambiguation: Optional[Disambiguation] = None
