"""
Named, parameterized prompt templates for the LLM agent. Each entry fills
{placeholders} at run time — lets a task run by name instead of typing a
fresh prompt each time. Execution is still the full LangGraph agent.

REPLY POLICY (applies to every comment/message reply task below,
regardless of whether it uses the browser or Graph API): factual
questions must be answered ONLY via answer_company_question (CRAG) —
never from general knowledge. Anything else gets a short, generic
acknowledgment only — no invented specifics, no claims not sourced from
the company knowledge base.
"""

TASK_PROMPTS = {
    "youtube_video_metrics": (
        'Go to YouTube and search for "{query}". Find the first video '
        "result that is NOT a sponsored/advertisement result — if the "
        "first result is sponsored, skip to the next one. Open that "
        "video and report its title, total view count, and total like "
        "count. Then call finish with those details."
    ),
    "wikipedia_text_extraction": (
        'Go to "https://en.wikipedia.org/wiki/{topic}". Call extract_text '
        "to read the page content. Summarize the main points in a few "
        "sentences, then call finish with that summary."
    ),

    # ============================================================
    # LEGACY — personal Facebook/Instagram PROFILE browser automation.
    # Kept for reference/testing only. The Page now runs on Graph API
    # (see the Graph API section below) — these are not used for the
    # Page and should not be pointed at it.
    # ============================================================
    "facebook_like_feed_posts": (
        "Go to the Facebook homepage (https://facebook.com). Scroll through "
        "the home feed and like up to {count} posts{topic_clause}. For each "
        "post, look for a 'Like' button/reaction control near the bottom of "
        "the post and click it — don't click the same post's like button "
        "twice. Skip sponsored/ad posts. Stop once you've liked {count} "
        "posts (or scrolled through a reasonable amount of the feed without "
        "finding more). Call finish with a summary of which posts you liked."
    ),
    "facebook_send_friend_requests": (
        "Go to Facebook's 'Friends' suggestions page "
        "(https://www.facebook.com/friends/suggestions). Send up to {count} "
        "friend requests to suggested people by clicking their 'Add friend' "
        "button. Call finish with a summary of who you sent requests to."
    ),
    "facebook_accept_friend_requests": (
        "Go to Facebook's friend requests page "
        "(https://www.facebook.com/friends/requests). Accept up to {count} "
        "pending friend requests by clicking 'Confirm'. If there are none, "
        "say so. Call finish with a summary of who you accepted."
    ),
    "facebook_reply_messenger": (
        "Go to Facebook Messenger (https://www.facebook.com/messages). Look "
        "for a conversation marked [likely-unread] in the current page "
        "state. If none, call finish immediately and say so. Open the "
        "first [likely-unread] conversation and read the latest message. "
        "If it asks a factual question about the company/product/pricing, "
        "call answer_company_question and reply ONLY with what it returns "
        "— never guess from general knowledge. If it doesn't, or the "
        "question isn't covered by answer_company_question, send only a "
        "short, generic acknowledgment (e.g. thanks for the message, "
        "someone will follow up) — do not invent specifics. If the latest "
        "message is a photo/video, acknowledge receiving media rather than "
        "guessing its content. Verify the reply actually appears before "
        "moving on — do not send it twice. Repeat for up to {count} "
        "[likely-unread] conversations. Call finish with a summary."
    ),
    "instagram_reply_to_messages": (
        "Go to Instagram Direct Messages (https://www.instagram.com/direct/inbox/). "
        "Wait until the conversation list has loaded. Look for a "
        "conversation marked [likely-unread]. If none, call finish "
        "immediately and say so. Open the first [likely-unread] "
        "conversation and read the latest incoming message via "
        "extract_text. If it asks a factual question about the "
        "company/product/pricing, call answer_company_question and reply "
        "ONLY with what it returns. Otherwise send only a short, generic "
        "acknowledgment — no invented specifics. If the message is a "
        "photo/video, acknowledge receiving media rather than guessing its "
        "content. Type the reply, send it ONCE via the 'Send' button or "
        "Enter, and verify it appears as the latest outgoing message before "
        "moving on — do not resend. Repeat for remaining [likely-unread] "
        "conversations. Call finish with how many you replied to."
    ),
    "facebook_create_post": (
        "Go to the Facebook homepage (https://facebook.com). Click the "
        "'What's on your mind?' area to open the post composer. Type this "
        "message into the text box: \"{message}\". "
        "{media_clause}"
        "Then find and click the 'Post' button. Before calling finish, "
        "verify the dialog actually closed and the post appears at the top "
        "of the feed, and if media was attached, verify the image/video "
        "thumbnail is visible in that new post. Call finish with "
        "confirmation of what you observed."
    ),

    # ============================================================
    # Graph API — the Page's actual production path. No browser, no
    # personal profile. Typically run on a schedule (see scheduler.py)
    # rather than manually.
    # ============================================================
    "facebook_reply_to_comments": (
        "Call graph_list_unreplied_comments to get every unreplied comment "
        "on the Page's recent posts, at any nesting depth (a comment can "
        "itself be a reply to another comment — reply to those too). If "
        "it returns none, call finish saying so immediately. "
        "For EACH unreplied comment, one at a time: "
        "1. If it asks a factual question about the company, its "
        "products, pricing, or features, call answer_company_question "
        "with that question and base your reply ONLY on what it returns. "
        "Never answer from your own general knowledge. If it says it "
        "doesn't know, say so honestly rather than guessing. "
        "2. If it's not a factual question (a general comment, "
        "compliment, complaint, or anything answer_company_question isn't "
        "for), reply with only a short, generic acknowledgment (e.g. "
        "'Thanks for your comment!', 'Appreciate you sharing that!') — do "
        "not invent specifics or draw on outside information. "
        "3. Call graph_reply_to_comment with that comment's comment_id "
        "and your reply text. Keep every reply to 1-2 sentences. "
        "4. Move to the next comment. "
        "When done, call finish with how many comments you replied to and "
        "how many used answer_company_question."
    ),
    "facebook_reply_to_messages": (
        "Call graph_list_unreplied_messages to get Page Messenger "
        "conversations with unread messages. If none, call finish saying "
        "so immediately. "
        "For EACH one, one at a time: "
        "1. If the message asks a factual question about the company, "
        "its products, pricing, or features, call answer_company_question "
        "and base your reply ONLY on what it returns — never from general "
        "knowledge. If it doesn't know, say so honestly. "
        "2. Otherwise, reply with only a short, generic acknowledgment — "
        "no invented specifics, no outside information. "
        "3. Call graph_reply_to_message with that conversation's "
        "sender_psid and your reply text. Keep replies to 1-2 sentences. "
        "4. Move to the next conversation. "
        "When done, call finish with how many you replied to and how many "
        "used answer_company_question."
    ),
    "instagram_reply_to_comments": (
        "Call graph_list_unreplied_instagram_comments to get every "
        "unreplied comment on the Instagram account's recent media. If it "
        "returns none, call finish saying so immediately. "
        "For EACH unreplied comment, one at a time: "
        "1. If it asks a factual question about the company, its "
        "products, pricing, or features, call answer_company_question "
        "and base your reply ONLY on what it returns. Never answer from "
        "your own general knowledge. If it doesn't know, say so honestly. "
        "2. Otherwise, reply with only a short, generic acknowledgment — "
        "no invented specifics, no outside information. "
        "3. Call graph_reply_to_instagram_comment with that comment's "
        "comment_id and your reply text. Keep replies to 1-2 sentences. "
        "4. Move to the next comment. "
        "When done, call finish with how many comments you replied to and "
        "how many used answer_company_question."
    ),
    "instagram_reply_to_messages": (
        "Call graph_list_unreplied_instagram_messages to get Instagram DM "
        "conversations with unread messages. If none, call finish saying "
        "so immediately. "
        "For EACH one, one at a time: "
        "1. If the message asks a factual question about the company, "
        "its products, pricing, or features, call answer_company_question "
        "and base your reply ONLY on what it returns — never from general "
        "knowledge. If it doesn't know, say so honestly. "
        "2. Otherwise, reply with only a short, generic acknowledgment — "
        "no invented specifics, no outside information. "
        "3. Call graph_reply_to_instagram_message with that conversation's "
        "sender_igsid and your reply text. Keep replies to 1-2 sentences. "
        "4. Move to the next conversation. "
        "When done, call finish with how many you replied to and how many "
        "used answer_company_question."
    ),
    # NOTE: original post content generation (below) is NOT under the
    # strict CRAG-only reply policy — that policy is specifically for
    # replies to other people's comments/messages. Writing your own
    # promotional post from a topic is normal creative writing.
    "facebook_post_from_topic": (
        "Write a short, engaging Facebook post about this topic: "
        "\"{topic}\". Follow these instructions: \"{instructions}\". Keep "
        "it natural and not overly promotional. "
        "If an image_url is given below, use it as-is. If not, come up "
        "with a short, concrete visual description matching the topic "
        "(e.g. \"a doctor reviewing an X-ray on a tablet\") and call "
        "generate_image_for_post with that description to get an "
        "image_url. "
        "image_url: \"{image_url}\" "
        "Then call graph_create_facebook_post with your written text as "
        "the message, and the image_url (given or generated) as "
        "image_path. Call finish confirming the post was created."
    ),
    "instagram_post_from_topic": (
        "Write a short, engaging Instagram caption about this topic: "
        "\"{topic}\". Follow these instructions: \"{instructions}\". "
        "Instagram posts always need an image. If an image_url is given "
        "below, use it as-is. If not, come up with a short, concrete "
        "visual description matching the topic and call "
        "generate_image_for_post with that description to get an "
        "image_url. "
        "image_url: \"{image_url}\" "
        "Then call graph_publish_instagram_photo with that image_url and "
        "your written caption. Call finish confirming the post was "
        "created."
    ),
    "facebook_post_video": (
        "Write a short Facebook caption for a video about this topic: "
        "\"{topic}\". Follow these instructions: \"{instructions}\". Then "
        "call graph_create_facebook_video_post with video_path_or_url="
        "\"{video_url}\" and your caption as the message. Call finish "
        "confirming the post was created."
    ),
    "instagram_post_reel": (
        "Write a short Instagram caption for a Reel about this topic: "
        "\"{topic}\". Follow these instructions: \"{instructions}\". Then "
        "call graph_publish_instagram_reel with video_url=\"{video_url}\" "
        "and your caption. This can take a couple minutes to process — "
        "wait for the result rather than assuming failure. Call finish "
        "confirming the post was created."
    ),
    # Add more named tasks here as you need them — see docs/ADDING_FEATURES.md.
}
