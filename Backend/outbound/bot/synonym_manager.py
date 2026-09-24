
import os
import json
import logging
from typing import Dict, List, Optional
from threading import Lock

logger = logging.getLogger(__name__)

class SynonymManager:
    _instance = None
    _lock = Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(SynonymManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, storage_path: str = None):
        # Allow re-init if needed, but typically singletone
        if not hasattr(self, "_initialized"):
            # Default path: bot/data_files/synonyms.json
            if storage_path:
                self.storage_path = storage_path
            else:
                base_dir = os.path.dirname(os.path.abspath(__file__))
                self.storage_path = os.path.join(base_dir, "data_files", "synonyms.json")
            
            self._ensure_dir()
            self.synonyms: Dict[str, List[str]] = self._load_synonyms()
            self._initialized = True

    def _ensure_dir(self):
        directory = os.path.dirname(self.storage_path)
        if not os.path.exists(directory):
            try:
                os.makedirs(directory, exist_ok=True)
            except Exception as e:
                logger.error(f"Failed to create directory for synonyms: {e}")

    def _load_synonyms(self) -> Dict[str, List[str]]:
        if not os.path.exists(self.storage_path):
            return {}
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load synonyms from {self.storage_path}: {e}")
            return {}

    def save_synonyms(self, new_synonyms: Dict[str, List[str]], mode: str = "replace"):
        """
        Save synonyms to JSON.
        mode="replace": Overwrite aliases for the given keys.
        mode="merge": Append new aliases to existing ones.
        """
        with self._lock:
            current = self._load_synonyms()
            
            # Normalize keys to match DB fields style if needed, 
            # but user might send "Patient_FirstName" or "patient firstname".
            # We will store exactly as sent OR normalize?
            # Let's store normalized keys (lower/stripped) for lookup, 
            # BUT we need to map back to DB columns if the input keys ARE DB columns.
            # Actually, usually the USER sends "ActualDBColName" -> ["Alias1", "Alias2"].
            
            for k, aliases in new_synonyms.items():
                if not aliases: 
                    continue
                # cleaning aliases
                clean_aliases = [str(a).strip() for a in aliases if str(a).strip()]

                if mode == "merge":
                    existing = current.get(k, [])
                    # Append unique
                    for a in clean_aliases:
                        if a not in existing:
                            existing.append(a)
                    current[k] = existing
                else:
                    # Replace
                    current[k] = clean_aliases
            
            try:
                with open(self.storage_path, "w", encoding="utf-8") as f:
                    json.dump(current, f, indent=2, ensure_ascii=False)
                # Update in-memory
                self.synonyms = current
            except Exception as e:
                logger.error(f"Failed to save synonyms: {e}")
                raise e

    def get_synonyms(self, field_name: str) -> List[str]:
        # Try exact match first
        if field_name in self.synonyms:
            return self.synonyms[field_name]
        
        # Try case-insensitive
        field_lower = field_name.lower().strip()
        for k, v in self.synonyms.items():
            if k.lower().strip() == field_lower:
                return v
        return []

    def get_all_synonyms(self) -> Dict[str, List[str]]:
        return self.synonyms