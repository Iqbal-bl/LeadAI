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


class BatchLevelCSVExportService:
    """
    Service to handle CSV creation and S3 upload for entire batch executions.
    Creates ONE ROW PER CALL with combined BatchInfo input + BatchInfoOutput extracted data.
    """

    # Placeholder for empty fields in CSV
    EMPTY_FIELD_PLACEHOLDER = ""

    # All fields to include in each row
    ALL_CSV_FIELDS = [
        # Batch Context
        "BatchId", "BatchName", "BatchExecutionId",
        
        # BatchInfo Input Fields
        "CsvName", "Group_ID", "Callers_Name", "Callers_Intent", "Company_Name",
        "NPI", "Tax_ID", "CLIA_Number", "Callback_Number", "Provider_Address",
        "Patient_FirstName", "Patient_LastName", "Subscriber_ID", "Claims_Address",
        "Subscriber_FirstName", "Subscriber_LastName", "Relationship", "DOB", "DOS",
        "Charge", "Insurance_Payment", "Submission_Method", "Appeal_Submission_Date",
        "Claim_Appeal", "Provider_Fax", "Provider_Email", "Unresponded_Claim",
        "Unresponded_Appeal", "Payer_ID", "Payer_Name", "Payer_Phone_Number",
        
        # Extracted Output Fields
        "Prefered_Payer_ID", "Prefered_Claims_Address", "Claims_TFL",
        "Processing_Delays", "Eligible", "CS_Agent_Name", "Call_Ref",
        "Department_Phone_Number", "Status", "Appeals_Address", "Received_date",
        "Processing_Period", "Other_information", "Processed_Date", "Denial_reason",
        "Claim_Number", "Appealable", "Appeals_TFL", "Appeals_Submission_Method",
        "Online_Portal", "Appeals_Fax_Number", "Corrected_claims", "Paid_date",
        "PaymentAmount", "Payment_Method", "Paid_To", "Check_Cashed",
        "Pay_to_address", "Check_Number", "Bank_Account", "Deposit_Date",
        "Appeal_Ticket", "PR_Amount", "PR_Type", "Bulk_Payment"
    ]

    def __init__(self):
        """Initialize S3 session"""
        self.s3_session = aioboto3.Session(
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "us-east-1")
        )
        self.s3_bucket = os.getenv("S3_csv_bucket", "insurance-claim-extracts")
        logger.info("BatchLevelCSVExportService initialized")

    async def create_and_upload_batch_csv(
        self,
        batch_id: str,
        batch_execution_id: str,
        email: str
    ) -> str:
        """
        1. Fetch batch execution metadata
        2. Fetch all CallNumberExecution for this batch execution
        3. Get BatchInfo via CallNumberExecution.CallNumberId -> CallNumber.BatchInfoId
        4. Fetch all BatchInfoOutput (for extracted data if available)
        5. Create CSV with ONE ROW PER CALL (combined input + output)
        6. Upload to S3
        7. Return S3 URL
        Creates its own database session to avoid session conflicts.
        """
        from database import get_dynamic_db

        db = get_dynamic_db(email)
        try:
            logger.info(
                f"Starting batch CSV export for batch_id={batch_id}, "
                f"execution_id={batch_execution_id}"
            )

            # 1) Get batch execution metadata
            batch_exec = db.query(models.BatchExecution).filter(
                models.BatchExecution.Id == batch_execution_id,
                models.BatchExecution.IsDeleted == False
            ).first()

            if not batch_exec:
                logger.error(f"BatchExecution not found: {batch_execution_id}")
                return None

            # Get batch name and client name
            batch_row = db.query(models.Batch).filter(
                models.Batch.Id == batch_id,
                models.Batch.IsDeleted == False
            ).first()
            batch_name = batch_row.Name if batch_row else batch_id
            
            # Get client name from batch -> client relationship
            client_name = None
            if batch_row and batch_row.client:
                client_name = batch_row.client.Name
            
            # Get batch date from CreatedAt
            batch_date = None
            if batch_row and batch_row.CreatedAt:
                batch_date = batch_row.CreatedAt.strftime("%Y-%m-%d")

            # 2) Optimization: Fetch everything in ONE query using JOINs
            # CallNumberExecution -> CallNumber -> BatchInfo
            # CallNumberExecution -> BatchInfoOutput (connected by CallNumberExecutionId)
            
            logger.info("Fetching all batch data with optimized JOIN query...")
            
            # Query returns tuples: (CallNumberExecution, CallNumber, BatchInfo, BatchInfoOutput)
            results = (
                db.query(
                    models.CallNumberExecution,
                    models.CallNumber,
                    models.BatchInfo,
                    models.BatchInfoOutput
                )
                .join(models.CallNumber, models.CallNumberExecution.CallNumberId == models.CallNumber.Id)
                .outerjoin(models.BatchInfo, models.CallNumber.BatchInfoId == models.BatchInfo.Id)
                .outerjoin(models.BatchInfoOutput, models.CallNumberExecution.Id == models.BatchInfoOutput.CallNumberExecutionId)
                .filter(
                    models.CallNumberExecution.BatchExecutionId == batch_execution_id,
                    models.CallNumberExecution.IsDeleted == False,
                    models.CallNumberExecution.Status == "completed"
                )
                .all()
            )

            if not results:
                logger.warning(f"No completed call executions found for batch execution {batch_execution_id}")
                return None

            logger.info(f"Fetched {len(results)} rows of joined data")

            # 5) Create CSV content (ONE ROW PER CALL)
            # Pass the results list directly
            csv_content = self._create_batch_csv_content(
                batch_exec, batch_name, results
            )

            logger.info(f"Batch CSV created: {len(csv_content)} bytes")

            # 6) Upload to S3 with custom filename (business-friendly format)
            batch_status = batch_exec.Status if batch_exec else None
            s3_url = await self._upload_to_s3(
                csv_content, 
                batch_name, 
                batch_execution_id,
                client_name=client_name,
                batch_status=batch_status,
                batch_date=batch_date
            )

            logger.info(f"Batch CSV uploaded to S3: {s3_url}")

            return s3_url

        except Exception as e:
            logger.error(f"Error creating/uploading batch CSV: {e}")
            import traceback
            traceback.print_exc()
            return None
        finally:
            db.close()

    def _create_batch_csv_content(
        self,
        batch_exec,
        batch_name: str,
        results: list
    ) -> str:
        """Create CSV using the joined query results"""
        csv_buffer = io.StringIO()
        csv_writer = csv.writer(csv_buffer)

        # --- HEADER ROW ---
        csv_writer.writerow(self.ALL_CSV_FIELDS)

        # --- DATA ROWS (ONE PER CALL) ---
        for row_tuple in results:
            # Unpack attributes from the tuple
            # The query selected models in this order: CNE, CN, BI, BIO
            cne = row_tuple[0]
            # cn  = row_tuple[1] # CallNumber (unused directly in row build, but needed for join)
            bi  = row_tuple[2] # BatchInfo (may be None if outerjoin)
            bio = row_tuple[3] # BatchInfoOutput (may be None if outerjoin)

            row = self._build_call_row(
                batch_exec, batch_name, cne, bi, bio
            )
            csv_writer.writerow(row)

        return csv_buffer.getvalue()

    def _build_call_row(
        self,
        batch_exec,
        batch_name: str,
        call_exec,
        batch_info,
        extracted_output
    ) -> list:
        """Build a single row from the joined objects"""
        row_data = {}

        # --- Batch Context ---
        row_data["BatchId"] = batch_exec.BatchId or ""
        row_data["BatchName"] = batch_name or ""
        row_data["BatchExecutionId"] = batch_exec.Id or ""

        # --- BatchInfo Input Fields ---
        batchinfo_fields = [
            "CsvName", "Group_ID", "Callers_Name", "Callers_Intent", "Company_Name",
            "NPI", "Tax_ID", "CLIA_Number", "Callback_Number", "Provider_Address",
            "Patient_FirstName", "Patient_LastName", "Subscriber_ID", "Claims_Address",
            "Subscriber_FirstName", "Subscriber_LastName", "Relationship", "DOB", "DOS",
            "Charge", "Insurance_Payment", "Submission_Method", "Appeal_Submission_Date",
            "Claim_Appeal", "Provider_Fax", "Provider_Email", "Unresponded_Claim",
            "Unresponded_Appeal", "Payer_ID", "Payer_Name", "Payer_Phone_Number"
        ]

        for field in batchinfo_fields:
            if batch_info:
                value = getattr(batch_info, field, None)
                row_data[field] = self._safe_str(value)
            else:
                row_data[field] = self.EMPTY_FIELD_PLACEHOLDER

        # --- Extracted Output Fields ---
        extracted_fields = [
            "Prefered_Payer_ID", "Prefered_Claims_Address", "Claims_TFL",
            "Processing_Delays", "Eligible", "CS_Agent_Name", "Call_Ref",
            "Department_Phone_Number", "Status", "Appeals_Address", "Received_date",
            "Processing_Period", "Other_information", "Processed_Date", "Denial_reason",
            "Claim_Number", "Appealable", "Appeals_TFL", "Appeals_Submission_Method",
            "Online_Portal", "Appeals_Fax_Number", "Corrected_claims", "Paid_date",
            "PaymentAmount", "Payment_Method", "Paid_To", "Check_Cashed",
            "Pay_to_address", "Check_Number", "Bank_Account", "Deposit_Date",
            "Appeal_Ticket", "PR_Amount", "PR_Type", "Bulk_Payment"
        ]

        for field in extracted_fields:
            if extracted_output:
                value = getattr(extracted_output, field, None)
                row_data[field] = self._safe_str(value)
            else:
                row_data[field] = self.EMPTY_FIELD_PLACEHOLDER

        # Convert to list following ALL_CSV_FIELDS order
        return [row_data.get(field, self.EMPTY_FIELD_PLACEHOLDER) for field in self.ALL_CSV_FIELDS]

    def _safe_str(self, value) -> str:
        """Safely convert value to string"""
        try:
            if value is None:
                return self.EMPTY_FIELD_PLACEHOLDER
            elif isinstance(value, (list, dict)):
                return str(value)
            elif isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                return value.isoformat()
            elif not isinstance(value, str):
                return str(value)
            return value
        except Exception as e:
            logger.warning(f"Error converting value: {e}")
            return self.EMPTY_FIELD_PLACEHOLDER

    async def _upload_to_s3(
        self,
        csv_content: str,
        batch_name: str,
        batch_execution_id: str,
        client_name: str = None,
        batch_status: str = None,
        batch_date: str = None
    ) -> str:
        """Upload batch CSV to S3 with business-friendly filename"""
        try:
            now = datetime.now(timezone.utc)
            year = now.strftime("%Y")
            month = now.strftime("%m")
            day = now.strftime("%d")

            # Build business-friendly filename: BatchName_ClientName_Date_Status_ExecId.csv
            # Clean batch_name of any special characters
            safe_batch_name = batch_name.replace(" ", "_") if batch_name else "Batch"
            
            # Build filename parts
            filename_parts = [safe_batch_name]
            
            if client_name:
                safe_client_name = client_name.replace(" ", "_")
                filename_parts.append(safe_client_name)
            
            # Use batch_date if available, otherwise use current date
            date_part = batch_date or now.strftime("%Y-%m-%d")
            filename_parts.append(date_part)
            
            if batch_status:
                # Capitalize status for readability
                filename_parts.append(batch_status.capitalize())
            
            # Include short execution ID to guarantee unique filenames per execution
            # This prevents overwrites when the same batch is run multiple times on the same day
            exec_id_short = batch_execution_id[:8] if batch_execution_id else now.strftime("%H%M%S")
            filename_parts.append(exec_id_short)
            
            filename = "_".join(filename_parts) + ".csv"
            s3_key = f"batch-exports/{year}/{month}/{day}/{filename}"

            async with self.s3_session.client("s3") as s3:
                await s3.put_object(
                    Bucket=self.s3_bucket,
                    Key=s3_key,
                    Body=csv_content.encode('utf-8'),
                    ContentType='text/csv'
                )

            s3_url = f"https://{self.s3_bucket}.s3.amazonaws.com/{s3_key}"
            logger.info(f"Batch CSV filename: {filename}")
            return s3_url

        except Exception as e:
            logger.error(f"Error uploading batch CSV to S3: {e}")
            raise


