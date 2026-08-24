"""Upload one local image or video to MinIO and print its public URL.

Examples:
    python tasks/minio/upload_media.py media/example_photo.jpg
    python tasks/minio/upload_media.py C:\\path\\to\\video.mp4

Requires the MINIO_* settings in .env. The returned URL can be used as an
image_url/video_url for Facebook and Instagram Graph API posting.
"""

import argparse
import asyncio
import base64
import mimetypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from media_hosting.minio_upload import upload_base64


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload local media to MinIO.")
    parser.add_argument("file", type=Path, help="Local image or video file to upload")
    parser.add_argument(
        "--mime-type",
        help="Override the detected MIME type, e.g. image/jpeg or video/mp4",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    file_path = args.file.resolve()
    if not file_path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")

    mime_type = args.mime_type or mimetypes.guess_type(file_path.name)[0]
    if not mime_type or not mime_type.startswith(("image/", "video/")):
        raise ValueError(
            "Could not determine an image/video MIME type. Use --mime-type, "
            "for example: --mime-type image/jpeg"
        )

    base64_data = base64.b64encode(file_path.read_bytes()).decode("ascii")
    print(f"Uploading {file_path.name} ({mime_type}) to MinIO...")
    url = await upload_base64(base64_data, mime_type)
    print("\nPublic URL:")
    print(url)


if __name__ == "__main__":
    asyncio.run(main())
