from dataclasses import dataclass
from datetime import datetime, timezone

@dataclass
class ConversationEntity:
    CallSid: str
    ResponseType: str
    ResponseText: str
    CreatedAt: datetime

    def to_dict(self) -> dict:
        return {
            "CallSid": self.CallSid,
            "ResponseType": self.ResponseType,
            "ResponseText": self.ResponseText,
            "CreatedAt": self.CreatedAt
        }