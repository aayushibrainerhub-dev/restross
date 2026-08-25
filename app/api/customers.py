from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import not_found
from app.db.database import get_db
from app.db.models.restaurant import Customer
from app.schemas.customer import CustomerCreate, CustomerRead

router = APIRouter(prefix="/customers", tags=["customers"])

@router.post("", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
def create(payload: CustomerCreate, db: Session = Depends(get_db)):
    customer = Customer(**payload.model_dump())
    db.add(customer); db.commit(); db.refresh(customer); return customer

@router.get("", response_model=list[CustomerRead])
def list_customers(db: Session = Depends(get_db)):
    return list(db.scalars(select(Customer).order_by(Customer.full_name)))

@router.get("/{customer_id}", response_model=CustomerRead)
def get_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = db.get(Customer, customer_id)
    if not customer: raise not_found("Customer", customer_id)
    return customer
