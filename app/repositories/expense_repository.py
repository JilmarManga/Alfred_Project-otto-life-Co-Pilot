from datetime import datetime
from typing import Dict

from app.core.firebase import db
from app.models.extracted_expense import ExtractedExpense
from datetime import datetime


class ExpenseRepository:
    """
    Repository responsible for persisting expense data into Firestore.
    """

    COLLECTION_NAME = "expenses"

    @staticmethod
    def save_expense(user_phone_number: str, expense: ExtractedExpense) -> Dict:
        """
        Save an expense document into Firestore.
        """

        doc_ref = db.collection(ExpenseRepository.COLLECTION_NAME).document()

        expense_data = {
            "user_phone_number": user_phone_number,
            "amount": expense.amount,
            "currency": expense.currency,
            "user_message": expense.description,
            "category": expense.category,
            "confidence": expense.confidence,
            "source": "whatsapp user's chat",
            "created_at": datetime.utcnow(),
        }

        doc_ref.set(expense_data)

        return {
            "expense_id": doc_ref.id,
            "status": "stored"
        }

    @staticmethod
    def get_expenses_by_date_range(user_phone_number: str, start_date: datetime, end_date: datetime):
            """
            Retrieve expenses for a given user and date range
            """
            expenses_ref = db.collection("expenses")
            query = (
                expenses_ref
                .where("user_phone_number", "==", user_phone_number)
                .where("created_at", ">=", start_date)
                .where("created_at", "<=", end_date)
                )

            docs = query.stream()

            expenses = []
            for doc in docs:
                expenses.append(doc.to_dict())
            return expenses