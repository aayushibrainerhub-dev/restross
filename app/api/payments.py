# from fastapi import APIRouter, Depends, status
# from sqlalchemy.orm import Session

# from app.core.exceptions import not_found
# from app.db.database import get_db
# from app.db.models.restaurant import Payment
# from app.schemas.payment import PaymentCreate, PaymentRead, PaymentStatusUpdate
# from app.services.payment_service import create_payment, mark_paid

# router = APIRouter(prefix="/payments", tags=["payments"])

# @router.post("", response_model=PaymentRead, status_code=status.HTTP_201_CREATED)
# def create(payload: PaymentCreate, db: Session = Depends(get_db)):
#     return create_payment(db, payload)

# @router.patch("/{payment_id}/status", response_model=PaymentRead)
# def change_status(payment_id: int, payload: PaymentStatusUpdate, db: Session = Depends(get_db)):
#     payment = db.get(Payment, payment_id)
#     if not payment: raise not_found("Payment", payment_id)
#     payment.status = payload.status
#     mark_paid(payment)
#     db.commit(); db.refresh(payment)
#     return payment
