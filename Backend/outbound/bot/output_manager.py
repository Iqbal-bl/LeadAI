import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

class OutputManager:
    def __init__(self):
        self.conversation_history = []
        self.extracted_data = []

    def save_conversation(self, role: str, content: str) -> None:
        """Save a conversation turn to history."""
        self.conversation_history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    def save_extracted_data(self, data: Dict[str, Any]) -> None:
        """Save extracted data from the conversation."""
        if data:
            self.extracted_data.append({
                **data,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

    def get_conversation_history(self) -> List[Dict[str, str]]:
        """Get the conversation history."""
        return self.conversation_history

    def get_extracted_data(self) -> List[Dict[str, Any]]:
        """Get the extracted data."""
        return self.extracted_data

    def clear_history(self) -> None:
        """Clear conversation history and extracted data."""
        self.conversation_history = []
        self.extracted_data = []

    def save_to_file(self,filename:str) -> None:
        try:
            data = {
                "conversation_history" : self.conversation_history,
                "extracted_data": self.extracted_data,
                "timestamp": datetime.now(timezone.utc).isoformat()
             }
            with open(filename, 'w', encoding= 'utf-8') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Data saved to {filename}")
        except Exception as e:
            logger.error(f"Error saving data to file: {e}")