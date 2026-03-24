from datetime import datetime
from typing import Dict

from app.core.firebase import db
from app.models.extracted_expense import ExtractedExpense


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
            "category": expense.category,
            "description": expense.description,
            "confidence": expense.confidence,
            "source": "whatsapp user's chat",
            "created_at": datetime.utcnow(),
        }

        doc_ref.set(expense_data)

        return {
            "expense_id": doc_ref.id,
            "status": "stored"
        }
