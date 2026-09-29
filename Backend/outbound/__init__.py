"""The original AI outbound-calling app (voice calls and batch calling).

LeadAI plugs into this app rather than replacing it: main.py imports `app` from
here, adds the batch routes, then registers LeadAI on top.

    app.py             FastAPI app: /api/make-call, Twilio webhooks, /media-stream
                       (live audio <-> Sarvam speech <-> LLM), transcripts, scripts API
    batching.py        batch-calling engine and /api/batches routes
    call_identity.py   works out the agent's name, company and call topic from the script
    batch_responses.py response helpers for the batch routes
    call_state.py      process-wide call state (hangup reasons)
    call_store.py      call data access: users, transcripts, call logs, recordings
    phone.py           phone number validation (E.164)
    xml_parser.py      reads and writes the XML agent-script format
    speech/            Sarvam speech-to-text and text-to-speech clients
    repositories/      data access classes for the batch tables
    bot/               insurance-claim status bot toolkit; only the batch CSV export
                       (bot/batch_level_csv_created.py) is used by the running app
    templates/ static/ the built-in demo call page served at "/" and "/call"
    system_prompt.txt  fallback prompt for the voice agent
"""
