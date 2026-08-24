
import json
import os
import logging

logger = logging.getLogger(__name__)

class OutputFieldManager:
    def __init__(self, output_file: str):
        self.output_file = output_file
        self.output_fields = self._load_output_fields()

    def _load_output_fields(self) -> dict:
        if os.path.exists(self.output_file):
            with open(self.output_file, 'r', encoding="utf-8") as f:
                return json.load(f)
        return {}

    def update_output_field(self, field: str, value: str):
        self.output_fields[field] = value
        with open(self.output_file, 'w', encoding="utf-8") as f:
            json.dump(self.output_fields, f, indent=2)