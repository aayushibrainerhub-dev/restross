from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import not_found
from app.db.database import get_db
from app.db.models.restaurant import MenuCategory, MenuItem, Restaurant
from app.schemas.menu import CategoryCreate, CategoryRead, MenuItemCreate, MenuItemRead

router = APIRouter(prefix="/menu", tags=["menu"])

@router.post("/restaurants/{restaurant_id}/categories", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
def create_category(restaurant_id: int, payload: CategoryCreate, db: Session = Depends(get_db)):
    if not db.get(Restaurant, restaurant_id): raise not_found("Restaurant", restaurant_id)
    category = MenuCategory(restaurant_id=restaurant_id, **payload.model_dump())
    db.add(category); db.commit(); db.refresh(category); return category

@router.post("/items", response_model=MenuItemRead, status_code=status.HTTP_201_CREATED)
def create_item(payload: MenuItemCreate, db: Session = Depends(get_db)):
    if not db.get(MenuCategory, payload.category_id): raise not_found("Menu category", payload.category_id)
    item = MenuItem(**payload.model_dump())
    db.add(item); db.commit(); db.refresh(item); return item

@router.get("/restaurants/{restaurant_id}/items", response_model=list[MenuItemRead])
def list_items(restaurant_id: int, db: Session = Depends(get_db)):
    statement = select(MenuItem).join(MenuCategory).where(MenuCategory.restaurant_id == restaurant_id)
    return list(db.scalars(statement))
