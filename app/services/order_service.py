# from decimal import Decimal
# from sqlalchemy.orm import Session

# from app.core.exceptions import not_found
# from app.db.models.restaurant import Order, OrderItem, Restaurant
# from app.schemas.order import OrderCreate


# def create_order(db: Session, payload: OrderCreate) -> Order:
#     if not db.get(Restaurant, payload.restaurant_id): raise not_found("Restaurant", payload.restaurant_id)
#     order = Order(restaurant_id=payload.restaurant_id, customer_id=payload.customer_id, notes=payload.notes)
#     total = Decimal("0")
#     for line in payload.items:
#         order.items.append(OrderItem(item_name=line.item_name, quantity=line.quantity, unit_price=line.unit_price))
#         total += line.unit_price * line.quantity
#     order.total_amount = total
#     db.add(order); db.commit(); db.refresh(order)
#     return order
