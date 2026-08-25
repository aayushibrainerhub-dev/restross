from datetime import datetime
from sqlalchemy.orm import Session

from app.core.exceptions import not_found
from app.db.models.restaurant import Order, Payment, PaymentStatus
from app.schemas.payment import PaymentCreate


def create_payment(db: Session, payload: PaymentCreate) -> Payment:
    order = db.get(Order, payload.order_id)
    if not order: raise not_found("Order", payload.order_id)
    payment = Payment(**payload.model_dump(), status=PaymentStatus.pending)
    db.add(payment); db.commit(); db.refresh(payment)
    return payment


def mark_paid(payment: Payment) -> None:
    if payment.status == PaymentStatus.paid: payment.paid_at = payment.paid_at or datetime.utcnow()
