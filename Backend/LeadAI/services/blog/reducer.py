"""
Reducer node for merging sections, planning image placements, generating images, and assembling final Markdown.
"""
import base64
import os
import re
from io import BytesIO
from typing import List, Tuple
import requests
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ...config import settings
from .schemas import GlobalImagePlan, Plan
from .state import State


def _get_llm():
    api_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY")
    return ChatOpenAI(
        model=settings.openai_model or "gpt-4o-mini",
        api_key=api_key,
        temperature=0.2,
    )


def merge_content(state: State) -> dict:
    """Merge worker-generated sections into a single Markdown document in task sequence."""
    plan: Plan = state["plan"]
    sections: List[Tuple[int, str]] = state.get("sections", []) or []

    # Sort sections according to task IDs
    sections = sorted(sections, key=lambda item: item[0])
    ordered_sections = [content for _, content in sections]
    body = "\n\n".join(ordered_sections).strip()

    merged_md = f"# {plan.blog_title}\n\n{body}\n"
    return {"merged_md": merged_md}


def get_decide_images_system(num_images: int = 1) -> str:
    placeholders = "\n".join(f"[[IMAGE_{i}]]" for i in range(1, num_images + 1))
    return f"""You are an expert visual content planner and technical editor.

Decide whether diagrams, visual illustrations, or architecture flow images are needed for THIS blog post.

Requirements:
1. Decide up to {num_images} relevant visual images that materially enhance readability or explanation.
2. Return the COMPLETE input Markdown with [[IMAGE_X]] placeholders inserted at the most appropriate positions (immediately after the relevant section).
3. Do not place all images at the very bottom.
4. Preserve all original Markdown text without truncating.

Allowed placeholders:
{placeholders}

For each image specification in `images`, provide:
- placeholder (e.g. [[IMAGE_1]])
- filename (e.g. workflow_diagram.jpg)
- alt (short description)
- caption (clean caption)
- prompt (detailed, modern AI image prompt for visual generation)
- size (1024x1024)
- quality (medium)

Return strictly GlobalImagePlan schema.
"""


def decide_images(state: State) -> dict:
    """Decide image specifications and insert placeholders in Markdown."""
    merged_md = state["merged_md"]
    plan: Plan = state.get("plan")

    blog_type = state.get("blog_type", "text_and_image")
    include_images = state.get("include_images", True)
    num_images = state.get("num_images", 1) or 1

    if blog_type == "text_only" or not include_images or num_images <= 0:
        return {
            "md_with_placeholders": merged_md,
            "image_specs": [],
        }

    llm = _get_llm()
    planner = llm.with_structured_output(GlobalImagePlan)
    system_prompt = get_decide_images_system(num_images=num_images)

    try:
        image_plan: GlobalImagePlan = planner.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(
                    content=(
                        f"Topic: {state['topic']}\n"
                        f"Max images: {num_images}\n\n"
                        f"Markdown:\n{merged_md}"
                    )
                ),
            ]
        )
        image_specs = [img.model_dump() for img in image_plan.images[:num_images]]
        md_with_placeholders = image_plan.md_with_placeholders or merged_md
    except Exception as exc:
        print(f"[Reducer] decide_images error: {exc}")
        image_specs = []
        md_with_placeholders = merged_md

    # Ensure placeholders are present in Markdown
    for idx, spec in enumerate(image_specs, start=1):
        placeholder = f"[[IMAGE_{idx}]]"
        spec["placeholder"] = placeholder
        if placeholder not in md_with_placeholders:
            md_with_placeholders = md_with_placeholders + f"\n\n{placeholder}\n"

    return {
        "md_with_placeholders": md_with_placeholders,
        "image_specs": image_specs,
    }


