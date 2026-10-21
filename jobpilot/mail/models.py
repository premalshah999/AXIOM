"""Request models."""

from pydantic import BaseModel
from pydantic import Field


class RuleInput(BaseModel):
    mailbox_id: str
    employer_origin: str = Field(max_length=2048)
    sender_domains: list[str] = Field(min_length=1, max_length=20)
    link_origins: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = True
