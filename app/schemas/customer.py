# from datetime import datetime
# from uuid import UUID
# from pydantic import BaseModel, ConfigDict, EmailStr


# class CustomerCreate(BaseModel):
#     full_name: str
#     phone: str | None = None
#     email: EmailStr | None = None


# class CustomerRead(CustomerCreate):
#     id: UUID
#     created_at: datetime
#     model_config = ConfigDict(from_attributes=True)