# --- INTEGRATION: Call this when batch completes ---
async def finalize_batch_with_csv(
    batch_id: str,
    batch_execution_id: str,
    email: str
) -> bool:
    """
    Called when batch completes.
    Creates its own database session to avoid session conflicts.
    Waits for all extractions to be saved for ACTUAL calls (those with terminal status), then creates CSV
    Returns True if successful, False otherwise
    """
    from database import get_dynamic_db

    db = get_dynamic_db(email)
    try:
        # 3) Now create and upload batch CSV
        logger.info("Starting batch CSV generation...")
        csv_service = BatchLevelCSVExportService()
        s3_url = await csv_service.create_and_upload_batch_csv(
            batch_id, batch_execution_id, email
        )

        if not s3_url:
            logger.error(f"Failed to generate batch CSV for {batch_execution_id}")
            return False

        # 4) Update BatchExecution with CSV URL
        batch_exec = db.query(models.BatchExecution).filter(
            models.BatchExecution.Id == batch_execution_id
        ).first()

        if batch_exec:
            batch_exec.BatchCsvLink = s3_url
            batch_exec.UpdatedAt = datetime.now(timezone.utc)
            db.commit()
            logger.info(f"Batch {batch_execution_id} CSV URL saved: {s3_url}")
            return True
        else:
            logger.error(f"BatchExecution {batch_execution_id} not found for CSV URL update")
            return False

    except Exception as e:
        logger.error(f"Error finalizing batch with CSV: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()