def _generate_image_bytes(prompt: str) -> bytes:
    """Generate image bytes using Cloudflare Flux, OpenAI DALL-E, or high-res PIL graphics."""
    # 1. Try Cloudflare Workers AI (if credentials present)
    cf_account = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    cf_token = os.getenv("CLOUDFLARE_API_TOKEN")
    if cf_account and cf_token:
        try:
            url = f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/run/@cf/black-forest-labs/flux-1-schnell"
            headers = {
                "Authorization": f"Bearer {cf_token}",
                "Content-Type": "application/json",
            }
            resp = requests.post(url, headers=headers, json={"prompt": prompt}, timeout=90)
            if resp.status_code == 200:
                res_data = resp.json()
                if res_data.get("success") and res_data.get("result", {}).get("image"):
                    return base64.b64decode(res_data["result"]["image"])
        except Exception as exc:
            print(f"[Reducer] Cloudflare image generation warning: {exc}")

    # 2. Try OpenAI Image API
    openai_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)
            img_res = client.images.generate(
                model="dall-e-3",
                prompt=prompt[:950],
                size="1024x1024",
                quality="standard",
                n=1,
            )
            if img_res.data:
                first = img_res.data[0]
                if getattr(first, "b64_json", None):
                    return base64.b64decode(first.b64_json)
                elif getattr(first, "url", None):
                    img_resp = requests.get(first.url, timeout=60)
                    if img_resp.status_code == 200:
                        return img_resp.content
        except Exception as exc:
            print(f"[Reducer] OpenAI image generation warning: {exc}")

    # 3. Fallback: Generate a high quality clean banner image
    try:
        from PIL import Image, ImageDraw, ImageFont
        width, height = 1200, 630
        img = Image.new("RGB", (width, height), color="#0f172a")
        draw = ImageDraw.Draw(img)
        # Background gradient effect
        for y in range(height):
            r = int(15 + (y / height) * 20)
            g = int(23 + (y / height) * 45)
            b = int(42 + (y / height) * 80)
            draw.line([(0, y), (width, y)], fill=(r, g, b))

        # Card container
        draw.rounded_rectangle([(60, 60), (width - 60, height - 60)], radius=24, outline="#3b82f6", width=3, fill=(15, 23, 42, 220))
        # Text
        draw.text((100, 120), "LEADAI INTELLIGENCE", fill="#60a5fa")
        # Draw wrapped prompt
        words = prompt.split()
        lines = []
        cur = []
        for w in words:
            cur.append(w)
            if len(" ".join(cur)) > 45:
                lines.append(" ".join(cur))
                cur = []
        if cur:
            lines.append(" ".join(cur))

        y_pos = 200
        for l in lines[:5]:
            draw.text((100, y_pos), l, fill="#f8fafc")
            y_pos += 45

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except Exception as fallback_exc:
        print(f"[Reducer] Fallback image error: {fallback_exc}")
        return b""


def _upload_image_to_storage(image_bytes: bytes, filename: str) -> str:
    """Upload image bytes to MinIO or LeadAI ObjectStore and return public/accessible URL."""
    if not image_bytes:
        return ""

    minio_endpoint = (os.getenv("MINIO_ENDPOINT") or "").replace("http://", "").replace("https://", "").strip()
    minio_public = (os.getenv("MINIO_PUBLIC_ENDPOINT") or "").strip()
    minio_bucket = os.getenv("MINIO_BUCKET_DOCUMENTS") or os.getenv("MINIO_BUCKET") or "leadai-media"
    access_key = os.getenv("MINIO_ACCESS_KEY")
    secret_key = os.getenv("MINIO_SECRET_KEY")

    if minio_endpoint and access_key and secret_key:
        try:
            from minio import Minio
            import urllib3

            verify_ssl = os.getenv("MINIO_VERIFY_SSL", "false").lower() == "true"
            http_client = urllib3.PoolManager(
                cert_reqs="CERT_REQUIRED" if verify_ssl else "CERT_NONE"
            )
            client = Minio(
                endpoint=minio_endpoint,
                access_key=access_key,
                secret_key=secret_key,
                secure=True,
                http_client=http_client,
            )
            if not client.bucket_exists(minio_bucket):
                client.make_bucket(minio_bucket)

            object_name = f"blog-media/{filename}"
            client.put_object(
                bucket_name=minio_bucket,
                object_name=object_name,
                data=BytesIO(image_bytes),
                length=len(image_bytes),
                content_type="image/jpeg",
            )

            pub_endpoint = minio_public or f"https://{minio_endpoint}"
            return f"{pub_endpoint.rstrip('/')}/{minio_bucket}/{object_name}"
        except Exception as exc:
            print(f"[Reducer] MinIO upload error: {exc}")

    # Fallback: base64 data URI if no remote object store configured
    b64_str = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:image/jpeg;base64,{b64_str}"


def generate_and_place_images(state: State) -> dict:
    """Generate image bytes for each spec, upload to storage, and replace placeholders in Markdown."""
    plan = state.get("plan")
    md = state.get("md_with_placeholders") or state.get("merged_md") or ""
    image_specs = state.get("image_specs", []) or []

    if not image_specs:
        return {"final": md}

    for spec in image_specs:
        placeholder = spec.get("placeholder", "[[IMAGE_1]]")
        filename = spec.get("filename") or "blog_image.jpg"
        prompt = spec.get("prompt", "")
        alt = spec.get("alt", "AI Generated Graphic")
        caption = spec.get("caption", "")

        img_bytes = _generate_image_bytes(prompt)
        image_url = _upload_image_to_storage(img_bytes, filename)

        if image_url:
            image_md = f"![{alt}]({image_url})"
            if caption:
                image_md += f"\n\n*{caption}*"
            md = md.replace(placeholder, image_md)
        else:
            md = md.replace(placeholder, "")

    return {"final": md}
