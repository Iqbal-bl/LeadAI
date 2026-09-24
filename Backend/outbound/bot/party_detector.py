"""
Party Detector Agent — LLM-based IVR/Human classification.

Runs in parallel with the main InsuranceClaimAgent as an asyncio background
task.  Analyses the last few conversation turns and decides whether the
other party on the call is an automated IVR system or a live human agent.

The result is written to a shared flag (`other_party_type`) on the main
agent instance so the *next* main-agent turn can adapt its response style.

Design constraints
──────────────────
• ZERO added latency  – fires via asyncio.create_task(); the main response
  stream is never awaited on the detector result.
• Minimal cost        – gpt-4o-mini, ~6 messages context, 1-token answer.
• Sticky transitions  – once "human" is detected it stays human unless the
  conversation clearly returns to an IVR menu (e.g. after a transfer).
"""

import logging
import openai as _openai
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# ── Detector system prompt (constant, cacheable) ──────────────────────────

DETECTOR_SYSTEM_PROMPT = """You are a call-party classifier. You will receive conversation turns between an AI (assistant) and the other party (user).

Your ONLY job: decide if the other party has **TRANSFERRED** to a live human agent.

### IVR (Default) - BE EXTREMELY SKEPTICAL. DO NOT CHANGE IF:
- The system is still in menus ("Press 1", "Say claim").
- There are recording disclaimers ("Call may be recorded", "Para español...").
- The voice is playing holds, queue music, or periodic "Your call is important" messages.
- The voice is a robotic prompt asking for information.
- The voice sounds like a "very natural" recording but is just a higher-quality automated menu.

### HUMAN (Transition Point) - ONLY CHANGE TO HUMAN IF:
- **TRANSFER EVENT**: You hear clear signals like "Connecting you to a representative", "One moment please", "Transferring your call".
- **HUMAN GREETING**: A new person enters and says "Hi, this is [Name] in [Department], how can I help you today?"
- **DYNAMIC DIALOGUE**: The voice responds to your specific provider name or NPI in a way that is clearly NOT a programmed menu path.

### RULES:
1. **ONE-WAY SWITCH**: We start as `ivr`. We only switch to `human` after multiple definitive verdicts. 
2. **NO NOISE SWITCHING**: Do not switch to human based on silence, noise, or short "Hello?" fragments unless followed by a full human introduction.
3. Respond with EXACTLY one word: `ivr` or `human`."""


class PartyDetectorAgent:
    """Lightweight LLM agent that classifies the other party as IVR or human."""

    def __init__(self):
        self._openai = _openai
        # Current classification (shared with main agent)
        self.current_party_type: str = "ivr"   # default: IVR
        # Track consecutive verdicts to avoid flip-flopping
        self._human_streak: int = 0
        self._ivr_streak: int = 0

    # ── Public API ────────────────────────────────────────────────────────

    async def detect(
        self,
        conversation_history: List[Dict[str, str]],
    ) -> str:
        """
        Classify the other party based on recent conversation history.

        Returns "ivr" or "human" and updates `self.current_party_type`.
        This method is designed to be called via asyncio.create_task()
        so it never blocks the main agent's response stream.
        """
        # Take only the tail of the history to keep the context tiny
        recent = self._get_recent_turns(conversation_history, max_turns=8)
        if not recent:
            return self.current_party_type  # nothing to judge yet

        messages = [
            {"role": "system", "content": DETECTOR_SYSTEM_PROMPT},
        ]
        # Feed the recent turns as-is (role = user / assistant)
        messages.extend(recent)

        # Final explicit decision nudge
        last_turn_text = recent[-1]["content"] if recent else "None"
        messages.append({
            "role": "user",
            "content": (
                f"SYSTEM STATUS:\n"
                f"- Current Mode: {self.current_party_type}\n"
                f"- Latest Chat Segment: \"{last_turn_text}\"\n\n"
                "CONSIDERATION:\n"
                "Does the conversation history above (especially the latest segment) indicate "
                f"that we should CHANGE the mode from {self.current_party_type} to "
                f"{'human' if self.current_party_type == 'ivr' else 'ivr'}?\n\n"
                "Respond with EXACTLY one word: `ivr` or `human`."
            ),
        })

        try:
            resp = await self._openai.ChatCompletion.acreate(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.0,
                max_tokens=10,
            )
            raw = (resp["choices"][0]["message"]["content"] or "").strip().lower()
            verdict = "human" if "human" in raw else "ivr"
        except Exception as e:
            logger.warning(f"[PARTY DETECTOR] LLM call failed: {e}")
            return self.current_party_type

        # ── Hysteresis transition logic ──────────────────────────────────
        self._apply_verdict(verdict)

        logger.info(
            f"[PARTY DETECTOR] Verdict={verdict} → "
            f"current_party_type={self.current_party_type} "
            f"(human_streak={self._human_streak}, ivr_streak={self._ivr_streak})"
        )
        return self.current_party_type

    # ── Internals ─────────────────────────────────────────────────────────

    def _apply_verdict(self, verdict: str) -> None:
        """
        Apply hysteresis transition logic.
        Starts at 'ivr'. Requires 2 human verdicts to switch to 'human'.
        Once it is 'human', it LOCKS and never goes back to 'ivr'.
        """
        # ONE-WAY LOCK: If we are already in human mode, stay there.
        if self.current_party_type == "human":
            return

        if verdict == "human":
            self._human_streak += 1
            self._ivr_streak = 0
            # Flip to human if we see 3 consecutive human verdicts
            if self._human_streak >= 2:
                self.current_party_type = "human"
                logger.info(f"[PARTY DETECTOR] 🔒 Mode LOCKED to HUMAN at turn streak {self._human_streak}")
        else:  # ivr
            self._ivr_streak += 1
            self._human_streak = 0
            # Since it's a one-way lock, we don't need to handle human->ivr transition here.
            # We just maintain the streaks.



    @staticmethod
    def _get_recent_turns(
        history: List[Dict[str, str]],
        max_turns: int = 8,
    ) -> List[Dict[str, str]]:
        """Extract the last N user/assistant turns from conversation history."""
        relevant = [
            {"role": t["role"], "content": t["content"]}
            for t in history
            if isinstance(t, dict)
            and t.get("role") in {"user", "assistant"}
            and t.get("content")
        ]
        return relevant[-max_turns:] if len(relevant) > max_turns else relevant

    def reset(self) -> None:
        """Reset detector state (e.g. for a new call)."""
        self.current_party_type = "ivr"
        self._human_streak = 0
        self._ivr_streak = 0
