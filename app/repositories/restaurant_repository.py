from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.restaurant import Restaurant


class RestaurantRepository:
    def __init__(self, db: Session): self.db = db
    def list(self, limit: int = 10) -> list[Restaurant]:
        return list(self.db.scalars(
            select(Restaurant)
            .order_by(Restaurant.rating_out_of_5.desc().nulls_last())
            .limit(limit)
        ))
    def get(self, restaurant_id: int) -> Restaurant | None: return self.db.get(Restaurant, restaurant_id)
    def create(self, restaurant: Restaurant) -> Restaurant:
        self.db.add(restaurant); self.db.commit(); self.db.refresh(restaurant); return restaurant
