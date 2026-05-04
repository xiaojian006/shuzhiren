from pydantic import BaseModel


class ChatRequest(BaseModel):
    question: str
    session_id: str = "default"
    role: str = "cycle"


class FeedbackRequest(BaseModel):
    session_id: str = "default"
    question: str
    answer: str
    useful: bool


class WatchlistRequest(BaseModel):
    session_id: str = "default"
    question: str = ""
    code: str | None = None
