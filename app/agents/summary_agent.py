from datetime import datetime, timedelta
from app.agents.base_agent import BaseAgent
from app.models.parsed_message import ParsedMessage
from app.models.agent_result import AgentResult
from app.repositories.expense_repository import ExpenseRepository


class SummaryAgent(BaseAgent):

    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        try:
            phone = user.get("phone_number", "")
            lang = user.get("language", "es")
            text = (parsed.raw_message or "").lower()
            now = datetime.utcnow()

            # Detect date range — order matters (most specific first)
            if any(s in text for s in ["hoy", "today", "día", "dia", "day"]):
                start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
                label = "hoy" if lang == "es" else "today"
            elif any(s in text for s in ["semana pasada", "last week"]):
                # Last calendar week (Mon–Sun)
                days_since_monday = now.weekday()
                last_monday = now - timedelta(days=days_since_monday + 7)
                start_date = last_monday.replace(hour=0, minute=0, second=0, microsecond=0)
                end_date = start_date + timedelta(days=7)
                label = "semana pasada" if lang == "es" else "last week"
            elif any(s in text for s in ["esta semana", "this week", "semana", "week"]):
                # Current week starting Monday
                days_since_monday = now.weekday()
                start_date = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
                label = "esta semana" if lang == "es" else "this week"
            elif any(s in text for s in ["mes pasado", "last month"]):
                first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                last_month_end = first_of_this_month
                last_month_start = (first_of_this_month - timedelta(days=1)).replace(day=1)
                start_date = last_month_start
                end_date = last_month_end
                label = "mes pasado" if lang == "es" else "last month"
            elif any(s in text for s in ["este mes", "this month", "mes", "month"]):
                start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                label = "este mes" if lang == "es" else "this month"
            elif any(s in text for s in ["este año", "this year", "año", "year"]):
                start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
                label = "este año" if lang == "es" else "this year"
            elif "15" in text:
                start_date = now - timedelta(days=15)
                label = "últimos 15 días" if lang == "es" else "last 15 days"
            else:
                # Default: current week
                days_since_monday = now.weekday()
                start_date = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
                label = "esta semana" if lang == "es" else "this week"

            # Use specific end_date for last week/month, otherwise now
            if 'end_date' not in locals():
                end_date = now

            expenses = ExpenseRepository.get_expenses_by_date_range(phone, start_date, end_date)

            # Aggregate by currency
            totals: dict = {}
            for e in expenses:
                currency = e.get("currency", "USD")
                totals[currency] = totals.get(currency, 0) + e.get("amount", 0)

            return AgentResult(
                agent_name="SummaryAgent",
                success=True,
                data={
                    "label": label,
                    "totals": totals,
                    "expense_count": len(expenses),
                    "lang": lang,
                },
            )

        except Exception as e:
            return AgentResult(
                agent_name="SummaryAgent",
                success=False,
                error_message=str(e),
            )
