"""Conversation history — every completed Investigate or Codemap result is
saved automatically (no explicit "save" action needed) so it can be revisited
later without recomputing it. Scoped per-repo, newest first.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from archaeologist.models.entities import Conversation, Repo


def save_conversation(session: Session, repo_id: int, kind: str, question: str, result: dict) -> None:
    session.add(Conversation(repo_id=repo_id, kind=kind, question=question.strip(), result=result))


def list_conversations(session: Session, repo_id: int, kind: str, limit: int = 40) -> list[dict]:
    rows = session.scalars(
        select(Conversation)
        .where(Conversation.repo_id == repo_id, Conversation.kind == kind)
        .order_by(Conversation.id.desc())
        .limit(limit)
    ).all()
    return [{"id": c.id, "question": c.question, "created_at": c.created_at.isoformat()} for c in rows]


def get_conversation(session: Session, conv_id: int, user_id: int) -> Conversation | None:
    """Ownership check joined through Conversation.repo_id -> Repo.user_id —
    a conv_id alone (a plain PK) previously let anyone fetch anyone else's
    saved answer just by guessing an id."""
    return session.scalar(
        select(Conversation).join(Repo, Repo.id == Conversation.repo_id)
        .where(Conversation.id == conv_id, Repo.user_id == user_id)
    )


def delete_conversation(session: Session, conv_id: int, user_id: int) -> bool:
    conv = get_conversation(session, conv_id, user_id)
    if conv is None:
        return False
    session.execute(delete(Conversation).where(Conversation.id == conv.id))
    return True
