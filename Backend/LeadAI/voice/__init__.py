"""Pipecat voice pipeline for LeadAI phone calls.

The pipeline handles the audio (telephony transport, voice activity, speech-to-text,
text-to-speech, interruptions). The THINKING is the same shared brain chat uses
(services/voice_flow.py), so a phone call gets the same knowledge grounding, handoff rules,
decision trace and pause/terminate control as a chat message.

    routing.py    which calls use this pipeline (VOICE_PIPELINE, default: none)
    brain.py      the Pipecat stage that turns "the caller finished speaking" into a reply
    session.py    one call's state and database work, off the audio thread
    pipeline.py   assembles the pipeline and serves the Twilio websocket
"""
