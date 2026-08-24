"""
Manual smoke test for graph_api/ — calls the raw functions directly, with
NO agent/LLM involved. Run this first before wiring anything into a task
prompt: it isolates real Graph API errors (bad token, missing permission,
wrong IDs) from agent-reasoning errors.

Comment/uncomment the calls you want to test. Requires .env to have:
    FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN, IG_BUSINESS_ACCOUNT_ID (as needed)

Run:
    python graph_api/smoke_test.py
"""

import asyncio

from dotenv import load_dotenv

from graph_api import comments, instagram, leads, pages

load_dotenv()


async def main():
    # --- Facebook Page: text post -----------------------------------
    # result = await pages.create_text_post("Testing the Graph API integration.")
    # print("create_text_post ->", result)

    # --- Facebook Page: photo post (local file) ----------------------
    # result = await pages.create_photo_post(
    #     "media/three-horses.jpeg", caption="Testing photo upload."
    # )
    # print("create_photo_post ->", result)

    # --- Facebook Page: read recent posts -----------------------------
    # result = await pages.get_recent_posts(limit=5)
    # print("get_recent_posts ->", result)

    # --- Comments: find and reply -------------------------------------
    # unreplied = await comments.list_unreplied_comments()
    # print("list_unreplied_comments ->", unreplied)
    # if unreplied:
    #     result = await comments.reply_to_comment(
    #         unreplied[0]["comment_id"], "Thanks for the comment!"
    #     )
    #     print("reply_to_comment ->", result)

    # --- Instagram: publish a photo (needs a PUBLIC image URL) --------
    result = await instagram.publish_photo(
        "https://i.postimg.cc/1zkLpWbq/6bb0eb27e53054f104f6d190977d2247.jpg", caption="Testing IG publish."
    )
    print("publish_photo ->", result)

    # --- Instagram: read recent media ---------------------------------
    # result = await instagram.get_recent_media(limit=5)
    # print("get_recent_media ->", result)

    # --- Lead Ads: list forms on a Page --------------------------------
    # import os
    # result = await leads.list_lead_forms(os.environ["FB_PAGE_ID"])
    # print("list_lead_forms ->", result)

    # --- Lead Ads: fetch submissions for a specific form ----------------
    # result = await leads.get_leads("YOUR_FORM_ID_HERE")
    # print("get_leads ->", result)


if __name__ == "__main__":
    asyncio.run(main())
