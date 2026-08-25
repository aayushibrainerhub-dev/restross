from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class CategoryRead(CategoryCreate):
    id: int
    restaurant_id: int
    model_config = ConfigDict(from_attributes=True)


class MenuItemCreate(BaseModel):
    category_id: int
    name: str
    description: str | None = None
    price: Decimal = Field(gt=0, max_digits=10, decimal_places=2)


class MenuItemRead(MenuItemCreate):
    id: int
    is_available: bool
    model_config = ConfigDict(from_attributes=True)
