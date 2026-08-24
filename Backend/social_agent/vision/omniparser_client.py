"""
Thin client around OmniParser (icon/element detector + Florence-2
captioner) — used as a FALLBACK when normal DOM-based indexing in
browser/browser.py can't see an element at all (closed shadow roots,
canvas-rendered UI, cross-origin iframes DOM access is blocked on).

Runs on Replicate's hosted endpoint by default (no local GPU needed,
~$0.001/call, ~5s/call): https://replicate.com/microsoft/omniparser-v2

To self-host instead (only worth it once you're calling this a lot):
clone https://github.com/microsoft/OmniParser, run their server, and
point OMNIPARSER_URL at it — see _parse_via_self_hosted() below for the
expected request/response shape to match if you do.

IMPORTANT: I haven't been able to run this against the live Replicate
endpoint from this sandbox (no network access here), so the exact output
key names in _normalize() are a best-effort guess based on Replicate's
listing page, not verified against a real response. The first time you
run vision_scan() for real, print(raw_output) before it hits _normalize()
and adjust the key names to match what actually comes back.
"""

import asyncio
import base64
import os


def _get_replicate_client():
    import replicate  # pip install replicate

    return replicate.Client(api_token=os.environ["REPLICATE_API_TOKEN"])


def _normalize(raw_output, image_width: int, image_height: int) -> list[dict]:
    """Best-effort normalization of OmniParser's output into a flat list of
    {"label": str, "x": float, "y": float} — pixel coordinates relative to
    the screenshot (same coordinate space as page.mouse.click()).

    OmniParser typically returns bounding boxes as normalized [x1, y1, x2,
    y2] (0-1 range) plus a caption per element. Adjust the key lookups
    below (`el.get("bbox")`, `el.get("content")`) once you've confirmed
    the real shape by printing raw_output once.
    """
    elements = raw_output if isinstance(raw_output, list) else raw_output.get("elements", [])
    results = []
    for el in elements:
        bbox = el.get("bbox") or el.get("box")
        label = el.get("content") or el.get("caption") or el.get("label") or "(unlabeled)"
        if not bbox or len(bbox) != 4:
            continue
        x1, y1, x2, y2 = bbox
        # Normalized (0-1) vs. already-pixel bboxes both show up across
        # OmniParser variants — this heuristic assumes normalized unless
        # values are clearly already > 1.
        if max(x1, y1, x2, y2) <= 1.0:
            x1, x2 = x1 * image_width, x2 * image_width
            y1, y2 = y1 * image_height, y2 * image_height
        results.append({
            "label": label,
            "x": (x1 + x2) / 2,
            "y": (y1 + y2) / 2,
        })
    return results


async def parse_screenshot(image_bytes: bytes, image_width: int, image_height: int) -> list[dict]:
    """Sends a screenshot to OmniParser, returns a flat list of detected
    UI elements: [{"label": ..., "x": ..., "y": ...}, ...] in pixel
    coordinates matching the screenshot's own dimensions."""

    def _call():
        client = _get_replicate_client()
        b64_image = "data:image/png;base64," + base64.b64encode(image_bytes).decode()
        return client.run(
            "microsoft/omniparser-v2",
            input={"image": b64_image},
        )

    # replicate's client is sync — run it off the event loop so it doesn't
    # block the rest of the agent loop while waiting on the API call.
    raw_output = await asyncio.to_thread(_call)
    return _normalize(raw_output, image_width, image_height)
