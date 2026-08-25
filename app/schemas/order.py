from datetime import datetime
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

from app.db.models.restaurant import OrderStatus


class OrderItemCreate(BaseModel):
    item_name: str = Field(min_length=1, max_length=150)
    quantity: int = Field(default=1, gt=0, le=100)
    unit_price: Decimal = Field(default=Decimal("0"))


class OrderItemRead(BaseModel):
    id: int
    item_name: str
    quantity: int
    unit_price: Decimal
    model_config = ConfigDict(from_attributes=True)


class OrderCreate(BaseModel):
    restaurant_id: int
    customer_id: UUID | None = None
    notes: str | None = None
    items: list[OrderItemCreate] = Field(min_length=1)


class OrderStatusUpdate(BaseModel):
    status: OrderStatus


class OrderRead(BaseModel):
    id: int
    restaurant_id: int
    customer_id: UUID | None
    status: OrderStatus
    total_amount: Decimal
    notes: str | None
    delivery_destination: str | None
    estimated_delivery_minutes: int | None
    confirmed_at: datetime | None
    created_at: datetime
    items: list[OrderItemRead]
    model_config = ConfigDict(from_attributes=True)
