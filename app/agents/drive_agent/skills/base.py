from abc import ABC, abstractmethod

from app.agents.drive_agent.skill_context import SkillContext, SkillResult


class DriveSkill(ABC):
    """Base class for all DriveAgent skills.

    Rules (mirrored from ListSkill / TravelSkill):
    - No LLM calls.
    - No WhatsApp calls (no send_whatsapp_message).
    - No user-facing string composition.
    - No routing decisions.
    - Firestore / Drive API I/O is allowed (the domain's tools).
    - Return SkillResult. Never AgentResult — the Agent wraps that.
    - Never write to a Drive file. Writes only happen in apply_modification,
      and only after the pending-gate confirmation.
    """
    name: str  # class-level constant, e.g. "read_file"

    @abstractmethod
    def execute(self, ctx: SkillContext) -> SkillResult:
        ...
