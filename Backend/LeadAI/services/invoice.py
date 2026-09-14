"""LeadAI Custom Server-Side Invoice & Email Service.

Renders high-fidelity HTML invoices (Texas Space Tours styling), converts to PDF in memory,
dispatches multi-part emails (HTML body + PDF attachment), and manages PDF downloads.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from xhtml2pdf import pisa

from LeadAI.config import settings

logger = logging.getLogger("LeadAI.invoice")

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def _number_to_words(amount: float, currency: str = "INR") -> str:
    """Convert numerical amount into formal English words for financial invoices."""
    units = [
        "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
        "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
        "Seventeen", "Eighteen", "Nineteen"
    ]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def _convert_hundreds(n: int) -> str:
        res = []
        if n >= 100:
            res.append(f"{units[n // 100]} Hundred")
            n %= 100
        if n >= 20:
            res.append(tens[n // 10])
            n %= 10
        if n > 0:
            res.append(units[n])
        return " ".join(res)

    int_part = int(amount)
    cents = int(round((amount - int_part) * 100))

    if int_part == 0:
        words = "Zero"
    else:
        chunks = []
        billions = int_part // 1_000_000_000
        millions = (int_part % 1_000_000_000) // 1_000_000
        thousands = (int_part % 1_000_000) // 1_000
        remainder = int_part % 1_000

        if billions:
            chunks.append(f"{_convert_hundreds(billions)} Billion")
        if millions:
            chunks.append(f"{_convert_hundreds(millions)} Million")
        if thousands:
            chunks.append(f"{_convert_hundreds(thousands)} Thousand")
        if remainder:
            chunks.append(_convert_hundreds(remainder))
        words = " ".join(chunks)

    curr_name = "Rupees" if currency.upper() == "INR" else "Dollars"
    return f"{words} {curr_name} and {cents:02d}/100 {currency.upper()}".strip()


def build_invoice_number(recharge_id: Any, created_at: datetime | None = None) -> str:
    year = (created_at or datetime.now(timezone.utc)).strftime("%Y")
    clean_id = str(recharge_id).replace("-", "")[-6:].upper()
    return f"INV-LA-{year}-{clean_id}"


def render_invoice_html(
    recharge: Any,
    client: Any,
    user_email: str | None = None,
    user_name: str | None = None,
) -> str:
    """Render the Texas Space Tours styled LeadAI invoice HTML with real payment data."""
    template = jinja_env.get_template("invoice_template.html")

    recharge_id = getattr(recharge, "Id", 1)
    created_at = getattr(recharge, "CreatedAt", None) or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    invoice_no = getattr(recharge, "InvoiceId", None) or build_invoice_number(recharge_id, created_at)
    issued_str = created_at.strftime("%d %b %Y, %H:%M UTC")
    due_str = created_at.strftime("%d %b %Y")

    amount = float(
        getattr(recharge, "PricePaid", None)
        if getattr(recharge, "PricePaid", None) is not None
        else (getattr(recharge, "Amount", 0.0) or 0.0)
    )
    currency = getattr(recharge, "Currency", "INR") or "INR"
    curr_symbol = "Rs. " if currency.upper() == "INR" else "$"

    client_name = getattr(client, "CompanyName", None) or getattr(client, "Name", "Valued Client")
    contact_name = user_name or getattr(client, "ContactPerson", "") or ""
    contact_phone = getattr(client, "ContactPhone", "") or ""
    contact_email = user_email or getattr(client, "ContactEmail", "") or ""
    customer_address = getattr(client, "Address", "") or ""

    formatted_rate = f"{curr_symbol}{amount:,.2f}"
    formatted_total = f"{curr_symbol}{amount:,.2f}"
    formatted_paid = f"{curr_symbol}{amount:,.2f}"
    formatted_due = f"{curr_symbol}0.00"

    plan_name = getattr(recharge, "PlanNameSnapshot", "Account Balance Recharge")
    mins = getattr(recharge, "PurchasedMinutes", 0)
    mins_str = f"{int(mins) if mins == int(mins) else mins} Calling Minutes" if mins else "Platform Credits"
    validity = getattr(recharge, "ValidityDaysSnapshot", None)
    validity_str = f" • Validity: {validity} Days" if validity else ""

    booking_code = str(recharge_id).replace("-", "")[-6:].upper()

    context = {
        "leadai_company_name": "LeadAI Technologies",
        "leadai_legal_name": "LeadAI Platform Services LLC",
        "leadai_address": "Chandigarh, India",
        "leadai_phone": "+91 172 4786 869",
        "leadai_email": "billing@leadai.com",
        "leadai_website": "www.leadai.com",
        "invoice_number": invoice_no,
        "issued_at": issued_str,
        "due_date": due_str,
        "booking_ref": f"B-{booking_code}",
        "client_company_name": client_name,
        "contact_name": contact_name,
        "contact_phone": contact_phone,
        "contact_email": contact_email,
        "customer_address": customer_address,
        "payment_status": "Paid",
        "currency": currency.upper(),
        "payment_method": "Razorpay (Card / UPI / NetBanking)",
        "transaction_id": getattr(recharge, "PaymentReference", "") or "",
        "auth_code": getattr(recharge, "RazorpayOrderId", "") or "",
        "subject_text": f"LeadAI Billing - Account Balance & Usage Recharge ({client_name})",
        "item_title": f"LeadAI Platform Recharge — {plan_name}",
        "item_description": f"{client_name} • {mins_str}{validity_str}",
        "quantity": 1,
        "rate_formatted": formatted_rate,
        "amount_formatted": formatted_rate,
        "subtotal_formatted": formatted_rate,
        "tax_formatted": "",
        "total_formatted": formatted_total,
        "amount_paid_formatted": formatted_paid,
        "balance_due_formatted": formatted_due,
        "amount_in_words": _number_to_words(amount, currency),
    }

    return template.render(**context)


def generate_invoice_pdf(html_content: str) -> bytes:
    """Generate standalone PDF bytes from HTML using xhtml2pdf."""
    out_pdf = io.BytesIO()
    pisa_status = pisa.CreatePDF(
        src=html_content,
        dest=out_pdf,
        encoding="utf-8",
    )
    if pisa_status.err:
        logger.error(f"Error rendering PDF invoice via xhtml2pdf: {pisa_status.err}")
        raise RuntimeError(f"PDF generation failed: {pisa_status.err}")
    return out_pdf.getvalue()


def send_invoice_email(
    to_email: str,
    subject: str,
    html_content: str,
    pdf_bytes: bytes,
    filename: str = "LeadAI_Invoice.pdf",
) -> bool:
    """Dispatch invoice email with full HTML body AND downloadable PDF attachment.

    Uses configured SMTP credentials. Non-blocking / safe-fail design so that payment
    verification never fails if SMTP encounters an issue.
    """
    if not settings.smtp_host:
        logger.warning("SMTP_HOST not configured. Invoice email skipped.")
        return False

    if not to_email:
        logger.warning("Recipient email is empty. Invoice email skipped.")
        return False

    import smtplib

    msg = MIMEMultipart("mixed")
    from_header = settings.smtp_from or settings.smtp_user or "billing@leadai.com"
    msg["From"] = from_header
    msg["To"] = to_email
    msg["Subject"] = subject

    # Alternative container for plain text fallback and HTML body
    alt_part = MIMEMultipart("alternative")
    plain_fallback = (
        f"Thank you for your payment to LeadAI.\n\n"
        f"Your custom invoice is attached to this email as a PDF.\n"
        f"For any queries, please reach out to billing@leadai.com."
    )
    alt_part.attach(MIMEText(plain_fallback, "plain", "utf-8"))
    alt_part.attach(MIMEText(html_content, "html", "utf-8"))
    msg.attach(alt_part)

    # Attach PDF
    pdf_attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
    pdf_attachment.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(pdf_attachment)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=25) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password or "")
            server.send_message(msg)
        logger.info(f"Invoice email successfully delivered to {to_email} with attachment {filename}")
        return True
    except Exception as exc:
        logger.error(f"Failed to deliver invoice email to {to_email}: {exc}", exc_info=True)
        return False
