# from fastapi import APIRouter, Depends, status
# from sqlalchemy.orm import Session

# from app.core.exceptions import not_found
# from app.db.database import get_db
# from app.db.models.restaurant import Order
# from app.repositories.order_repository import OrderRepository
# from app.schemas.order import OrderCreate, OrderRead, OrderStatusUpdate
# from app.services.order_service import create_order

# router = APIRouter(prefix="/orders", tags=["orders"])

# @router.post("", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
# def create(payload: OrderCreate, db: Session = Depends(get_db)):
#     return create_order(db, payload)

# @router.get("", response_model=list[OrderRead])
# def list_orders(restaurant_id: int | None = None, db: Session = Depends(get_db)):
#     return OrderRepository(db).list(restaurant_id)

# @router.get("/{order_id}", response_model=OrderRead)
# def get_order(order_id: int, db: Session = Depends(get_db)):
#     order = OrderRepository(db).get(order_id)
#     if not order: raise not_found("Order", order_id)
#     return order

# @router.patch("/{order_id}/status", response_model=OrderRead)
# def change_status(order_id: int, payload: OrderStatusUpdate, db: Session = Depends(get_db)):
#     order = db.get(Order, order_id)
#     if not order: raise not_found("Order", order_id)
#     order.status = payload.status; db.commit(); db.refresh(order); return OrderRepository(db).get(order_id)
