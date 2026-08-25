from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field

from app.db.models.restaurant import PaymentStatus


class PaymentCreate(BaseModel):
    order_id: int
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    method: str
    transaction_reference: str | None = None


class PaymentStatusUpdate(BaseModel):
    status: PaymentStatus


class PaymentRead(PaymentCreate):
    id: int
    status: PaymentStatus
    paid_at: datetime | None
    model_config = ConfigDict(from_attributes=True)
