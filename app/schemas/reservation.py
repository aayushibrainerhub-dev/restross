from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

from app.db.models.restaurant import ReservationStatus


class ReservationCreate(BaseModel):
    restaurant_id: int
    customer_id: UUID
    reserved_for: datetime
    party_size: int = Field(gt=0, le=100)
    notes: str | None = None


class ReservationStatusUpdate(BaseModel):
    status: ReservationStatus


class ReservationRead(ReservationCreate):
    id: int
    status: ReservationStatus
    model_config = ConfigDict(from_attributes=True)
