from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import not_found
from app.db.database import get_db
from app.db.models.restaurant import Restaurant
from app.repositories.restaurant_repository import RestaurantRepository
from app.schemas.restaurant import RestaurantCreate, RestaurantRead

router = APIRouter(prefix="/restaurants", tags=["restaurants"])

@router.post("", response_model=RestaurantRead, status_code=status.HTTP_201_CREATED)
def create(payload: RestaurantCreate, db: Session = Depends(get_db)):
    return RestaurantRepository(db).create(Restaurant(**payload.model_dump()))

@router.get("", response_model=list[RestaurantRead])
def list_restaurants(db: Session = Depends(get_db)):
    return RestaurantRepository(db).list()

@router.get("/{restaurant_id}", response_model=RestaurantRead)
def get_restaurant(restaurant_id: int, db: Session = Depends(get_db)):
    restaurant = RestaurantRepository(db).get(restaurant_id)
    if not restaurant: raise not_found("Restaurant", restaurant_id)
    return restaurant

