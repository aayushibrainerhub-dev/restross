from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.restaurant import Restaurant


class RestaurantRepository:
    def __init__(self, db: AsyncSession): self.db = db
    async def list(self, limit: int = 10) -> list[Restaurant]:
        result = await self.db.execute(
            select(Restaurant)
            .order_by(Restaurant.rating_out_of_5.desc().nulls_last())
            .limit(limit)
        )
        return list(result.scalars())
    async def get(self, restaurant_id: int) -> Restaurant | None:
        return await self.db.get(Restaurant, restaurant_id)
    async def create(self, restaurant: Restaurant) -> Restaurant:
        self.db.add(restaurant)
        await self.db.commit()
        await self.db.refresh(restaurant)
        return restaurant
