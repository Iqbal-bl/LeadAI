"""
Free, no-API-key image generation via Pollinations.ai. Hitting the URL
IS the generation — the same URL is also directly usable as image_url
for Facebook/Instagram posting, since it's already publicly reachable.
No hosting step needed.

Anonymous rate limit: ~1 request/15s. Fine for occasional post-image
generation; not meant for bulk/production-scale volume.
"""

import urllib.parse

BASE_URL = "https://image.pollinations.ai/prompt"


def generate_image_url(prompt: str, width: int = 1024, height: int = 1024) -> str:
    """Returns a public URL that generates (and serves) an image matching
    the prompt. Pass this URL directly as image_url/video_url to any
    graph_api posting function — no download/rehost needed."""
    encoded = urllib.parse.quote(prompt)
    return f"{BASE_URL}/{encoded}?width={width}&height={height}&nologo=true"
