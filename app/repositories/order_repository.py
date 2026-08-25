from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models.restaurant import Order


class OrderRepository:
    def __init__(self, db: Session): self.db = db
    def get(self, order_id: int) -> Order | None:
        return self.db.scalar(select(Order).options(selectinload(Order.items)).where(Order.id == order_id))
    def list(self, restaurant_id: int | None = None) -> list[Order]:
        query = select(Order).options(selectinload(Order.items)).order_by(Order.created_at.desc())
        if restaurant_id: query = query.where(Order.restaurant_id == restaurant_id)
        return list(self.db.scalars(query))
