from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.restaurant import ChatMessage, User


async def conversation(
    db: AsyncSession, current_user_id: UUID, other_user_id: UUID, limit: int = 100
) -> list[ChatMessage]:
    result = await db.execute(
        select(ChatMessage)
        .where(
            or_(
                (ChatMessage.sender_id == current_user_id)
                & (ChatMessage.recipient_id == other_user_id),
                (ChatMessage.sender_id == other_user_id)
                & (ChatMessage.recipient_id == current_user_id),
            )
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def save_message(
    db: AsyncSession, sender_id: UUID, recipient_id: UUID, content: str
) -> ChatMessage:
    message = ChatMessage(
        sender_id=sender_id,
        recipient_id=recipient_id,
        content=content,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message


async def get_chat_users(db: AsyncSession, current_user_id: UUID) -> list[User]:
    result = await db.execute(
        select(User)
        .where(User.id != current_user_id, User.is_active.is_(True))
        .order_by(User.full_name)
    )
    return list(result.scalars().all())