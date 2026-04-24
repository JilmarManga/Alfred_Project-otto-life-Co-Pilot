from app.agents.base_agent import BaseAgent
from app.db.user_context_store import update_user_context
from app.models.agent_result import AgentResult
from app.models.parsed_message import ParsedMessage


class TypeClarifyAgent(BaseAgent):
    """
    Fires when a small whole number in a calendar context is ambiguous between
    a clock time and an expense amount. Stashes the parsed context and returns
    a question asking the user to choose: calendar or expense?

    The response is intercepted by handle_pending_type_clarify before the next
    message reaches the normal pipeline.
    """

    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        phone = user.get("phone_number", "")
        update_user_context(phone, "pending_type_clarify", {
            "amount": parsed.amount,
            "category": parsed.category_hint or "other",
            "raw_message": parsed.raw_message,
            "event_title": parsed.event_title,
            "event_location": parsed.event_location,
            "date_hint": parsed.date_hint,
        })
        return AgentResult(
            agent_name="TypeClarifyAgent",
            success=True,
            data={
                "type": "expense_or_calendar_clarify",
                "event_title": parsed.event_title,
                "event_location": parsed.event_location,
                "amount": parsed.amount,
            },
        )
