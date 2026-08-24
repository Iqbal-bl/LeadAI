"""
Message-history bookkeeping for the agent loop: trimming old tool results so
token usage doesn't grow unbounded on long tasks, plus optional debug
logging of exactly what's being sent to the model each turn.
"""

from langchain_core.messages import AIMessage, ToolMessage

RECENT_FULL_DETAIL = 6


def trim_messages(messages):
    """Keeps every message (so the model remembers what it already did) but
    shrinks old tool results down to a short note instead of their full
    page-state dump once they're more than RECENT_FULL_DETAIL turns old —
    those dumps are the biggest token cost and stop being useful once a
    turn is a few steps in the past."""
    if len(messages) <= RECENT_FULL_DETAIL + 1:
        return messages

    head, older, recent = (
        messages[:1],
        messages[1:-RECENT_FULL_DETAIL],
        messages[-RECENT_FULL_DETAIL:],
    )
    older = [
        ToolMessage(
            content=m.content[:200] + " ...[older state truncated]",
            tool_call_id=m.tool_call_id,
            name=m.name,
        )
        if isinstance(m, ToolMessage) and isinstance(m.content, str) and len(m.content) > 200
        else m
        for m in older
    ]
    return [*head, *older, *recent]


def log_llm_input(messages):
    print("\n" + "=" * 70)
    print(f"[LLM INPUT] {len(messages)} messages being sent to the model:")
    for i, m in enumerate(messages):
        role = m.__class__.__name__
        if isinstance(m.content, list):
            # Multimodal content (e.g. text + image_url blocks from vision
            # mode) — show a concise placeholder for images instead of
            # dumping the raw base64 string, which would flood the console.
            parts = []
            for block in m.content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    parts.append("[image attached — see last_vision_frame.png]")
                elif isinstance(block, dict) and "text" in block:
                    parts.append(block["text"])
                else:
                    parts.append(str(block))
            content = " ".join(parts)
        else:
            content = m.content if isinstance(m.content, str) else str(m.content)
        preview = content if len(content) <= 1000 else content[:1000] + " ...[truncated for display]"
        print(f"\n  [{i}] {role}")
        if isinstance(m, ToolMessage):
            print(f"      tool_call_id={m.tool_call_id} name={m.name}")
        print(f"      content: {preview}")
        if isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                print(f"      -> tool_call: {tc['name']}({tc['args']})")
    print("=" * 70 + "\n")
