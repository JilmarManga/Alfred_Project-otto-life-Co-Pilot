import re
import logging
from app.agents.base_agent import BaseAgent
from app.models.parsed_message import ParsedMessage
from app.models.agent_result import AgentResult
from app.models.extracted_expense import ExtractedExpense
from app.repositories.expense_repository import ExpenseRepository
from app.parser.word_number_parser import parse_word_numbers

logger = logging.getLogger(__name__)

CATEGORY_KEYWORDS = {
    "food": ["almuerzo", "desayuno", "cena", "comida", "pan", "restaurante",
             "coffee", "café", "cafe", "lunch", "breakfast", "dinner", "food"],
    "transport": ["uber", "taxi", "bus", "metro", "moto", "gasolina", "gas",
                  "transporte", "peaje", "pasaje", "fuel", "ride"],
    "shopping": ["tienda", "compré", "comprar", "ropa", "zapatos", "mercado",
                 "shopping", "clothes", "shoes", "store", "mall"],
    "health": ["medicina", "doctor", "hospital", "salud", "farmacia",
               "medicine", "pharmacy", "health", "clinic"],
}


class ExpenseAgent(BaseAgent):

    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        try:
            amount = parsed.amount
            text = (parsed.raw_message or "").lower()
            phone = user.get("phone_number", "")
            logger.info("ExpenseAgent executing — phone=%s raw_amount=%s raw_message=%r", phone, amount, parsed.raw_message)
            preferred_currency = user.get("preferred_currency")

            # Safety net: parser returned small number but "mil" is in message
            if amount and amount < 1000 and "mil" in text:
                amount = amount * 1000

            # Secondary: try numeric patterns from raw text
            if not amount:
                number_matches = re.findall(r"\d[\d\.,]*", text)
                if number_matches:
                    raw = number_matches[0].replace(".", "").replace(",", "")
                    try:
                        amount = float(raw)
                    except ValueError:
                        pass

            # Last resort: word-number parser
            if not amount:
                amount = parse_word_numbers(text)

            if not amount:
                return AgentResult(
                    agent_name="ExpenseAgent",
                    success=False,
                    error_message="No se pudo extraer el monto del mensaje.",
                )

            # Currency: explicit in message → use and silently lock in; else use preferred;
            # else stash pending and ask user which currency (onboarding V1.0.0 — no currency question at signup).
            explicit_currency = None
            if "peso" in text or "cop" in text or "colombian" in text:
                explicit_currency = "COP"
            elif "dolar" in text or "usd" in text or "dollar" in text:
                explicit_currency = "USD"
            elif parsed.currency:
                explicit_currency = parsed.currency

            if explicit_currency:
                currency = explicit_currency
                if not preferred_currency:
                    try:
                        from app.repositories.user_repository import UserRepository
                        UserRepository.create_or_update_user(phone, {"preferred_currency": currency})
                    except Exception as e:
                        logger.warning("Failed to silently save preferred_currency: %s", e)
            elif preferred_currency:
                currency = preferred_currency
            else:
                from app.db.user_context_store import update_user_context
                update_user_context(phone, "pending_expense", {
                    "amount": amount,
                    "category": parsed.category_hint or "other",
                    "raw_message": parsed.raw_message,
                })
                return AgentResult(
                    agent_name="ExpenseAgent",
                    success=False,
                    data={"needs_currency": True, "amount": amount},
                    error_message="needs_currency",
                )

            # Category from hint or keyword scan
            VALID_CATEGORIES = {"food", "transport", "shopping", "health", "other"}
            category = parsed.category_hint or "other"
            if category not in VALID_CATEGORIES:
                # LLM returned a non-standard hint (e.g. "housing", "rent") — try keyword scan
                category = "other"
                for cat, keywords in CATEGORY_KEYWORDS.items():
                    if any(kw in text for kw in keywords):
                        category = cat
                        break
            elif category not in CATEGORY_KEYWORDS:
                # Hint matches a valid literal but skip scan since it's already valid
                pass
            else:
                # Valid hint, but still scan to improve accuracy if hint is just "other"
                if category == "other":
                    for cat, keywords in CATEGORY_KEYWORDS.items():
                        if any(kw in text for kw in keywords):
                            category = cat
                            break

            expense = ExtractedExpense(
                amount=amount,
                currency=currency,
                category=category,
                description=parsed.raw_message,
                confidence=0.9,
            )

            logger.info("ExpenseAgent saving — phone=%s amount=%s currency=%s category=%s", phone, amount, currency, category)
            result = ExpenseRepository.save_expense(
                user_phone_number=phone,
                expense=expense,
            )
            logger.info("ExpenseAgent saved — expense_id=%s", result.get("expense_id"))

            return AgentResult(
                agent_name="ExpenseAgent",
                success=True,
                data={
                    "amount": amount,
                    "currency": currency,
                    "category": category,
                    "expense_id": result.get("expense_id"),
                    "raw_message": parsed.raw_message,
                },
            )

        except Exception as e:
            logger.error("ExpenseAgent failed for %s: %s", user.get("phone_number", "?"), e)
            return AgentResult(
                agent_name="ExpenseAgent",
                success=False,
                error_message=str(e),
            )
