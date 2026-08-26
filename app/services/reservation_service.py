# from sqlalchemy.orm import Session

# from app.core.exceptions import not_found
# from app.db.models.restaurant import Customer, Reservation, Restaurant
# from app.schemas.reservation import ReservationCreate


# def create_reservation(db: Session, payload: ReservationCreate) -> Reservation:
#     if not db.get(Restaurant, payload.restaurant_id): raise not_found("Restaurant", payload.restaurant_id)
#     if not db.get(Customer, payload.customer_id): raise not_found("Customer", payload.customer_id)
#     reservation = Reservation(**payload.model_dump())
#     db.add(reservation); db.commit(); db.refresh(reservation)
#     return reservation
