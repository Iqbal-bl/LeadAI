"""Free AI image generation, exposed as a tool for post-creation tasks."""

from langchain_core.tools import tool

from social_agent.image_gen import pollinations


@tool
def generate_image_for_post(prompt: str) -> str:
    """Generates an image from a text description and returns a public
    URL for it — use this when a post needs media but none was provided.
    Pass a short, concrete visual description (e.g. "a doctor reviewing
    an X-ray on a tablet, modern clinic"), not the post's caption text.
    The returned URL can be passed directly as image_url to
    graph_create_facebook_post or graph_publish_instagram_photo — no
    separate download/upload step needed."""
    return pollinations.generate_image_url(prompt)


IMAGE_GEN_TOOLS = [generate_image_for_post]
