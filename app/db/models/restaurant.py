from sqlalchemy import JSON
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Enum as SqlEnum, ForeignKey, Integer, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class OrderStatus(str, Enum):
    pending = "pending"; confirmed = "confirmed"; preparing = "preparing"; served = "served"; completed = "completed"; cancelled = "cancelled"


class ReservationStatus(str, Enum):
    booked = "booked"; seated = "seated"; completed = "completed"; cancelled = "cancelled"

# no needed
class User(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30), default="staff")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    # One-to-one: a registered user can have a customer profile
    customer: Mapped["Customer | None"] = relationship(back_populates="user", uselist=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), index=True)
    recipient_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Restaurant(Base):
    __tablename__ = "restaurants"
    id: Mapped[int] = mapped_column(primary_key=True)
    cafe: Mapped[str] = mapped_column(String(150), index=True)
    location: Mapped[str] = mapped_column(String(100), index=True)
    rating_out_of_5: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    feedback: Mapped[str | None] = mapped_column(String(500))
    menu_item: Mapped[str] = mapped_column(String(200))
    price_inr: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    approx_cost_for_two_inr: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2)
    )


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    # Link to a registered User account (optional — walk-in customers have no account)
    user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150))
    phone: Mapped[str | None] = mapped_column(String(40), unique=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    destination: Mapped[str | None] = mapped_column(JSON)
    orders: Mapped[list["Order"]] = relationship(back_populates="customer")
    user: Mapped["User | None"] = relationship(back_populates="customer")


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    customer_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("customers.id"), index=True)
    status: Mapped[OrderStatus] = mapped_column(SqlEnum(OrderStatus), default=OrderStatus.pending)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    delivery_destination: Mapped[str | None] = mapped_column(String(255))
    estimated_delivery_minutes: Mapped[int | None] = mapped_column(Integer)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    customer: Mapped[Customer | None] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    item_name: Mapped[str] = mapped_column(String(150), default="Item")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    order: Mapped[Order] = relationship(back_populates="items")

# no needed
class Reservation(Base):
    __tablename__ = "reservations"
    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    customer_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("customers.id"), index=True)
    reserved_for: Mapped[datetime] = mapped_column(DateTime, index=True)
    party_size: Mapped[int] = mapped_column(Integer)
    status: Mapped[ReservationStatus] = mapped_column(SqlEnum(ReservationStatus), default=ReservationStatus.booked)
    notes: Mapped[str | None] = mapped_column(Text)
