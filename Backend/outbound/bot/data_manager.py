
import pandas as pd
import logging
from typing import List, Dict, Union

logger = logging.getLogger(__name__)

class ClaimDataManager:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.claim_data = self._load_claim_data()

    def _load_claim_data(self) -> list:
        try:
            if self.file_path.endswith('.csv'):
                df = pd.read_csv(self.file_path)
            elif self.file_path.endswith(('.xlsx', '.xls')):
                df = pd.read_excel(self.file_path)
            else:
                raise ValueError("Unsupported file format. Please use CSV or Excel file.")
            claim_data_list = df.to_dict(orient="records")
            return [{k: str(v) for k, v in record.items() if pd.notna(v)} for record in claim_data_list]
        except Exception as e:
            logger.error(f"Error loading claim data: {e}")
            return []

    def get_claim_data(self) -> Union[List[Dict[str, str]], Dict[str, str]]:
        if len(self.claim_data) == 1:
            return self.claim_data[0]
        return self.claim_data