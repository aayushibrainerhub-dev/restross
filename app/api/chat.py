from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.services.chat_service import ChatService

router = APIRouter(prefix="/chat", tags=["chat"])

from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    message: str
    session_id: str = Field(default="default_session")

class ChatResponse(BaseModel):
    response: str

@router.post("", response_model=ChatResponse)
def chat_query(payload: ChatRequest, db: Session = Depends(get_db)):
    service = ChatService(db)
    result = service.process_query(payload.message, session_id=payload.session_id)
    # SQL is an internal implementation detail.  Never expose generated or
    # executed queries in the public chat response.
    return ChatResponse(response=result["response"])
