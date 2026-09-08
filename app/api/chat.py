import os
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from app.db.models.restaurant import User
from app.main import get_current_user, get_db
from app.services.chat_service import ChatService, TopicGuardrailMiddleware, topic_guardrail
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model

router = APIRouter(prefix="/chat", tags=["chat"])

class ChatRequest(BaseModel):
    message: str
    session_id: str = Field(default="default_session")

class ChatResponse(BaseModel):
    response: str
    past_addresses: list[str] = []
    ask_destination: bool = False

@router.post("", response_model=ChatResponse)
async def chat_query(
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    groq_key = os.getenv("GROQ_API_KEY", "")
    groq_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
    if groq_model == "llama-3.3-70b-versatile":
        groq_model = "openai/gpt-oss-20b"

    llm = init_chat_model(
        groq_model,
        model_provider="groq",
        api_key=groq_key,
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
    )
