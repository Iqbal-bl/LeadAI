import aioboto3
import csv
import io
from datetime import datetime, timezone
import os
import logging
from Domain import models
from dotenv import load_dotenv
load_dotenv()
logger = logging.getLogger(__name__)


class CSVExtractionService:
    """
    Service to handle CSV creation and S3 upload for extracted call data.
    """

    # Placeholder for empty fields in CSV
    EMPTY_FIELD_PLACEHOLDER = ""

    # Fields to fetch from BatchInfo table
    BATCHINFO_CSV_FIELDS = [
        "BatchId", "CsvName", "Group_ID", "Callers_Name", "Callers_Intent", "Company_Name", 
        "NPI", "Tax_ID", "CLIA_Number", "Callback_Number", "Provider_Address", 
        "Patient_FirstName", "Patient_LastName", "Subscriber_ID", "Claims_Address", 
        "Subscriber_FirstName", "Subscriber_LastName", "Relationship", "DOB", "DOS", 
        "Charge", "Insurance_Payment", "Submission_Method", "Appeal_Submission_Date", 
        "Claim_Appeal", "Provider_Fax", "Provider_Email", "Unresponded_Claim", 
        "Unresponded_Appeal","Payer_ID", "Payer_Name", "Payer_Phone_Number"
    ]

    # Fields to fetch from extracted/output data
    EXTRACTED_CSV_FIELDS = [
        "BatchExecutionId", "CallNumberExecutionId", "Prefered_Payer_ID", 
        "Prefered_Claims_Address", "Claims_TFL", "Processing_Delays", "Eligible", 
        "CS_Agent_Name", "Call_Ref", "Department_Phone_Number", "Status", "Appeals_Address", 
        "Received_date", "Processing_Period", "Other_information", "Processed_Date", 
        "Denial_reason", "Claim_Number", "Appealable", "Appeals_TFL", 
        "Appeals_Submission_Method", "Online_Portal", "Appeals_Fax_Number", 
        "Corrected_claims", "Paid_date", "PaymentAmount", "Payment_Method", "Paid_To", 
        "Check_Cashed", "Pay_to_address", "Check_Number", "Bank_Account", "Deposit_Date", 
        "Appeal_Ticket", "PR_Amount", "PR_Type", "Bulk_Payment"
    ]


    def __init__(self):
        """Initialize S3 session (async)"""
        self.s3_session = aioboto3.Session(
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "us-east-1")
        )
        self.s3_bucket = os.getenv("S3_csv_bucket", "insurance-claim-extracts")
        logger.info("CSVExtractionService initialized")

    async def create_and_upload_csv(
        self,
        db,
        output_record: dict,
        batch_info_id: str,
        call_number_execution_id: str,
        batch_info_data: dict = None  # OPTIONAL: Pass existing data to skip DB fetch
    ) -> str:
        """
        1. Get BatchInfo data (from DB or passed dict)
        2. Merge with extracted output_record
        3. Create CSV with specified fields (from both sources)
        4. Upload to S3 (ASYNC)
        5. Return S3 URL
        """
        try:
            # 1) Get BatchInfo data
            if batch_info_data:
                # Use provided data (already a dict)
                batch_info = batch_info_data
            else:
                # Fetch from DB (returns SQLAlchemy object)
                batch_info = self._get_batch_info(db, batch_info_id)
            
            # 2) Prepare data dictionary combining both sources
            csv_data = self._prepare_csv_data(batch_info, output_record)
            
            logger.info(f"CSV data prepared: {len(csv_data)} fields")
            
            # 3) Create CSV in memory
            csv_content = self._create_csv_content(csv_data)
            
            logger.info(f"CSV created: {len(csv_content)} bytes")
            
            # 4) Upload to S3 with date-based folder structure (ASYNC)
            s3_url = await self._upload_to_s3(csv_content, call_number_execution_id)
            
            logger.info(f"CSV uploaded to S3: {s3_url}")
            
            return s3_url
            
        except Exception as e:
            logger.error(f"Error creating/uploading CSV: {e}")
            return None

    def _get_batch_info(self, db, batch_info_id: str):
        """Fetch BatchInfo record from database"""
        if not batch_info_id:
            return None
        
        try:
            return db.query(models.BatchInfo).filter(
                models.BatchInfo.Id == batch_info_id
            ).first()
        except Exception as e:
            logger.error(f"Error fetching BatchInfo: {e}")
            return None

    def _prepare_csv_data(self, batch_info, output_record: dict) -> dict:
        """Prepare combined data from BatchInfo and extracted fields"""
        csv_data = {}

        # Add BatchInfo fields
        if batch_info:
            is_dict = isinstance(batch_info, dict)
            for field in self.BATCHINFO_CSV_FIELDS:
                if is_dict:
                    value = batch_info.get(field)
                else:
                    value = getattr(batch_info, field, None)
                csv_data[field] = value if value is not None else self.EMPTY_FIELD_PLACEHOLDER
        else:
            for field in self.BATCHINFO_CSV_FIELDS:
                csv_data[field] = self.EMPTY_FIELD_PLACEHOLDER


        # Add extracted fields (overwrites if duplicate)
        for field in self.EXTRACTED_CSV_FIELDS:
            value = output_record.get(field)
            csv_data[field] = value if value is not None else self.EMPTY_FIELD_PLACEHOLDER

        return csv_data

    def _create_csv_content(self, csv_data: dict) -> str:
        """Create CSV content in memory - column-formatted with headers and single data row"""
        csv_buffer = io.StringIO()
        csv_writer = csv.writer(csv_buffer)
        
        # All fields as column headers (preserve order)
        all_fields = list(dict.fromkeys(self.BATCHINFO_CSV_FIELDS + self.EXTRACTED_CSV_FIELDS))
        
        # Header row - each field is a column
        csv_writer.writerow(all_fields)
        
        # Data row - all values in a single row
        data_row = []
        for field_name in all_fields:
            field_value = csv_data.get(field_name, self.EMPTY_FIELD_PLACEHOLDER)

            # Convert all values to string safely
            try:
                if field_value is None:
                    field_value = self.EMPTY_FIELD_PLACEHOLDER
                elif isinstance(field_value, (list, dict)):
                    field_value = str(field_value)
                elif not isinstance(field_value, str):
                    field_value = str(field_value)
            except Exception as e:
                logger.warning(f"Error converting field {field_name}: {e}")
                field_value = self.EMPTY_FIELD_PLACEHOLDER

            data_row.append(field_value)
        
        csv_writer.writerow(data_row)
        
        return csv_buffer.getvalue()

    async def _upload_to_s3(self, csv_content: str, call_number_execution_id: str) -> str:
        """Upload CSV to S3 with date-based folder structure"""
        try:
            now = datetime.now(timezone.utc)
            year = now.strftime("%Y")
            month = now.strftime("%m")
            day = now.strftime("%d")
            timestamp = now.strftime("%H%M%S")
            
            s3_key = f"extracted-data/{year}/{month}/{day}/{call_number_execution_id}_{timestamp}.csv"
            
            # Use async S3 client
            async with self.s3_session.client("s3") as s3:
                await s3.put_object(
                    Bucket=self.s3_bucket,
                    Key=s3_key,
                    Body=csv_content.encode('utf-8'),
                    ContentType='text/csv'
                )
            
            # Generate S3 URL
            s3_url = f"https://{self.s3_bucket}.s3.amazonaws.com/{s3_key}"
            return s3_url
            
        except Exception as e:
            logger.error(f"Error uploading to S3: {e}")
            raise