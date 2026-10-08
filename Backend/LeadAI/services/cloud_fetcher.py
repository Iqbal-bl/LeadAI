"""
LeadAI Cloud File Fetcher.

Downloads and extracts file data from Google Drive and Dropbox shared links
(as well as direct URLs) so that knowledge base documents contain actual
document content, not just the raw link URL.
"""
from __future__ import annotations

import logging
import mimetypes
import os
import re
import urllib.parse
from typing import Tuple

import requests
from fastapi import HTTPException, status

from ..config import settings

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def _parse_filename_from_headers(headers: dict, fallback: str) -> str:
    """Extract filename from Content-Disposition header if available."""
    cd = headers.get("content-disposition") or headers.get("Content-Disposition") or ""
    if cd:
        # RFC 5987 filename*=UTF-8''filename.ext
        m_rfc = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.IGNORECASE)
        if m_rfc:
            try:
                return urllib.parse.unquote(m_rfc.group(1).strip().strip('"\''))
            except Exception:
                pass

        # Standard filename="..."
        m_std = re.search(r'filename="([^"]+)"', cd, re.IGNORECASE)
        if m_std:
            return m_std.group(1).strip()

        # Fallback filename=unquoted
        m_bare = re.search(r"filename=([^;\s]+)", cd, re.IGNORECASE)
        if m_bare:
            return m_bare.group(1).strip().strip('"\'')

    return fallback


def _detect_content_type(filename: str, header_type: str | None) -> str:
    """Normalize and detect proper MIME content type."""
    ctype = (header_type or "").split(";")[0].strip().lower()
    if ctype and ctype not in (
        "application/octet-stream",
        "binary/octet-stream",
        "application/x-download",
        "application/download",
    ):
        return ctype

    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        return guessed

    fn_lower = filename.lower()
    if fn_lower.endswith(".pdf"):
        return "application/pdf"
    if fn_lower.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if fn_lower.endswith(".txt"):
        return "text/plain"
    if fn_lower.endswith(".csv"):
        return "text/csv"
    if fn_lower.endswith(".md"):
        return "text/markdown"
    if fn_lower.endswith((".html", ".htm")):
        return "text/html"

    return ctype or "text/plain"


def _extract_google_file_id(url: str) -> str | None:
    """Extract the file or document ID from various Google Drive/Docs URLs."""
    # Pattern 1: /d/([a-zA-Z0-9_-]+)
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)

    # Pattern 2: id=([a-zA-Z0-9_-]+)
    m = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)

    return None


def fetch_google_drive(url: str, default_title: str | None = None) -> Tuple[str, str, bytes]:
    """Download a file or export a document from Google Drive."""
    file_id = _extract_google_file_id(url)
    if not file_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Google Drive URL. Could not extract file ID.",
        )

    clean_title = re.sub(r'[\\/*?:"<>|]', "", (default_title or "").strip()) or f"drive_{file_id[:8]}"

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    # Check if this is a Google Docs / Sheets / Slides URL
    lower_url = url.lower()
    if "docs.google.com/document" in lower_url:
        export_url = f"https://docs.google.com/document/d/{file_id}/export?format=docx"
        fallback_filename = f"{clean_title}.docx"
    elif "docs.google.com/spreadsheets" in lower_url:
        export_url = f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=csv"
        fallback_filename = f"{clean_title}.csv"
    elif "docs.google.com/presentation" in lower_url:
        export_url = f"https://docs.google.com/presentation/d/{file_id}/export?format=pdf"
        fallback_filename = f"{clean_title}.pdf"
    else:
        # Regular Google Drive file
        export_url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
        fallback_filename = f"{clean_title}.pdf"

    try:
        resp = session.get(export_url, allow_redirects=True, timeout=30)
    except Exception as exc:
        logger.warning("[LeadAI cloud_fetcher] Google Drive fetch failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to connect to Google Drive: {exc}",
        ) from exc

    # If drive.usercontent redirected or returned 404, try drive.google.com/uc
    if resp.status_code in (404, 403, 500) and "docs.google.com" not in lower_url:
        alt_url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t"
        try:
            alt_resp = session.get(alt_url, allow_redirects=True, timeout=30)
            if alt_resp.status_code == 200:
                resp = alt_resp
        except Exception:
            pass

    content_type_header = resp.headers.get("content-type", "").lower()

    # Check for authentication / sign-in screen
    if "accounts.google.com" in resp.url.lower() or (
        "text/html" in content_type_header
        and any(x in resp.text.lower() for x in ["sign in - google accounts", "accounts.google.com", "serviceLogin"])
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "The Google Drive file is private and requires sign-in. "
                "Please update the file's General Access permission to 'Anyone with the link can view' and try again."
            ),
        )

    # Check for virus scan confirmation page (files > 100MB or with scan tokens)
    if "text/html" in content_type_header and "confirm=" in resp.text:
        # Search for confirmation link or form action
        m_confirm = re.search(r'href="([^"]*confirm=[^"]*)"', resp.text)
        if m_confirm:
            confirm_link = m_confirm.group(1)
            if confirm_link.startswith("/"):
                confirm_link = f"https://drive.google.com{confirm_link}"
            try:
                resp = session.get(confirm_link, allow_redirects=True, timeout=30)
                content_type_header = resp.headers.get("content-type", "").lower()
            except Exception:
                pass

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Google Drive returned HTTP {resp.status_code}. Please verify the link is accessible.",
        )

    blob = resp.content
    if not blob:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The file downloaded from Google Drive is empty.",
        )

    # If it's HTML and didn't match a binary document, check if it's an error message
    if "text/html" in content_type_header and not fallback_filename.endswith((".html", ".htm")):
        if "access denied" in resp.text.lower() or "permission" in resp.text.lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied accessing Google Drive file. Ensure anyone with the link can view.",
            )
        if "cannot find" in resp.text.lower() or "does not exist" in resp.text.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Google Drive file not found. Please verify the URL.",
            )

    filename = _parse_filename_from_headers(resp.headers, fallback_filename)
    if not os.path.splitext(filename)[1]:
        # If no extension was in the parsed filename, use fallback extension
        fallback_ext = os.path.splitext(fallback_filename)[1]
        filename = f"{filename}{fallback_ext}"

    content_type = _detect_content_type(filename, resp.headers.get("content-type"))
    return filename, content_type, blob


