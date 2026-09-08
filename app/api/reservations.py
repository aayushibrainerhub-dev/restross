# from fastapi import APIRouter, Depends, status
# from sqlalchemy import select
# from sqlalchemy.orm import Session
# 
# from app.core.exceptions import not_found
# from app.db.models.restaurant import Reservation, User
# from app.main import get_current_user, get_db
# from app.schemas.reservation import ReservationCreate, ReservationRead, ReservationStatusUpdate
# from app.services.reservation_service import create_reservation
# 
# router = APIRouter(prefix="/reservations", tags=["reservations"])
# 
# @router.post("", response_model=ReservationRead, status_code=status.HTTP_201_CREATED)
# def create(payload: ReservationCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
#     return create_reservation(db, payload)
# 
# @router.get("", response_model=list[ReservationRead])
# def list_reservations(restaurant_id: int | None = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
#     statement = select(Reservation).order_by(Reservation.reserved_for)
#     if restaurant_id: statement = statement.where(Reservation.restaurant_id == restaurant_id)
#     return list(db.scalars(statement))
# 
# @router.patch("/{reservation_id}/status", response_model=ReservationRead)
# def change_status(reservation_id: int, payload: ReservationStatusUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
#     reservation = db.get(Reservation, reservation_id)
#     if not reservation: raise not_found("Reservation", reservation_id)
#     reservation.status = payload.status; db.commit(); db.refresh(reservation); return reservation
