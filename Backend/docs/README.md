# Docs

Current guides
- [DEPLOYMENT.md](DEPLOYMENT.md) — installing on a server (MySQL, ngrok, PM2)
- [DOCKER.md](DOCKER.md) — running with Docker
- [INSTAGRAM_STANDALONE_SETUP.md](INSTAGRAM_STANDALONE_SETUP.md) — connecting Instagram without a Facebook Page
- [LEADAI_EXPANSION.md](LEADAI_EXPANSION.md) — architecture decisions and the frontend guide for channels, campaigns, CRM
- [SOCIAL_ARCHITECTURE.md](SOCIAL_ARCHITECTURE.md), [SOCIAL_KEY_CONCEPTS.md](SOCIAL_KEY_CONCEPTS.md), [SOCIAL_PUBLISHING.md](SOCIAL_PUBLISHING.md), [SOCIAL_FRONTEND_GUIDE.md](SOCIAL_FRONTEND_GUIDE.md), [README_SOCIAL.md](README_SOCIAL.md) — social publishing
- [LINKEDIN_DM_AUTOMATION_PLAN.md](LINKEDIN_DM_AUTOMATION_PLAN.md) — plan, not built yet
- [../LeadAI/API_DOCUMENTATION.md](../LeadAI/API_DOCUMENTATION.md) — LeadAI API reference (written before billing, channels and campaigns; parts are out of date)

History (written when those changes were made; file names in them are from before the reorganisation)
- [history/](history/)

## Old file names

The backend was reorganised into `core/`, `domain/` and `outbound/`. Old names in older notes map like this:

| Old | New |
|---|---|
| `multiligual_call.py` | `outbound/app.py` |
| `batching.py` | `outbound/batching.py` |
| `db.py` | `outbound/call_store.py` |
| `database.py`, `base.py`, `auth.py`, `storage.py`, `token_validation.py`, `config_guard.py` | `core/` (same names) |
| `swagger_schema.py` | `core/swagger.py` |
| `Websockets/connection.py` | `core/websocket_manager.py` |
| `Domain/` | `domain/` |
| `Repositories/`, `bot/` | `outbound/repositories/`, `outbound/bot/` |
| `sarvam_stt.py`, `sarvam_tts.py` | `outbound/speech/` |
| `validate_number.py`, `xml_parser.py`, `globals.py`, `response.py` | `outbound/phone.py`, `outbound/xml_parser.py`, `outbound/call_state.py`, `outbound/batch_responses.py` |
| `voice_agent.py`, `conversation_entity.py` | removed (unused; still in git history before this reorganisation) |
