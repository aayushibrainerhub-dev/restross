from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import not_found
from app.db.models.restaurant import Restaurant
from app.main import get_db
from app.repositories.restaurant_repository import RestaurantRepository
from app.schemas.restaurant import RestaurantCreate, RestaurantRead

router = APIRouter(prefix="/restaurants", tags=["restaurants"])

@router.post("", response_model=RestaurantRead, status_code=status.HTTP_201_CREATED)
async def create(payload: RestaurantCreate, db: AsyncSession = Depends(get_db)):
    return await RestaurantRepository(db).create(Restaurant(**payload.model_dump()))

@router.get("", response_model=list[RestaurantRead])
async def list_restaurants(db: AsyncSession = Depends(get_db)):
    return await RestaurantRepository(db).list()

@router.get("/{restaurant_id}", response_model=RestaurantRead)
async def get_restaurant(restaurant_id: int, db: AsyncSession = Depends(get_db)):
    restaurant = await RestaurantRepository(db).get(restaurant_id)
    if not restaurant: raise not_found("Restaurant", restaurant_id)
    return restaurant