import os
from datetime import timezone
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from app.db.models.restaurant import User
from app.main import get_current_user, get_db
from app.core.security import get_user_from_token
from app.db.database import SessionLocal
from app.services.direct_chat_service import conversation, get_chat_users, save_message
from app.services.chat_service import ChatService, TopicGuardrailMiddleware, topic_guardrail
from langchain.agents import create_agent
from langchain_groq import ChatGroq
from app.core.config import get_settings

router = APIRouter(prefix="/chat", tags=["chat"])
websocket_router = APIRouter(tags=["chat"])

class ChatRequest(BaseModel):
    message: str
    session_id: str = Field(default="default_session")

class ChatResponse(BaseModel):
    response: str
    past_addresses: list[str] = []
    ask_destination: bool = False
    tokens_used: int = 0
    total_cost: float = 0.0


class DirectMessageRequest(BaseModel):
    recipient_id: UUID
    content: str = Field(min_length=1, max_length=4000)


class ChatUserResponse(BaseModel):
    id: UUID
    full_name: str
    email: str


def serialize_message(message):
    return {
        "id": message.id,
        "sender_id": str(message.sender_id),
        "recipient_id": str(message.recipient_id),
        "content": message.content,
        "created_at": message.created_at.replace(tzinfo=timezone.utc).isoformat(),
    }


@router.get("/users", response_model=list[ChatUserResponse])
async def list_chat_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await get_chat_users(db, current_user.id)


@router.get("/conversations/{user_id}")
async def get_conversation(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    other_user = await db.get(User, user_id)
    if not other_user or not other_user.is_active or other_user.id == current_user.id:
        raise HTTPException(status_code=404, detail="Chat user not found")
    messages = await conversation(db, current_user.id, user_id)
    return {"messages": [serialize_message(message) for message in messages]}


@websocket_router.websocket("/ws/chat")
async def direct_chat_socket(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with SessionLocal() as db:
        current_user = await get_user_from_token(token, db)
        if not current_user:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await websocket.accept()
        try:
            while True:
                payload = await websocket.receive_json()
                try:
                    request = DirectMessageRequest.model_validate(payload)
                except Exception:
                    await websocket.send_json({"type": "error", "message": "Invalid message payload"})
                    continue
                if request.recipient_id == current_user.id:
                    await websocket.send_json({"type": "error", "message": "You cannot message yourself"})
                    continue
                recipient = await db.get(User, request.recipient_id)
                if not recipient or not recipient.is_active:
                    await websocket.send_json({"type": "error", "message": "Recipient is unavailable"})
                    continue
                message = await save_message(
                    db, current_user.id, recipient.id, request.content.strip()
                )
                await websocket.send_json({"type": "message", "message": serialize_message(message)})
        except WebSocketDisconnect:
            return

@router.post("", response_model=ChatResponse)
async def chat_query(
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    groq_key = os.getenv("GROQ_API_KEY", "")
    settings = get_settings()
    groq_model = os.getenv("GROQ_MODEL", settings.groq_model)
    if groq_model == "llama-3.3-70b-versatile":
        groq_model = "openai/gpt-oss-20b"

    llm = ChatGroq(
        model=groq_model,
        api_key=groq_key,
        metadata={"ls_model_name": settings.langfuse_model_name or groq_model},
    )

    # LangChain agent created with Groq LLM and guardrail middleware
    agent = create_agent(
        model=llm,
        tools=[],
        middleware=[
            topic_guardrail,
        ],
    )

    result = await ChatService(
        db=db,
        current_user=current_user,
        agent=agent,
    ).process_query(payload.message, session_id=payload.session_id)
    # SQL is an internal implementation detail. Never expose generated or
    # executed queries in the public chat response.

    return ChatResponse(
        response=result["response"],
        past_addresses=result.get("past_addresses") or [],
        ask_destination=bool(result.get("ask_destination", False)),
        tokens_used=result.get("tokens_used", 0),
        total_cost=result.get("total_cost", 0.0),
    )