def fetch_dropbox(url: str, default_title: str | None = None) -> Tuple[str, str, bytes]:
    """Download a file from a Dropbox shared link."""
    # Convert Dropbox URL to direct download format
    # Dropbox allows direct download by ensuring dl=1
    parsed = urllib.parse.urlparse(url)
    q_params = urllib.parse.parse_qs(parsed.query)
    q_params["dl"] = ["1"]
    new_query = urllib.parse.urlencode(q_params, doseq=True)
    direct_url = urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
    )

    clean_title = re.sub(r'[\\/*?:"<>|]', "", (default_title or "").strip()) or "dropbox_document"

    # Derive fallback filename from path
    path_name = os.path.basename(parsed.path.rstrip("/"))
    fallback_filename = path_name if path_name and "." in path_name else f"{clean_title}.pdf"

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    try:
        resp = session.get(direct_url, allow_redirects=True, timeout=30)
    except Exception as exc:
        logger.warning("[LeadAI cloud_fetcher] Dropbox fetch failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to connect to Dropbox: {exc}",
        ) from exc

    if resp.status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dropbox file not found or the shared link has expired.",
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Dropbox returned HTTP {resp.status_code}. Please ensure the link is public and accessible.",
        )

    content_type_header = resp.headers.get("content-type", "").lower()
    # Check if Dropbox returned an HTML error/login page instead of binary
    if "text/html" in content_type_header:
        text_lower = resp.text.lower()
        if "sign in" in text_lower or "dropbox login" in text_lower:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Dropbox file requires login. Please ensure the link is publicly accessible.",
            )
        if "deleted" in text_lower or "error" in text_lower:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The Dropbox link appears to be invalid, deleted, or inaccessible.",
            )

    blob = resp.content
    if not blob:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The file downloaded from Dropbox is empty.",
        )

    filename = _parse_filename_from_headers(resp.headers, fallback_filename)
    if not os.path.splitext(filename)[1]:
        fallback_ext = os.path.splitext(fallback_filename)[1]
        filename = f"{filename}{fallback_ext}"

    content_type = _detect_content_type(filename, resp.headers.get("content-type"))
    return filename, content_type, blob


def fetch_generic_url(url: str, default_title: str | None = None) -> Tuple[str, str, bytes]:
    """Download a document from a direct web URL (OneDrive, Box, public files, etc.)."""
    clean_title = re.sub(r'[\\/*?:"<>|]', "", (default_title or "").strip()) or "cloud_document"
    parsed = urllib.parse.urlparse(url)
    path_name = os.path.basename(parsed.path.rstrip("/"))
    fallback_filename = path_name if path_name and "." in path_name else f"{clean_title}.pdf"

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    try:
        resp = session.get(url, allow_redirects=True, timeout=30)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch document from link: {exc}",
        ) from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Server returned HTTP {resp.status_code} when downloading document.",
        )

    blob = resp.content
    if not blob:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Downloaded file is empty.",
        )

    filename = _parse_filename_from_headers(resp.headers, fallback_filename)
    content_type = _detect_content_type(filename, resp.headers.get("content-type"))
    return filename, content_type, blob


def fetch_cloud_file(url: str, default_title: str | None = None) -> Tuple[str, str, bytes]:
    """
    Route any cloud/web URL to the appropriate downloader.
    Returns: (filename, content_type, blob)
    """
    url_str = (url or "").strip()
    if not url_str:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cloud link URL is required.")

    lower = url_str.lower()
    if "drive.google.com" in lower or "docs.google.com" in lower or "drive.usercontent.google.com" in lower:
        return fetch_google_drive(url_str, default_title)
    elif "dropbox.com" in lower:
        return fetch_dropbox(url_str, default_title)
    else:
        return fetch_generic_url(url_str, default_title)
