# LinkedIn DM & Chat AI Automation Plan

This document outlines the proposed design and implementation strategy for integrating AI-driven messaging and chat into the LeadAI LinkedIn module. This plan is designed to be built in a future phase.

---

## 1. Core Objectives
* **Auto-Reply**: Enable the existing conversational AI engine to respond to incoming LinkedIn messages.
* **Unified Inbox**: Synchronize LinkedIn direct messages with the LeadAI shared database so staff can view/reply from the dashboard.
* **Account Safety**: Protect LinkedIn accounts from restriction/bans by minimizing Voyager API traffic.

---

## 2. Polling Strategy: Dynamic State-Aware Polling (Recommended)

Since LinkedIn does not provide real-time webhooks for personal profiles, the background worker must poll for messages. To prevent anti-scraping flags, we will use a **Dynamic Polling** model:

```
               +----------------------------------+
               |  Idle Baseline (No Active Chat)  |
               |         Poll every 30m           |
               +----------------+-----------------+
                                |
                   New Incoming / Outgoing Message
                                |
                                v
               +----------------------------------+
               |   Active State (Chat Engaged)    |
               |         Poll every 2m            |
               +----------------+-----------------+
                                |
                    10 Minutes of Silence
                                |
                                v
               +----------------------------------+
               |  Idle Baseline (No Active Chat)  |
               |         Poll every 30m           |
               +----------------+-----------------+
```

### Safety Features
1. **Human Jitter**: Add a randomized sleep delay (e.g. `random.uniform(5.0, 15.0)`) before executing any message fetch operation.
2. **Business Hours restriction**: Only poll for messages during configured company hours (e.g., 9:00 AM to 6:00 PM local time).
3. **Inbox Activity Hook**: If the staff user has the unified chat dashboard open in their browser, the frontend can trigger an on-demand sync (checking only active chats).

---

## 3. Why the "Email-Triggered Sync" is Not Recommended

We analyzed the option of reading LinkedIn notification emails from a user's mailbox to trigger the message sync. This is not recommended because:
* **Configurable Notification Rules**: Users can toggle off or customize email notifications on LinkedIn. If turned off, the AI will fail to reply.
* **Delays and Bundling**: LinkedIn often bundles incoming messages into digest emails or delays sending them by several minutes.
* **Security Consent**: Requiring users to grant the CRM read/write access to their business email accounts (IMAP/Google OAuth) creates high friction and setup complexity.

---

## 4. Proposed Technical Changes (For Future Implementation)

### A. Core Outgoing Dispatcher
Extend the `send_text` method in [channels.py](file:///c:/BharatLogic/AIOutbound-with-LeadAI-backend/LeadAI/services/channels.py) to dispatch outbound LinkedIn messages:

```python
elif channel == "linkedin":
    from ..social.linkedin_bot import get_linkedin_client
    api = get_linkedin_client(account)
    
    # Send using the bot client with fallback logic
    try:
        api.send_message(recipients=[to], message_body=text)
    except Exception:
        # Fallback to existing conversation thread URN
        api.send_message(conversation_urn_id=to, message_body=text)
```

### B. Inbound Sync Job
Add a background job `"linkedin.sync_messages"` to [jobs.py](file:///c:/BharatLogic/AIOutbound-with-LeadAI-backend/LeadAI/services/jobs.py):
1. Calls `api.get_conversations()` to retrieve the latest chats.
2. Filters for new messages received from the candidate.
3. Ingests them as `LeadMessage` rows in the database.
4. Triggers the AI agent (`conversation_flow.process_inbound_message`).

---

## 5. Summary of Benefits
* **No Account Restriction**: Polling traffic is minimized and randomized to emulate natural human behavior.
* **Plug-and-Play AI**: Seamlessly hooks into the existing RAG/AI agent engine (`conversation_flow.py`).
* **Zero Mailbox Dependencies**: No email API integrations or credentials required.
