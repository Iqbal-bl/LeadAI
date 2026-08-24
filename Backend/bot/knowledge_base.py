import json
import logging

logger = logging.getLogger(__name__)

class KnowledgeBase:
    def __init__(self, knowledge_file: str):
        self.knowledge_file = knowledge_file
        self.knowledge = self._load_knowledge()

    def _load_knowledge(self) -> dict:
        try:
            with open(self.knowledge_file, 'r', encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"Knowledge base file not found: {self.knowledge_file}")
            return {}
        except json.JSONDecodeError:
            logger.error(f"Error decoding JSON from file: {self.knowledge_file}")
            return {}

    def get_relevant_knowledge(self, query: str) -> str:
        relevant_info = []
        query_words = set(query.lower().split())
        for key, value in self.knowledge.items():
            if isinstance(value, str):
                if any(word in value.lower() for word in query_words):
                    relevant_info.append(f"{key}: {value}")
            elif isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    if any(word in str(sub_value).lower() for word in query_words):
                        relevant_info.append(f"{key} - {sub_key}: {sub_value}")
        return "\n".join(relevant_info) if relevant_info else "No relevant information found."
