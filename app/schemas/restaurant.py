from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class RestaurantCreate(BaseModel):
    cafe: str = Field(min_length=2, max_length=150)
    location: str = Field(min_length=1, max_length=100)
    rating_out_of_5: Decimal | None = Field(default=None, ge=0, le=5)
    feedback: str | None = Field(default=None, max_length=500)
    menu_item: str = Field(min_length=1, max_length=200)
    price_inr: Decimal | None = Field(default=None, ge=0)
    approx_cost_for_two_inr: Decimal | None = Field(default=None, ge=0)


class RestaurantRead(RestaurantCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)
