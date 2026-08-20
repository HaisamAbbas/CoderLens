"""Conversation history — every completed Investigate or Codemap result is
saved automatically (no explicit "save" action needed) so it can be revisited
later without recomputing it. Scoped per-repo, newest first.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from archaeologist.models.entities import Conversation


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


def get_conversation(session: Session, conv_id: int) -> Conversation | None:
    return session.get(Conversation, conv_id)


def delete_conversation(session: Session, conv_id: int) -> bool:
    result = session.execute(delete(Conversation).where(Conversation.id == conv_id))
    return result.rowcount > 0
