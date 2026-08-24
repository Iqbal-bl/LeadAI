from datetime import datetime
import pytz
import os
import re
import json
import logging
import pandas as pd
from typing import Any, AsyncGenerator, List, Dict, Mapping, Optional, Tuple, Union
from bot.claim_data_get import ClaimDataPrompt
from bot.synonym_manager import SynonymManager
from bot.single_prompt import prompt_core, prompt_ivr_mode, prompt_human_mode, dtmf_prompt, textual_prompt
from bot.party_detector import PartyDetectorAgent
import openai as _openai  # legacy client only
from dotenv import load_dotenv
import os
load_dotenv()

logger = logging.getLogger(__name__)


class InsuranceClaimAgent:
    def __init__(self, knowledge_base, output_manager, call_id: str | None = None):
        # ⚠️ Avoid setting module-global keys; keep a client per instance
        self.claim_data: dict = {}
        _openai.api_key = os.getenv("OPENAI_API_KEY")
        self._openai = _openai

        self.knowledge_base = knowledge_base
        self.output_manager = output_manager
        self.call_id = call_id or "unknown-call"

        self.extracted_data: List[Dict[str, str]] = []
        self.conversation_history: List[Dict[str, str]] = []  # per-call, safe
        self.system_prompt = prompt_core
        builder = ClaimDataPrompt(placeholder = os.getenv("PLACEHOLDER"))
        self.claim_prompt = builder.build_from_file(os.getenv("FILE_PATH"), sheet=os.getenv("SHEET"))

        # CSV serialization for single-process; prefer DB for multi-worker
        import asyncio, threading
        self._csv_async_lock = asyncio.Lock()
        self._csv_thread_lock = threading.Lock()

        self.last_agent_response: str = ""
        self.repetition_count: int = 0

        self._cached_claim_data_block: str | None = None
        self._last_claim_data_id: str | None = None

        # ── IVR / Human party detection (LLM-based, background) ──
        self.party_detector = PartyDetectorAgent()
        self.other_party_type: str = "ivr"   # default: IVR picks up first
    
    def _get_field_smart(self, data: dict, field_name: str) -> Optional[str]:
        """Case-insensitive and underscore-flexible lookup for claim data fields."""
        if not data:
            return None
        
        target = field_name.lower().replace("_", "").replace(" ", "")
        for k, v in data.items():
            k_clean = str(k).lower().replace("_", "").replace(" ", "")
            if k_clean == target:
                return str(v).strip() if v is not None else None
        return None

    def _format_claim_data_for_prompt(self, claim_data) -> str:
        """
        Convert claim_data dict (from BatchInfo) into a clear Key-Value block.
        """
        if not claim_data:
            return "### CLAIM DATA:\n(No data available)"

        # Support either a single dict or list[dict]
        records = []
        if isinstance(claim_data, dict):
            records = [claim_data]
        elif isinstance(claim_data, list):
            records = claim_data
        else:
            return "### CLAIM DATA:\n(No data available)"

        # Use only the first record
        data = records[0]
        lines = []

        # -- Integrate SynonymManager --
        sm = SynonymManager()
        synonyms_map = sm.get_all_synonyms()
        # -----------------------------

        for k, v in data.items():
            # normalize keys
            original_key = str(k).strip()
            
            # --- Skip internal fields for AI prompt ---
            if original_key in {"BatchId", "CsvName"}:
                continue
            # ------------------------------------------

            key_lower = original_key.lower()

            val = "" if v is None else str(v).strip()
            
            # Check for synonyms
            # We match against the original key (or lowercase) in the synonym map
            aliases = []
            if original_key in synonyms_map:
                aliases = synonyms_map[original_key]
            elif key_lower in synonyms_map:  # fallback
                aliases = synonyms_map[key_lower]
            
            # Display format: "OriginalKey / Alias / Alias2: Value"
            
            if aliases:
                # User wants "OriginalKey / Alias1 / Alias2"
                readable_alias = " / ".join(aliases)
                key_display = f"{original_key} / {readable_alias}"
            else:
                # perform standard cleanup
                key_display = original_key.replace("_", " ")

            if val:
                lines.append(f"{key_display}: {val}")
            else:
                lines.append(f"{key_display}: (missing)")

        # -- Separate Identity for Clarity --
        caller_name = self._get_field_smart(data, "caller_name") or self._get_field_smart(data, "Callers_Name") or "Andrea"
        
        # Programmatic Splitting: Split at the first space to get First and Last name
        caller_parts = caller_name.split(" ", 1)
        caller_first = caller_parts[0]
        caller_last = caller_parts[1] if len(caller_parts) > 1 else ""

        patient_f = self._get_field_smart(data, "patient_firstname") or ""
        patient_l = self._get_field_smart(data, "patient_lastname") or ""
        patient_name = f"{patient_f} {patient_l}".strip() or "(missing)"
        
        identity_lines = [
            f"YOUR IDENTITY (Full Name): {caller_name}",
            f"YOUR IDENTITY (First Name): {caller_first}",
            f"YOUR IDENTITY (Last Name): {caller_last if caller_last else '(none)'}",
            f"PATIENT IDENTITY (Subject Name): {patient_name}",
            "---"
        ]
        

        content = "### AVAILABLE CLAIM DATA (Use this to answer questions):\n"
        content += "\n".join(identity_lines) + "\n"
        content += ("\n".join(lines) if lines else "(none)")
        
        # Add intelligence instructions
        content += (
            "\n\n### DATA USAGE RULES:\n"
            "If the user provides a substring (e.g., last 4 digits, just the year, first name only) "
            "and it matches your record, ACCEPT it as valid. Ignore prefixes/suffixes (e.g., 'ZGZ').\n"
            "1. **SMART MATCHING:** If the agent asks for 'DOB' and you have 'Date of Birth', MATCH IT.\n"
            "2. **PARTIAL MATCHING:** If you have 'Johnathan' and they ask for 'John', CONFIRM it.\n"
            "3. **ID MATCHING:** Ignore dashes and spaces in IDs (Member ID, NPI, Tax ID, etc.). If the digits match your record, CONFIRM it as correct.\n"
            "4. **TRUST THIS DATA:** This is your source of truth. Valid values here are what you should speak."
        )
        # Cache the result
        self._cached_claim_data_block = content
        self._last_claim_data_id = str(id(claim_data)) if claim_data else None
        return content
    def build_system_prompt(self, base_prompt: str) -> str:
        # Return only the base prompt for now; time rules will be appended at the end of the system message sequence
        return base_prompt.strip()

    def build_time_aware_directive(self) -> str:
        """Create a separate system directive for time/date to allow prefix caching of the main prompt."""
        payer_timezone = pytz.timezone('US/Eastern')
        now = datetime.now(pytz.utc).astimezone(payer_timezone)
        formatted_now = now.strftime("%B %d, %Y at %I:%M %p")

        hour = now.hour
        if 5 <= hour < 12:
            time_greeting = "Good morning"
        elif 12 <= hour < 17:
            time_greeting = "Good afternoon"
        else:
            time_greeting = "Good evening"

        return f"""
            ## CURRENT PAYER TIME (US/Eastern)
            Current Date/Time: {formatted_now}
            Time-of-day greeting word (use ONLY on your very first turn if speaking to a human for the first time): {time_greeting}
            WARNING: Do NOT use this greeting word again after the first introduction. This block appears every turn for date reference only — it is NOT a trigger to greet again.
            """.strip()


    # def save_conversation_turn(self, user_input: str, ai_response: str):
    #     self.conversation_history.append({"user": user_input, "assistant": ai_response})
    def save_conversation_turn(self, user_input: str, ai_response: str):
        """Save conversation turn in the correct format for extraction."""
        if user_input.startswith("[SYSTEM EVENT:"):
            self.conversation_history.append({"role": "system", "content": user_input})
        else:
            self.conversation_history.append({"role": "user", "content": user_input})
        self.conversation_history.append({"role": "assistant", "content": ai_response})
        # logger.info(f"[CONVERSATION] Saved turn: USER: {user_input[:80]}... | AGENT: {ai_response[:80]}...")

    def rollback_last_turn(self):
        """
        Removes the last turn (User + Assistant) from conversation history.
        Used for barge-in where the AI response was based on incomplete context.
        """
        if not self.conversation_history:
            return

        logger.info(f"[{self.call_id}] ⏪ Rolling back last turn due to interruption.")
        
        # 1. Pop the Assistant message if it exists at the end
        if self.conversation_history and self.conversation_history[-1]["role"] == "assistant":
            self.conversation_history.pop()
            
        # 2. Pop the corresponding User/System message
        if self.conversation_history and self.conversation_history[-1]["role"] in ["user", "system"]:
            self.conversation_history.pop()
            
        # Reset local repetition trackers to avoid false loop detection
        self.repetition_count = max(0, self.repetition_count - 1)
        if hasattr(self, "user_repetition_count"):
            self.user_repetition_count = max(0, self.user_repetition_count - 1)


    def create_messages_with_smart_history(
        self,
        user_input: str,
        claim_data: Optional[Any] = None,
        mode_prompt: str = "",
        rag_chunks: str = "",
        max_messages: int = 100,
    ):
        def _trim(s: Optional[str], max_chars: int = 8000) -> str:
            if not s:
                return ""
            return s if len(s) <= max_chars else s[:max_chars] + " …"

        messages: list[dict] = []

        # =====================================================================
        # STEP 1: STATIC PREFIX (PERMANENT CACHE)
        # =====================================================================
        
        # 1) Base System Prompt
        messages.append({"role": "system", "content": self.build_system_prompt(self.system_prompt)})
        
        # 2) Mode-specific rules — with explicit banner so AI always knows current mode
        if self.other_party_type == "ivr":
            messages.append({"role": "system", "content": (
                "⚠️ CURRENT MODE: IVR\n"
                "You are speaking to an AUTOMATED SYSTEM — NOT a human.\n"
                "ABSOLUTE SILENCE RULE — output [SILENT] for ALL of the following, no exceptions:\n"
                "  • IVR reads out any information (claim status, amounts, dates, account details, etc.)\n"
                "  • IVR presents menu options ('say X', 'press Y', 'say hang up', 'say another claim', etc.) — only respond if one option exactly matches your intent, otherwise [SILENT]\n"
                "  • IVR says 'Ok', 'Sure', 'Alright', 'Bye', 'Ok bye', 'Goodbye', or any farewell\n"
                "  • IVR says 'Thank you', 'Have a good day', or any closing phrase\n"
                "  • IVR is on hold, transferring, or playing recordings/music\n"
                "ONLY OUTPUT A VALUE when: IVR explicitly asks YOU to provide a specific piece of information (NPI, member ID, DOB, etc.).\n"
                "FORBIDDEN IN IVR MODE: 'I'm checking the status', 'I'm calling about', 'Good morning', asking for name/reference, any sentence — these are human conversation only.\n"
                "NEVER acknowledge with any word. NEVER ask questions. ONLY [SILENT] or the raw requested value.\n"
                "Stay in IVR mode until a clearly live human voice is detected."
            )})
            messages.append({"role": "system", "content": prompt_ivr_mode})
            if mode_prompt:
                messages.append({"role": "system", "content": mode_prompt})
        else:
            messages.append({"role": "system", "content": (
                "✅ CURRENT MODE: HUMAN\n"
                "You are now speaking to a LIVE HUMAN REPRESENTATIVE.\n"
                "Use natural, professional conversation. Greet ONLY on your very first human turn (if not greeted yet).\n"
                "Do NOT greet again after the first introduction.\n"
                "NATURAL BEHAVIOR: Do NOT start every reply with 'Thank you' or 'Thank you so much'. "
                "Do NOT be over-polite. Just respond directly to what was said, like a real caller would. "
                "Move to your next topic naturally without excessive pleasantries.\n"
                "CONFIRMATION RECOGNITION: If the representative reads back a value (email, ID, date, name, fax, etc.) "
                "and asks 'Is that correct?' or 'Does that sound right?' or repeats it for confirmation — "
                "compare it against your claim data (ignore symbols like hyphens, dots, spaces, dashes — match digits/letters only). "
                "If it matches → respond: 'Yes, that's correct.' "
                "If it does NOT match → say 'No' and give the correct value. "
                "NEVER respond with 'I did not get that' to a confirmation from the rep."
            )})
            messages.append({"role": "system", "content": prompt_human_mode})

        # 3) Data Usage Rules (Static)
        messages.append({
            "role": "system",
            "content": (
                "### DATA USAGE RULES:\n"
                "- If sub-string matches (last 4, year-only, etc.), ACCEPT as valid.\n"
                "- SMART MATCHING: Case-insensitive, ignore spaces/underscores/hyphens.\n"
                "- TRUST DATA: This is your source of truth. Valid values are what you should speak.\n"
                "- REPETITION: Only use 'As I mentioned...' if the exact text appears in AGENT turns below.\n"
                "- DOB FORMAT (VOICE): When speaking a date of birth to a human or IVR in voice mode, ALWAYS say it as "
                "'Month Day, Year' (e.g., '10/05/1955' → 'October fifth, nineteen fifty-five'). "
                "NEVER read a date digit-by-digit (e.g., 'one-oh-five...' is WRONG).\n"
                "- DOB FORMAT (DTMF): When entering DOB via keypad, use MMDDYYYY format (e.g., '10051955').\n"
                "- STRICT DATE MATCHING (DOB, DOS) — ABSOLUTE RULE:\n"
                "  1. Convert BOTH sides to MM/DD/YYYY before comparing. NEVER use digit-count matching for dates.\n"
                "  2. Leading zeros do NOT matter: '9' = '09' (both mean September). NEVER say 'No' just because digit counts differ.\n"
                "     Example: you sent '9282004' and IVR confirmed '09/28/2004' — SAME date → say 'Yes'.\n"
                "  3. Verify MONTH, DAY, and YEAR individually after normalization.\n"
                "  4. If the Year is different (e.g. 2000 vs 2004), you MUST say 'No'.\n"
                "  5. Map spoken ordinals before comparing:\n"
                "     first=1, second=2, third=3, fourth=4, fifth=5, sixth=6, seventh=7, eighth=8, ninth=9, tenth=10,\n"
                "     eleventh=11, twelfth=12, thirteenth=13, fourteenth=14, fifteenth=15, sixteenth=16,\n"
                "     seventeenth=17, eighteenth=18, nineteenth=19, twentieth=20,\n"
                "     twenty-first/20 first=21, twenty-second/20 second=22, twenty-third/20 third=23,\n"
                "     twenty-fourth/20 fourth=24, twenty-fifth/20 fifth=25, twenty-sixth/20 sixth=26,\n"
                "     twenty-seventh/20 seventh=27, twenty-eighth/20 eighth=28, twenty-ninth/20 ninth=29,\n"
                "     thirtieth=30, thirty-first/30 first=31.\n"
                "  6. Example: IVR says 'September 20 eighth 2004' → Sep 28 2004 → 09/28/2004. "
                "     If your data is '09/28/2004' or '9/28/2004' or '9282004' → ALL MATCH → say 'Yes'.\n"
                "- VALUE CONFIRMATION MATCHING: When the other party repeats back a value you gave "
                "(ID, date, fax, email, etc.) — strip ALL symbols (hyphens, dots, spaces, slashes, dashes) "
                "from both values and compare only the raw alphanumeric characters. "
                "If they match → confirm 'Yes'. Example: you said '12345', they say '1-2-3 4-5' → MATCH → 'Yes'.\n"
                "- FAX NUMBERS: Provider_Fax is your general provider fax. Appeals_Fax_Number is for appeals only. "
                "Provide the exact field value — NEVER substitute one fax field for another."
            )
        })

        # =====================================================================
        # STEP 2: STABLE CALL DATA (CALL CACHE)
        # =====================================================================

        # 4) Claim Data Context
        if not claim_data:
            claim_data_block = f"Claim Data:\n{self.claim_prompt}"
        else:
            if self._cached_claim_data_block and self._last_claim_data_id == str(id(claim_data)):
                claim_data_block = self._cached_claim_data_block
            else:
                claim_data_block = self._format_claim_data_for_prompt(claim_data)
        
        messages.append({"role": "system", "content": f"Here is the context for this call:\n\n{claim_data_block}"})

        # 5) Knowledge Base (RAG)
        if rag_chunks:
            messages.append({"role": "system", "content": "Knowledge Base:\n" + _trim(rag_chunks, 4000)})

        # =====================================================================
        # STEP 3: SEMI-DYNAMIC HISTORY (KV-CACHE STABLE)
        # =====================================================================

        messages.append({"role": "system", "content": "=== TRANSCRIPT START ==="})
        
        hist = []
        if getattr(self, "conversation_history", None):
            hist = [
                {"role": t["role"], "content": t["content"]}
                for t in self.conversation_history
                if isinstance(t, dict)
                and t.get("role") in {"user","assistant"}
                and t.get("content")
            ]
            # Budgeted history window (keeps prefix prefixable)
            if len(hist) > 80: hist = hist[-80:]
            messages.extend(hist)

        # =====================================================================
        # STEP 4: FULLY DYNAMIC FOOTER (CACHE BUSTERS)
        # =====================================================================

        # 6) Current Time (Changes every minute - MUST be near end)
        messages.append({"role": "system", "content": self.build_time_aware_directive()})

        # 7) Logical Guards (Turn-specific alerts)
        
        # End-of-call status check
        conversation_ended = False
        if self.conversation_history:
            closing_phrases = {"goodbye", "good bye", "bye bye", "bye",
            "thank you goodbye", "thanks goodbye", "have a nice day", "have a good day",
            "take care", "talk to you later", "see you later", "call you later"}
            last_agent_text = [m["content"].lower() for m in self.conversation_history[-8:] if m.get("role")=="assistant"]
            conversation_ended = any(any(p in txt for p in closing_phrases) for txt in last_agent_text[-3:])

        if conversation_ended:
            messages.append({"role": "system", "content": "CALL OVER: Respond with silence [SILENT] or brief goodbye if they spoke."})

        # Intent detection for current input
        t_lower = user_input.lower()
        is_goodbye = any(p in t_lower for p in ["goodbye", "good bye", "bye bye", "bye",
            "thank you goodbye", "thanks goodbye", "have a nice day", "have a good day",
            "take care", "talk to you later", "see you later", "call you later"])
        transfer_hints = ["transfer", "hold", "connecting", "stay on line"]
        has_transfer = any(p in t_lower for p in transfer_hints)
        
        if is_goodbye and not has_transfer:
            if self.other_party_type == "ivr":
                messages.append({"role": "system", "content": (
                    "IVR said a farewell ('bye', 'ok bye', 'goodbye', etc.). "
                    "Output EXACTLY [SILENT]. Do NOT ask for name. Do NOT ask for reference. "
                    "Do NOT say goodbye back. Stay silent."
                )})
            else:
                messages.append({"role": "system", "content": "Politely finish and end call."})
        elif has_transfer:
            if self.other_party_type == "ivr":
                messages.append({"role": "system", "content": "Transfer/Hold detected in IVR. Stay ABSOLUTELY SILENT. Output [SILENT]."})
            else:
                messages.append({"role": "system", "content": "Transfer/Hold detected. Acknowledge and wait."})

        # Confirmation matching: pre-compute symbol-stripped comparison so the AI cannot get it wrong
        _conf_patterns = ["is it correct", "is that correct", "is that right", "correct?", "right?", "confirm"]
        if any(p in t_lower for p in _conf_patterns) and self.last_agent_response:
            def _strip_sym(s: str) -> str:
                return re.sub(r'[^A-Za-z0-9]', '', s).upper()
            last_val = _strip_sym(self.last_agent_response)
            input_val = _strip_sym(user_input)
            match = bool(last_val) and (last_val in input_val or input_val in last_val)
            messages.append({"role": "system", "content": (
                f"CONFIRMATION CHECK (pre-computed — trust this result, do not re-evaluate):\n"
                f"  Your last value: '{self.last_agent_response}' → symbols stripped: '{last_val}'\n"
                f"  Other party said: '{user_input}' → symbols stripped: '{input_val}'\n"
                f"  Result: {'MATCH → output exactly: Yes' if match else 'NO MATCH → output: No, and state the correct value'}\n"
                "Hyphens, spaces, slashes, dots are all ignored. Only the digit/letter sequence matters."
            )})

        # DTMF reminder
        from bot.single_prompt import dtmf_prompt
        if mode_prompt == dtmf_prompt:
            messages.append({"role": "system", "content": (
                "IVR asked for DTMF keypad input. Output ONLY raw digits (0-9), *, or #.\n"
                "KEYPAD LETTER→DIGIT RULE: If the subscriber ID, member ID, or any value contains letters "
                "(e.g., 'SL123', 'AWB456'), convert every letter to its phone keypad digit before outputting:\n"
                "  A/B/C=2  D/E/F=3  G/H/I=4  J/K/L=5  M/N/O=6  P/Q/R/S=7  T/U/V=8  W/X/Y/Z=9\n"
                "Example: 'SL123' → S=7, L=5 → output '75123'. 'AWB456' → A=2, W=9, B=2 → output '292456'.\n"
                "This conversion is applied automatically — output only the resulting digit string."
            )})

        # 8a) First-turn IVR override — suppress greeting before the very first IVR message
        if self.other_party_type == "ivr" and not hist:
            messages.append({"role": "system", "content": (
                "FIRST TURN OVERRIDE — IVR mode, no history yet (may be due to barge-in rollbacks).\n"
                "DO NOT say 'Good morning', 'Good afternoon', or any greeting.\n"
                "DO NOT introduce yourself ('I'm calling from...', 'This is...', etc.).\n"
                "Apply the CORE DECISION RULE in this exact priority order:\n"
                "  1. IVR presents MENU OPTIONS ('press 1 for X, press 2 for Y', 'for provider support press 5', etc.)\n"
                "     → Output ONLY the single digit matching Provider / Claims / Healthcare. Nothing else.\n"
                "     → ALWAYS press the provider/claims option even if the menu also says 'thank you, have a wonderful day'.\n"
                "  2. IVR asks YOU to provide data (NPI, member ID, DOB, tax ID, etc.) → provide the raw value.\n"
                "  3. IVR asks for your reason/motive ('state your reason', 'what are you calling about') → say ONLY: 'Claim status'.\n"
                "  4. IVR is giving intro/info/recording notice with NO menu and NO question → output [SILENT].\n"
                "MENU TAKES PRIORITY: If ANY 'press X' option exists in the message, output the correct digit immediately."
            )})

        # 8) The Actual Turn
        if user_input.startswith("[SYSTEM EVENT:"):
            messages.append({"role": "system", "content": user_input})
        else:
            messages.append({"role": "user", "content": user_input})

        return messages


    @staticmethod
    def _letters_to_t9(text: str) -> str:
        """Convert letters in a string to their phone keypad (T9) digit equivalents.
        Digits, *, # are passed through unchanged. Spaces and punctuation are removed."""
        t9_map = {
            'A': '2', 'B': '2', 'C': '2', 'a': '2', 'b': '2', 'c': '2',
            'D': '3', 'E': '3', 'F': '3', 'd': '3', 'e': '3', 'f': '3',
            'G': '4', 'H': '4', 'I': '4', 'g': '4', 'h': '4', 'i': '4',
            'J': '5', 'K': '5', 'L': '5', 'j': '5', 'k': '5', 'l': '5',
            'M': '6', 'N': '6', 'O': '6', 'm': '6', 'n': '6', 'o': '6',
            'P': '7', 'Q': '7', 'R': '7', 'S': '7', 'p': '7', 'q': '7', 'r': '7', 's': '7',
            'T': '8', 'U': '8', 'V': '8', 't': '8', 'u': '8', 'v': '8',
            'W': '9', 'X': '9', 'Y': '9', 'Z': '9', 'w': '9', 'x': '9', 'y': '9', 'z': '9',
        }
        result = []
        for ch in text:
            if ch in t9_map:
                result.append(t9_map[ch])
            elif ch in '0123456789*#':
                result.append(ch)
            # else: strip spaces and punctuation
        return ''.join(result)

    @staticmethod
    def is_positive_dtmf_prompt(text: str) -> bool:
        text = (text or "").lower()
        dtmf_keywords = [
            "press", "enter", "marque", "oprima", "pulse", "appuyez",
            "dial", "input", "digits",
            "key in", "type"
        ]
        negations = [
            "do not", "don't", "not",
            "no oprima", "no presione", "no marque", "no ingrese", "no",
            "n'appuyez pas", "ne composez pas", "ne saisissez pas", "pas",
            "ना दबाएँ", "मत दबाएँ",
        ]
        clauses = re.split(r"[.;,?]| or ", text)
        for clause in clauses:
            if any(kw in clause for kw in dtmf_keywords):
                if any(neg in clause for neg in negations):
                    continue
                return True
        return False
    
    @staticmethod
    def is_voicemail(text: str) -> bool:
        """Detect if the transcript is a voicemail/answering machine."""
        voicemail_phrases = [
            "leave a message", "leave your name", "after the beep",
            "your message", "mailbox is full", "not available, please leave",
            "leave a brief message", "record your message", "please leave a message",
            "you have reached the voicemail", "you've reached the voicemail",
            "voicemail box"
        ]
        t = (text or "").lower()
        return any(phrase in t for phrase in voicemail_phrases)

    def _normalize_text(self, text: str) -> str:
        """Normalize text for loop detection comparison."""
        return re.sub(r'\W+', '', text.lower())

    # ── Party Detection (IVR / Human) ─────────────────────────────────────

    async def _run_party_detection_background(self) -> None:
        """
        Background task: classify the other party as IVR or human.

        RULES:
        1. Force IVR for first 6 turns (approx 12 messages) to handle menus.
        2. After that, fire the LLM detector on every turn.
        3. Only update self.other_party_type when the result DIFFERS.
        """
        # --- Forced IVR for first 6 turns (12 messages) ---
        # Each turn usually has 1 User and 1 Assistant message.
        if len(self.conversation_history) < 8:
            if self.other_party_type != "ivr":
                logger.info(f"[{self.call_id}] Forcing IVR mode for initial turns ({len(self.conversation_history)} msgs)")
                self.other_party_type = "ivr"
                self.party_detector.reset() # Reset detector streaks
            return

        try:
            result = await self.party_detector.detect(self.conversation_history)
            if result != self.other_party_type:
                prev = self.other_party_type
                self.other_party_type = result
                logger.info(
                    f"[{self.call_id}] [PARTY SWITCH] {prev} → {result} "
                    f"(will take effect on next turn)"
                )
        except Exception as e:
            logger.warning(f"[{self.call_id}] Party detection failed (non-fatal): {e}")
    # ─────────────────────────────────────────────────────────────────────

    async def query_stream(
        self,
        input_text: str,
        claim_data: Optional[Any] = None,
    ) -> AsyncGenerator[Dict[str, str], None]:

        # --- Voicemail Early Exit (ICSC-287) ---
        if self.is_voicemail(input_text):
            logger.warning(f"[{self.call_id}] Voicemail detected. Ending call immediately.")
            yield {"type": "tts", "text": "Thank you, I will call back later. Goodbye."}
            yield {"type": "end_call", "reason": "voicemail detected in call"}
            return
        # -----------------------------------------

        # --- User Loop Detection ---
        if not hasattr(self, 'recent_user_responses'):
            self.recent_user_responses = []
            self.user_repetition_count = 0
            self.last_user_response = ""

        if input_text and not input_text.startswith("[SYSTEM EVENT:"):
            norm_user_current = self._normalize_text(input_text)
            if norm_user_current:
                self.recent_user_responses.append(norm_user_current)
                if len(self.recent_user_responses) > 10:
                    self.recent_user_responses.pop(0)

                norm_user_last = self._normalize_text(self.last_user_response)
                
                if norm_user_current == norm_user_last:
                    self.user_repetition_count += 1
                    logger.warning(f"[{self.call_id}] User/IVR repetition detected! Count: {self.user_repetition_count}")
                else:
                    self.user_repetition_count = 0
                    self.last_user_response = input_text
                
                user_occurrence_count = self.recent_user_responses.count(norm_user_current)
                
                if self.user_repetition_count >= 3 or user_occurrence_count >= 4:
                    logger.error(f"[{self.call_id}] User/IVR looping detected (4x).")
                    if self.other_party_type == "ivr":
                        # IVR mode: never cut the call — reset counters and continue
                        self.user_repetition_count = 0
                        self.recent_user_responses = []
                        logger.info(f"[{self.call_id}] IVR mode — loop counters reset, staying on call.")
                    else:
                        yield {"type": "tts", "text": "I am sorry, we're unable to move forward with your request at this time. Please call back later."}
                        yield {"type": "end_call", "reason": "user_loop_detected"}
                        return
        # -----------------------------

        rag_chunks = None
        # DTMF/textual sub-prompts are only relevant in IVR mode
        if self.other_party_type == "ivr":
            is_dtmf_prompt = self.is_positive_dtmf_prompt(input_text)
            mode_prompt = dtmf_prompt if is_dtmf_prompt else textual_prompt
        else:
            is_dtmf_prompt = False
            mode_prompt = ""  # Human mode — no DTMF rules
        messages = self.create_messages_with_smart_history(input_text, claim_data, mode_prompt, rag_chunks)

        if self.repetition_count >= 3 and not is_dtmf_prompt:
            logger.warning(f"[{self.call_id}] Loop warning: repetition count {self.repetition_count}. Injecting human-agent loop breaker.")
            messages.append({
                "role": "system", 
                "content": (
                    "WARNING: You have given a very similar response 3+ times. The conversation may be stuck. "
                    "FIRST: Answer the user's CURRENT question directly — read what they just said and respond to THAT. "
                    "SECOND: If you truly cannot answer their current question, try a different approach: "
                    "offer alternative information you DO possess (e.g., Date of Birth, Provider Name). "
                    "NEVER ignore what the user just asked. Their latest message is the priority."
                )
            })

        try:
            completion = await self._openai.ChatCompletion.acreate(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.1 if is_dtmf_prompt else 0.5,
                max_tokens=250,
                stream=True
            )

            buffer = ""
            assistant_reply_full = ""
            type_decided = False
            got_json = False

            async for chunk in completion:
                delta = chunk['choices'][0]['delta'].get('content', '')
                if not delta:
                    continue

                buffer += delta
                assistant_reply_full += delta  # Always collect full response

                # === JSON + DTMF handling (unchanged) ===
                if not type_decided and buffer.strip().startswith('{') and buffer.strip().endswith('}'):
                    try:
                        payload = json.loads(buffer.strip())
                        if isinstance(payload, dict) and "dtmf" in payload:
                            reply_text = payload.get("reply", "")
                            dtmf_digit = payload.get("dtmf")
                            got_json = True
                            type_decided = True

                            to_save = reply_text or str(dtmf_digit)
                            clean_to_save = re.sub(r'\[.*?\]', '', to_save).strip()
                            if clean_to_save:
                                self.save_conversation_turn(input_text, clean_to_save)
                            else:
                                if input_text:
                                    if input_text.startswith("[SYSTEM EVENT:"):
                                        self.conversation_history.append({"role": "system", "content": input_text})
                                    else:
                                        self.conversation_history.append({"role": "user", "content": input_text})

                            # Stream reply in sentences
                            temp = ""
                            for ch in reply_text:
                                temp += ch
                                if ch in ".?!":
                                    clean_t = re.sub(r'\[.*?\]', '', temp)
                                    clean_t = re.sub(r'[\[\]]', '', clean_t).strip()
                                    if clean_t:
                                        yield {"type": "tts", "text": clean_t}
                                    temp = ""
                            clean_t = re.sub(r'\[.*?\]', '', temp)
                            clean_t = re.sub(r'[\[\]]', '', clean_t).strip()
                            if clean_t:
                                yield {"type": "tts", "text": clean_t}
                            if dtmf_digit:
                                yield {"type": "dtmf", "text": dtmf_digit}
                            return
                    except:
                        pass

                # Decide it's normal text — skip entirely in DTMF mode so the
                # full response is collected and routed as DTMF at end-of-stream
                if not type_decided and not is_dtmf_prompt:
                    if re.fullmatch(r'[0-9*#]{1,20}', buffer.strip()):
                        pass
                    elif any(p in buffer for p in ".!?"):
                        type_decided = True
                    elif len(buffer.split()) >= 4 and not buffer.strip().startswith('{'):
                        type_decided = True

                # Stream normal text in sentences
                if type_decided and not got_json:
                    while True:
                        # Improved REGEX: Split on end-of-sentence punctuation followed by space or end of string.
                        # Do NOT split on commas/colons, as that breaks prosody and causes large mid-sentence TTS pauses.
                        m = re.search(r'(.+?[.?!](?:\s|$))', buffer)
                        if m:
                            sent = m.group(1).strip()
                            clean_s = re.sub(r'\[.*?\]', '', sent)
                            clean_s = re.sub(r'[\[\]]', '', clean_s).strip()
                            if clean_s:
                                yield {"type": "tts", "text": clean_s}
                            buffer = buffer[m.end():]
                            continue
                        
                        # Fallback for strings without punctuation (like spelled-out IDs) or long fragments
                        words = buffer.split()
                        if (len(words) >= 7 and buffer.endswith(' ')) and not buffer.strip().startswith('{'):
                            sent = buffer.strip()
                            clean_s = re.sub(r'\[.*?\]', '', sent)
                            clean_s = re.sub(r'[\[\]]', '', clean_s).strip()
                            if clean_s:
                                yield {"type": "tts", "text": clean_s}
                            buffer = ""
                        break

            # =================================================================
            # END OF STREAM — NOW IT'S SAFE TO SAVE + SIGNAL END OF CALL
            # =================================================================

            final_response = assistant_reply_full.strip()

            # === CASE 1: DTMF mode ===
            if is_dtmf_prompt and not type_decided:
                no_brackets = re.sub(r'\[.*?\]', '', final_response).strip()
                is_sentence = no_brackets.count(' ') > 4  # >4 spaces = full sentence

                if is_sentence:
                    # AI produced a sentence when it should have been silent or sent digits.
                    # Send as TTS so it doesn't get mangled into garbage DTMF.
                    if no_brackets:
                        self.save_conversation_turn(input_text, no_brackets)
                        yield {"type": "tts", "text": no_brackets}
                    else:
                        if input_text:
                            if input_text.startswith("[SYSTEM EVENT:"):
                                self.conversation_history.append({"role": "system", "content": input_text})
                            else:
                                self.conversation_history.append({"role": "user", "content": input_text})
                else:
                    # Short value — strip spaces and non-keypad chars, send as DTMF
                    clean_dtmf = re.sub(r'[^A-Za-z0-9*#]', '', no_brackets)
                    if clean_dtmf:
                        self.save_conversation_turn(input_text, clean_dtmf)
                        dtmf_value = self._letters_to_t9(clean_dtmf)
                        yield {"type": "dtmf", "text": dtmf_value}
                    else:
                        # [SILENT] or truly empty — save user turn only
                        if input_text:
                            if input_text.startswith("[SYSTEM EVENT:"):
                                self.conversation_history.append({"role": "system", "content": input_text})
                            else:
                                self.conversation_history.append({"role": "user", "content": input_text})

            # === CASE 2: Pure digit response in non-DTMF IVR mode → always DTMF ===
            # A single digit in IVR mode is always a menu selection, never a voice response
            elif not type_decided and re.fullmatch(r'[0-9*#]{1,30}', final_response):
                self.save_conversation_turn(input_text, final_response)
                if self.other_party_type == "ivr":
                    yield {"type": "dtmf", "text": final_response}
                else:
                    spoken_target = " ".join(list(final_response))
                    yield {"type": "tts", "text": spoken_target}

            # === CASE 3: Normal text response ===
            elif final_response:
                clean_final = re.sub(r'\[.*?\]', '', final_response).strip()
                # SAVE FULL RESPONSE — ONLY ONCE, AT THE END
                if clean_final:
                    self.save_conversation_turn(input_text, clean_final)
                else:
                    if input_text:
                        if input_text.startswith("[SYSTEM EVENT:"):
                            self.conversation_history.append({"role": "system", "content": input_text})
                        else:
                            self.conversation_history.append({"role": "user", "content": input_text})

                # Send any remaining text not ending in punctuation
                remaining = buffer.strip()
                clean_rem = re.sub(r'\[.*?\]', '', remaining).strip()
                if clean_rem:
                    yield {"type": "tts", "text": clean_rem}

                # SIGNAL: This response ends the call
                goodbye_phrases =[
                    "have a great day", "goodbye", "good bye", "take care", "bye", 
                    "have a nice day", "have a good day", "thank you for your time",
                    "talk to you later", "see you later", "until next time"
                ]
                if any(phrase in final_response.lower() for phrase in goodbye_phrases):
                    # ICSC-192: Check for transfer context before ending call
                    # If CS agent mentioned transfer/connecting phrases recently,
                    # do NOT end the call — stay on line for the transfer
                    transfer_phrases = [
                        "transfer", "transferring", "connecting you", "connect you",
                        "hold while", "putting you through", "put you through",
                        "have on the line", "have someone on the line", "another representative",
                        "i'll connect", "let me connect", "stay on the line",
                        "placing you", "hold on", "pass you", "get someone", 
                        "find someone", "get you over", "one moment"
                    ]
                    # Check current user input AND recent history for transfer context
                    input_lower = input_text.lower()
                    has_transfer_in_input = any(p in input_lower for p in transfer_phrases)
                    
                    has_transfer_in_history = False
                    if self.conversation_history:
                        # Check last 6 messages (3 turns) for transfer context
                        recent_msgs = self.conversation_history[-6:]
                        for msg in recent_msgs:
                            if isinstance(msg, dict) and msg.get("content"):
                                msg_lower = msg["content"].lower()
                                if any(p in msg_lower for p in transfer_phrases):
                                    has_transfer_in_history = True
                                    break
                    
                    if self.other_party_type == "ivr":
                        logger.info(f"[{self.call_id}] IVR mode — suppressing end_call from goodbye phrase in AI response")
                    elif has_transfer_in_input or has_transfer_in_history:
                        logger.info(f"[{self.call_id}] TRANSFER CONTEXT DETECTED — suppressing end_call despite goodbye phrase in AI response")
                    else:
                        yield {"type": "end_call", "reason": "natural_goodbye"}

            logger.info(f"[{self.call_id}] FULL AI RESPONSE: '{final_response}'")

            # --- Party Detection (background, non-blocking) ---
            # Fire the LLM detector as a background task so it doesn't
            # add any latency to the current response.  The result will
            # update self.other_party_type for the NEXT turn.
            import asyncio
            asyncio.create_task(self._run_party_detection_background())
            # --------------------------------------------------

            # --- Loop Detection Update ---
            is_system_nudge = input_text and input_text.startswith("[SYSTEM EVENT:")
            if final_response and not is_system_nudge:
                norm_current = self._normalize_text(final_response)
                norm_last = self._normalize_text(self.last_agent_response)
                
                # Sequential repetition check
                if norm_current and norm_current == norm_last:
                    self.repetition_count += 1
                    logger.warning(f"[{self.call_id}] Continuous repetition detected! Count: {self.repetition_count}")
                else:
                    self.repetition_count = 0
                    self.last_agent_response = final_response

                # Loop detected — never cut the call, reset counters and let AI adapt
                if self.repetition_count >= 6:
                    if self.other_party_type == "ivr":
                        # IVR mode: reset loop counter, stay on call
                        self.repetition_count = 0
                        self.last_agent_response = ""
                        logger.info(f"[{self.call_id}] IVR mode — agent loop counter reset, not ending call.")
                    else:
                        logger.error(f"[{self.call_id}] Continuous loop detected (6x). Ending call.")
                        yield {"type": "tts", "text": "I am sorry, we're unable to move forward with your request at this time. Please call back later."}
                        yield {"type": "end_call", "reason": "loop_detected"}
            # -----------------------------

        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            yield {"type": "error", "text": "Sorry, I couldn't process that."}


    def _history_as_lines(self) -> list[str]:
        """Return the whole history as normalized USER/AGENT lines (no truncation)."""
        lines = []
        for t in self.conversation_history:
            if not isinstance(t, dict):
                continue
            # Prefer structured role/content if present
            if "role" in t and "content" in t and t["content"]:
                if t["role"] == "user":
                    lines.append(f"USER: {t['content']}")
                elif t["role"] == "assistant":
                    lines.append(f"AGENT: {t['content']}")
            else:
                if t.get("user"):
                    lines.append(f"USER: {t['user']}")
                if t.get("assistant"):
                    lines.append(f"AGENT: {t['assistant']}")
        # print(f"--- Transcript has {len(lines)} lines ---{lines}")
        return lines

    def _iter_history_chunks(self, max_chars: int = 30000):
        """
        Yield transcript chunks <= max_chars, preserving turn order and boundaries.
        Ensures we cover the *entire* history by splitting it up.
        """
        lines = self._history_as_lines()
        if not lines:
            yield ""
            return

        buf = []
        size = 0
        for ln in lines:
            ln2 = ln.replace("\r", "")
            ln_len = len(ln2) + 1  # newline
            if size and size + ln_len > max_chars:
                yield "\n".join(buf)
                buf = [ln2]
                size = ln_len
            else:
                buf.append(ln2)
                size += ln_len
        if buf:
            yield "\n".join(buf)

    def _build_extractor_system_prompt(self) -> str:
        return (
            "You are a strict JSON extractor. "
            "Given a phone-call transcript (USER/AGENT turns), extract all requested fields "
            "for an insurance claim follow-up. If a field is not explicitly present, output null. "
            "Never invent values. Prefer the most recent/confirmed values mentioned by AGENT or USER. "
            "Output a single JSON object only—no extra text."
        )

    # Fields to exclude from extraction (handled by database layer)
    _AUDIT_FIELDS = [
        "Id", "CreatedBy", "CreatedAt", "UpdatedBy", "UpdatedAt", "IsDeleted"
    ]

    # Data fields only (extraction fields)
    _DATA_FIELDS = [
        "BatchInfoId","BatchExecutionId","CallNumberExecutionId","CsvName", "Group_ID",
        "Callers_Name","Callers_Intent","Company_Name","NPI","Tax_ID","CLIA_Number",
        "Callback_Number","Payer_ID","Provider_Address","Patient_FirstName","Patient_LastName",
        "Subscriber_ID","Payer_Phone_Number","Payer_Name","Claims_Address","Subscriber_FirstName",
        "Subscriber_LastName","Relationship","DOB","DOS","Charge","Insurance_Payment",
        "Submission_Method","Appeal_Submission_Date","Claim_Appeal","Provider_Fax","Provider_Email",
        "Unresponded_Claim","Unresponded_Appeal","Prefered_Payer_ID","Prefered_Claims_Address",
        "Claims_TFL","Processing_Delays","Eligible","CS_Agent_Name","Call_Ref",
        "Department_Phone_Number","Status","Appeals_Address","Received_date","Processing_Period",
        "Other_information","Processed_Date","Denial_reason","Claim_Number","Appealable",
        "Appeals_TFL","Appeals_Submission_Method","Online_Portal","Appeals_Fax_Number",
        "Corrected_claims","Paid_date","PaymentAmount","Payment_Method","Paid_To","Check_Cashed",
        "Pay_to_address","Check_Number","Bank_Account","Deposit_Date","Appeal_Ticket",
        "PR_Amount","PR_Type","Bulk_Payment"
    ]

    def set_call_context_ids(
        self,
        batch_info_id: int | None,
        batch_execution_id: int | None,
        call_number_execution_id: int | None,
        csv_name: str | None = None,
    ):
        self._batch_info_id = batch_info_id
        self._batch_execution_id = batch_execution_id
        self._call_number_execution_id = call_number_execution_id
        self._csv_name = csv_name

    def _blank_output_record(self) -> dict:
        # Start with data fields only (audit fields will be added by database layer)
        base = {k: None for k in self._DATA_FIELDS}
        # Set context fields that are part of data extraction
        base["BatchInfoId"] = getattr(self, "_batch_info_id", None)
        base["BatchExecutionId"] = getattr(self, "_batch_execution_id", None)
        base["CallNumberExecutionId"] = getattr(self, "_call_number_execution_id", None)
        base["CsvName"] = getattr(self, "_csv_name", None)
        return base

    def _merge_records_latest_wins(self, acc: dict, update: dict) -> dict:
        """
        Merge 'update' into 'acc' per data schema; non-null values in 'update' overwrite acc.
        Returns the mutated accumulator for convenience.
        """
        for k in self._DATA_FIELDS:
            if k in update and update[k] is not None:
                acc[k] = update[k]
        return acc


    def _build_extractor_user_prompt_for_chunk(self, transcript_chunk: str) -> str:
        # Get current date/time for relative date resolution
        from datetime import datetime
        current_date = datetime.now().strftime("%m/%d/%Y")
        current_time = datetime.now().strftime("%I:%M %p")
        
        # Field extraction guidance
        field_guidance = f"""
            TODAY'S DATE: {current_date}
            CURRENT TIME: {current_time}
            (Use this when resolving relative dates like "today", "current date", "this call date")

            CONVERSATION STRUCTURE - IMPORTANT:
            - USER: lines = Insurance company agent providing information (THIS IS WHERE DATA COMES FROM)
            - AGENT: lines = Our AI bot asking questions

            EXTRACT DATA FROM "USER:" LINES - those contain the actual information from the insurance company agent.
            "AGENT:" LINES - those are our AI bot asking questions.

            FIELD EXTRACTION GUIDE:
            - Prefered_Payer_ID: Updated/corrected payer ID if insurance company agent provides one
            - Prefered_Claims_Address: Updated mailing address for paper claims
            - Claims_TFL: Timely Filing Limit for claims (e.g., "90 days", "1 year")
            - Processing_Delays: **LIVE AGENT ONLY** - Capture processing delay info ONLY from live human agents, NOT from IVR.
              INCLUDE: Specific delays mentioned by live agent (e.g., "Claims taking 30-45 days", "System outage caused delays", "Backlog due to...")
              EXCLUDE (DO NOT CAPTURE): 
              - IVR pre-recorded messages like "We are experiencing higher wait times in our service centers"
              - Generic hold announcements, advertisements, disclaimers
              - Any automated/robotic voice messages about wait times or service delays
              RULE: If it sounds like a generic announcement that applies to all callers (not specific to THIS claim), DO NOT capture it.
            - Eligible: Patient eligibility on DOS (Yes/No/Pending)
            - CS_Agent_Name: The insurance company representative's name. EXTRACT FROM USER LINES ONLY - this is where the insurance agent speaks. When AGENT (our AI bot) asks for their name, USER (insurance agent) responds with THEIR name. NEVER extract the name that AGENT introduces itself with (that's our AI bot's name, ignore it). Always look for names in USER lines at the end of the conversation when our bot asks "what is your name?"
            - Call_Ref: Call reference number/ID from insurance company agent. SMART EXTRACTION RULES:
              1) If agent provides a specific reference number (e.g., "CC9955", "REF123456") → use that
              2) **RELATIVE DATE RESOLUTION**: If agent says "today's date", "this call's date", "the date of this call", or "current date" as the reference:
                 - Resolve to the ACTUAL CALL DATE in MM/DD/YYYY format
                 - Example: If call is on December 18, 2025 and agent says "the reference is today's date" → "12/18/2025"
              3) If agent says "use the date" or "date is your reference" → resolve to call date
              4) Extract from USER lines at end of conversation when bot asks for reference
            - Department_Phone_Number: Any transferred/referred department number provided by insurance company agent.
            - Status: **CRITICAL FIELD** - Claim status from USER lines. MUST BE ONE OF: "Verification Incomplete", "Not Received", "In Process", "Paid", "Denied", "Purged"
              DETECTION PATTERNS (look for these keywords/phrases in USER lines):
              - **"Paid"**: agent says "paid", "payment issued", "payment was made", "check sent", "EFT sent", "finalized", "processed and paid", "claim has been paid", mentions payment amount/date
              - **"Denied"**: agent says "denied", "denial", "rejected", "not covered", "declined", "claim was denied"
              - **"In Process"**: agent says "in process", "pending", "processing", "under review", "being reviewed", "still processing", "not yet finalized", "in queue"
              - **"Verification Incomplete"**: agent says "cannot verify", "insufficient information", "unable to authenticate", "unable to verify", "cannot pull up", or call ends due to failed validation (like incorrect ID).
              - **"Not Received"**: agent says "not received", "no record", "can't find", "don't see it", "not in system", "never received".
                **IMPORTANT**: Only use "Not Received" if the agent successfully searched for a **VALID** member ID/claim number but found no claim. 
                **DO NOT** use "Not Received" if the agent rejected the member ID/data itself (invalid/incomplete/could not verify). If verification failed or the ID was wrong, status MUST be "Verification Incomplete" or null, NOT "Not Received".
              - **"Purged"**: agent says "purged", "archived", "deleted", "removed from system", "no longer available"
              IMPORTANT: **DO NOT INFER** - Extract status ONLY if the agent explicitly states it or if the context of the conversation (payments/denials) makes it undeniable. If status is unclear or not mentioned, output null.
            - Appeals_Address: Mailing address for appeals
            - Received_date: Date claim/appeal was received from USER (MM/DD/YYYY) - if year missing, add 2023
            - Processing_Period: Expected processing time (e.g., "30 days", "2-3 weeks")
            - Other_information: **COMPREHENSIVE CAPTURE** - Extract ALL useful information from USER lines that doesn't fit in dedicated fields. MUST INCLUDE:
              **Payment Details:** Virtual card payments, draft numbers, clearance dates, payment methods not in other fields
              **Claim Details:** Partial payments, discounts taken, fee negotiations, adjustment reasons
              **Timely Filing:** TFL info for claims/appeals, contract-based deadlines, out-of-network deadlines
              **Procedures:** Corrected claim policies, reconsideration forms, website/portal references, submission methods
              **Agent Statements:** Corrections the agent makes ("that was my mistake"), clarifications, important notes
              **Line-Level Info:** Line denials, partial denials, specific line adjustments, "no lines denied"
              **Negative Info:** "not available", "we don't have that", system limitations
              **CALL END REASON:** When call ends WITHOUT claim status, identify the ACTUAL reason from transcript:
                - "Due to incorrect member ID" → ONLY if agent explicitly says ID is invalid/not found/doesn't match
                - "Call disconnected during IVR navigation" → If call ended during automated menu
                - "Call disconnected during hold/transfer" → If call dropped while on hold or during transfer
                - "Agent transferred call but disconnection occurred" → Transfer attempted but failed
                - "IVR system hang-up" → Automated system ended the call
                - "After hours/office closed" → IVR indicated closed hours
                - "System error on insurance side" → Agent mentioned system issues
                - "Agent could not locate account" → Agent searched but couldn't find ANY records (different from invalid ID)
                - "Call ended before reaching live agent" → Never connected to human
                - "Maximum hold time exceeded" → Cut off after long wait
                - "Agent requested callback" → Asked caller to call back later
                - "Insufficient verification information" → Missing DOB, SSN, or other required verification data
                - "Coverage terminated/inactive" → Member coverage issue, not ID issue
                DO NOT default to "incorrect member ID" - use the ACTUAL reason from the transcript!
              **Reference Numbers:** Any ID/reference not captured elsewhere (draft numbers, ticket numbers, etc.)
              **Examples to capture:**
              - "It was paid on virtual card. Draft number is 1197833320. Cleared on July 7."
              - "Partially paid - discount taken through fee negotiation."
              - "180 days from original process date for timely filing."
              - "We accept corrected claims. Form is on our website."
              - "That was my mistake - no lines denied."
              FORMAT: Capture as clear, readable statements. Separate multiple items with semicolons.
            - Processed_Date: Date claim was processed from USER (MM/DD/YYYY) - if year missing, add 2023
            - Denial_reason: Full denial reason/code
            - Claim_Number: Claim number or DCN from USER response - CRITICAL RULES:
              1) PRESERVE EXACT ALPHANUMERIC FORMAT - Do NOT strip letters! Example: "25083E193400" must stay as "25083E193400"
              2) PHONETIC ALPHABET NORMALIZATION: When agent uses phonetic clarification like "E as in Edward", "B as in Bravo", "A as in Alpha", extract ONLY the letter, NOT the clarification phrase!
                 - Example: "25092E as in Edward 031100" → "25092E031100" (NOT "25092EAsInEdward031100")
                 - Example: "ABC as in Alpha Bravo Charlie 123" → "ABC123"
                 - Remove "as in [word]" patterns and keep only the actual letter
              3) Copy the final normalized claim number with all letters and numbers preserved
            - Appealable: Appeal rights - "Yes", "No", or "Exhausted"
            - Appeals_TFL: Timely Filing Limit for appeals
            - Appeals_Submission_Method: "Mail", "Fax", or "Online Portal"
            - Online_Portal: Portal name/URL if mentioned
            - Appeals_Fax_Number: Fax for appeals
            - Corrected_claims: Whether corrected claim accepted (Yes/No)
            - Paid_date: **PAYMENT ISSUED DATE** (MM/DD/YYYY) - This is when the payment (check or EFT) was ISSUED/SENT, NOT deposited.
              IMPORTANT DISTINCTIONS:
              - Paid_date = "payment was issued on...", "check was cut on...", "we paid on...", "payment date is..."
              - Deposit_Date = "deposited on...", "cleared on...", "funds received on..." (different field!)
              - Processed_Date = "claim was processed on..." (different field!)
              Listen carefully for context clues. If agent says "payment was 5/27", use that for Paid_date.
            - PaymentAmount: Total paid amount
            - Payment_Method: "EFT" or "Check"
            - Paid_To: "Provider" or "Patient"
            - Check_Cashed: "Yes" if insurance agent says "cached" or "cashed", "No" if not cashed
            - Pay_to_address: Payment mailing address
            - Check_Number: Check number OR EFT trace/reference number (remove spaces if present, e.g., "C C 9 A C B V 2332" → "CC9ACBV2332"). For EFT, this is the EFT reference/trace number.
            - Bank_Account: Bank account (last 4 digits for EFT). If agent says "no bank account information available" or similar, put null here but capture that response in Other_information.
            - Deposit_Date: Payment deposit date (MM/DD/YYYY). IMPORTANT: When bot asks about EFT details and agent provides a deposit date, CAPTURE IT HERE. Example: "The deposit date is 5/28/2025" → "05/28/2025"
            - Appeal_Ticket: Reprocessing/appeal ticket number
            - PR_Amount: **TOTAL** Patient Responsibility amount. CRITICAL RULES:
              1) If agent mentions MULTIPLE amounts (e.g., Deductible=$35.09, Coinsurance=$305.31), calculate and store the SUM (e.g., $340.40)
              2) Store as numeric value only (no $ symbol)
              3) ALSO capture the individual breakdown in Other_information (e.g., "Deductible: $35.09, Coinsurance: $305.31")
            - PR_Type: Types of patient responsibility - can be COMMA-SEPARATED if multiple. Valid values: "Deductible", "Coinsurance", "Copay"
              1) If agent mentions Deductible AND Coinsurance → "Deductible, Coinsurance"
              2) If only one type mentioned → just that type (e.g., "Deductible")
              3) Example: Agent says "$35 deductible and $305 coinsurance" → PR_Type: "Deductible, Coinsurance"
            - Bulk_Payment: Bulk payment total  
            """
        worked_example = """
            EXAMPLE SECTION — FOR LEARNING ONLY
            THIS IS NOT THE REAL TRANSCRIPT. DO NOT EXTRACT FROM THIS.
            THESE VALUES ARE FAKE AND MUST NEVER APPEAR IN YOUR OUTPUT.
            WORKED EXAMPLE — Every field shown with source transcript line and reasoning:

            EXAMPLE TRANSCRIPT:
            USER: Hello good morning. How can I help you today?
            AGENT: Good morning. I'm calling from a provider's office to check on the status of a claim.
            USER: Please enter or say your NPI.
            AGENT: <DTMF:1558672279>
            USER: Enter or say your tax ID.
            AGENT: <DTMF:010894487>
            USER: I am Alina. I am here to assist you with the claim. What exactly do you want to know?
            AGENT: I'm checking the status of a claim. Can you tell me if it was received and what the current status is?
            USER: Tell me your full name.
            AGENT: My name is Stephen.
            USER: What is the patient's name?
            AGENT: The patient's name is Krystal Rogers.
            USER: Do you have the subscriber ID?
            AGENT: Yes the subscriber ID is 9620232607.
            USER: Provide the patient's date of birth.
            AGENT: 12/19/1994.
            USER: Do you have the provider address?
            AGENT: Yes the provider address is PO Box 1234 Pasadena CA 91009-8400.
            USER: Please provide the billed amount.
            AGENT: The billed amount is $1500.
            USER: Enter the date of service.
            AGENT: 06/14/2021.
            USER: Your claim is paid.
            AGENT: Thank you. When was the claim received?
            USER: 25 January 2025.
            AGENT: When was the claim processed?
            USER: After 10 days.
            AGENT: What was the paid amount?
            USER: 3500.
            AGENT: What is the patient responsibility amount?
            USER: The patient responsibility is 10 dollars deductible.
            AGENT: Was payment made to the provider or patient?
            USER: The payment was made to the provider.
            AGENT: Can you provide the claim number?
            USER: 145033ABC.
            AGENT: When was the payment issued?
            USER: 15th October.
            AGENT: Can you provide the total bulk payment amount?
            USER: 4000 dollars.
            AGENT: What payment method was used, check or EFT?
            USER: EFT.
            AGENT: Can you provide the EFT reference number?
            USER: 145ASBVW.
            AGENT: What was the deposit date?
            USER: 25 February.
            AGENT: Can you provide the bank account details?
            USER: I do not have the bank account number listed.
            AGENT: Was the claim partially paid or denied?
            USER: The claim was fully paid. No lines were denied.
            AGENT: Is the claim appealable?
            USER: Yes it is appealable. You have 180 days to file an appeal.
            AGENT: What is the preferred submission method for appeals?
            USER: You can submit via mail to PO Box 9000 Atlanta GA 30301.
            AGENT: Is there an online portal for appeals?
            USER: Yes, you can use availity.com. Registration is required.
            AGENT: What is the appeals fax number?
            USER: The fax number for appeals is 1-800-555-1234.
            AGENT: Do you accept corrected claims?
            USER: Yes we accept corrected claims.
            AGENT: What is the timely filing limit for claims?
            USER: Timely filing limit is 1 year from date of service.
            AGENT: What payer ID should be used for electronic submissions?
            USER: You should use payer ID 68069.
            AGENT: What is the correct mailing address for paper claims?
            USER: Send paper claims to PO Box 1000 Miami FL 33101.
            AGENT: Was the patient eligible on the date of service?
            USER: Yes the patient was eligible on that date.
            AGENT: Are there any processing delays currently?
            USER: Yes we are experiencing a 30 to 45 day delay due to system backlog.
            AGENT: May I get your name for my records?
            USER: My name is Alina.
            AGENT: Do you have a reference number for this call?
            USER: 55BDWS03262026.
            AGENT: Thank you Alina. Have a great day!

            ═══════════════════════════════════════════════════
            CORRECT EXTRACTION — EVERY FIELD WITH SOURCE + REASON
            ═══════════════════════════════════════════════════

            IDENTITY & CALL CONTEXT FIELDS:
            ─────────────────────────────────

            "Callers_Name": "Stephen"
            SOURCE → AGENT line: "My name is Stephen."
            REASON → This is our AI bot's caller name from claim data. Passed through unchanged.
            NOTE   → Do NOT confuse with CS_Agent_Name. Callers_Name = our bot. CS_Agent_Name = insurance rep.

            "Callers_Intent": "Claim Status"
            SOURCE → Claim data field, confirmed by AGENT saying "check on the status of a claim"
            REASON → Passed through from input data. Not extracted from transcript.

            "Company_Name": "Natera"
            SOURCE → Claim data. Not spoken in this transcript.
            REASON → Passed through from input data unchanged.

            "NPI": "1558672279"
            SOURCE → AGENT line: "<DTMF:1558672279>" when USER asked for NPI.
            REASON → Our bot entered this from claim data. Passed through unchanged.

            "Tax_ID": "010894487"
            SOURCE → AGENT line: "<DTMF:010894487>" when USER asked for Tax ID.
            REASON → Our bot entered this from claim data. Passed through unchanged.

            "Payer_ID": "68069"
            SOURCE → Claim data. Also confirmed when USER says "You should use payer ID 68069."
            REASON → Passed through from input. If agent corrects it, updated value goes to Prefered_Payer_ID.

            "Payer_Name": "SUNSHINE HEALTH PLAN"
            SOURCE → Claim data. Not extracted from transcript.
            REASON → Passed through unchanged.

            "Payer_Phone_Number": "+919592791186"
            SOURCE → Claim data. Not extracted from transcript.
            REASON → Passed through unchanged.

            "Provider_Address": "PO Box 1234 Pasadena, CA 91009-8400"
            SOURCE → AGENT line: "the provider address is PO Box 1234 Pasadena CA 91009-8400"
            REASON → Our bot confirmed this from claim data. Passed through unchanged.

            "Provider_Fax": "18328782234"
            SOURCE → Claim data. Not extracted from transcript in this example.
            REASON → Passed through unchanged.

            "Provider_Email": null
            SOURCE → Not in this transcript.
            REASON → Not mentioned by any party. Output null.

            "Callback_Number": null
            SOURCE → Not in this transcript.
            REASON → Not mentioned. Output null.

            "CLIA_Number": null
            SOURCE → Not in this transcript.
            REASON → Not mentioned. Output null.

            ─────────────────────────────────
            PATIENT & SUBSCRIBER FIELDS:
            ─────────────────────────────────

            "Patient_FirstName": "Krystal"
            SOURCE → AGENT line: "The patient's name is Krystal Rogers."
            REASON → First word of patient name. Passed through from claim data / confirmed in transcript.

            "Patient_LastName": "Rogers"
            SOURCE → AGENT line: "The patient's name is Krystal Rogers."
            REASON → Second word = last name. If patient_lastname field was missing in claim data,
                        extract last name from the full name spoken. "Rogers" is the last name here.

            "Subscriber_ID": "9620232607"
            SOURCE → AGENT line: "the subscriber ID is 9620232607"
            REASON → Passed through from claim data. Also known as Member ID — treat as same field.

            "Subscriber_FirstName": "Krystal"
            SOURCE → Claim data. In this case Relationship = SELF so subscriber = patient.
            REASON → Passed through unchanged.

            "Subscriber_LastName": "Rogers"
            SOURCE → Claim data. Relationship = SELF so subscriber = patient.
            REASON → Passed through unchanged.

            "Relationship": "SELF"
            SOURCE → Claim data. Not extracted from transcript.
            REASON → Passed through unchanged.

            "DOB": "12/19/1994"
            SOURCE → AGENT line: "12/19/1994" when USER asked for date of birth.
            REASON → Our bot provided this from claim data. Passed through unchanged. Already MM/DD/YYYY.

            "DOS": "06/14/2021"
            SOURCE → AGENT line: "06/14/2021" when USER asked for date of service.
            REASON → Passed through from claim data. Already MM/DD/YYYY.

            "Charge": "1500"
            SOURCE → AGENT line: "The billed amount is $1500."
            REASON → Numeric only, no $ symbol. This is the billed/charge amount from claim data.

            "Insurance_Payment": "163.96"
            SOURCE → Claim data field. Not spoken in this transcript.
            REASON → Passed through unchanged.

            "Submission_Method": "Electronic"
            SOURCE → Claim data. Not extracted from transcript.
            REASON → Passed through unchanged.

            "Appeal_Submission_Date": null
            SOURCE → Not in this transcript.
            REASON → No appeal submission date mentioned. Output null.

            "Claim_Appeal": "Claim"
            SOURCE → Claim data field. Confirmed by Callers_Intent = "Claim Status".
            REASON → Passed through unchanged. Determines whether word "claim" or "appeal" used.

            "Unresponded_Claim": "Yes"
            SOURCE → Claim data. Not extracted from transcript.
            REASON → Passed through unchanged.

            "Unresponded_Appeal": null
            SOURCE → Claim data. Not applicable here.
            REASON → Passed through unchanged.

            ─────────────────────────────────
            LIVE CALL EXTRACTED FIELDS:
            ─────────────────────────────────

            "Status": "Paid"
            SOURCE → USER line: "Your claim is paid."
            REASON → Matches "Paid" keyword exactly. Must be one of: Not Received / In Process / Paid / Denied / Purged.
            WRONG  → "Fully paid", "Paid in full", "Payment was made" — always normalize to exact enum "Paid".

            "Received_date": "01/25/2025"
            SOURCE → USER line: "25 January 2025" — spoken in response to AGENT asking when claim was received.
            REASON → Convert written date to MM/DD/YYYY. Day=25, Month=January=01, Year=2025 → "01/25/2025".
            NOTE   → Year was provided here. If year missing, use current year from TODAY'S DATE at top of prompt.

            "Processed_Date": "02/04/2025"
            SOURCE → USER line: "After 10 days." — spoken in response to AGENT asking when claim was processed.
            REASON → No explicit date given. CALCULATE: Received_date 01/25/2025 + 10 days = 02/04/2025.
            RULE   → If agent gives a processing PERIOD (not a date), calculate from Received_date and store result.
            NOTE   → This is DIFFERENT from Paid_date (when payment issued) and Deposit_Date (when deposited).

            "PaymentAmount": "3500"
            SOURCE → USER line: "3500." — spoken in response to AGENT asking paid amount.
            REASON → Numeric only, no $ symbol. This is what the insurance paid for this claim.
            NOTE   → Different from Bulk_Payment (total across multiple claims) and Charge (what was billed).

            "PR_Amount": "10"
            SOURCE → USER line: "The patient responsibility is 10 dollars deductible."
            REASON → Numeric only. If multiple types (deductible + coinsurance), SUM them here.
            EXAMPLE MULTI → If USER said "$35 deductible and $305 coinsurance" → PR_Amount = "340" (35+305)

            "PR_Type": "Deductible"
            SOURCE → USER line: "10 dollars deductible" — agent explicitly said the word "deductible".
            REASON → Valid values: "Deductible", "Coinsurance", "Copay". Use ONLY what agent explicitly says.
            WRONG  → If USER just says "10 dollars" with no type word → PR_Type = null. Never guess the type.
            MULTI  → If USER says "deductible and coinsurance" → PR_Type = "Deductible, Coinsurance" (comma-separated)

            "Paid_To": "Provider"
            SOURCE → USER line: "The payment was made to the provider."
            REASON → Valid values: "Provider" or "Patient". Exact match here.

            "Claim_Number": "145033ABC"
            SOURCE → USER line: "145033ABC." — spoken in response to AGENT asking for claim number.
            REASON → Preserve EXACT alphanumeric. Letters ABC must NOT be stripped.
            PHONETIC EXAMPLE → If USER said "145033 A as in Alpha B as in Bravo C as in Charlie" → still "145033ABC"
                                Remove "as in [word]" patterns, keep only the actual letter.

            "Paid_date": "10/15/2025"
            SOURCE → USER line: "15th October." — spoken in response to AGENT asking when payment was ISSUED.
            REASON → This is when payment was ISSUED/SENT. Day=15, Month=October=10, Year=current year.
            CRITICAL DISTINCTION:
                Paid_date    = "payment was issued on..." / "check was cut on..." / "we paid on..."
                Deposit_Date = "deposited on..." / "cleared on..." / "funds received on..."
                Processed_Date = "claim was processed on..."
            WRONG  → Do NOT put issued date into Deposit_Date. Do NOT put deposit date into Paid_date.

            "Bulk_Payment": "4000"
            SOURCE → USER line: "4000 dollars." — spoken in response to AGENT asking for TOTAL/BULK payment amount.
            REASON → Numeric only. This is total payment across multiple claims in a bulk/batch payment.
            NOTE   → Different from PaymentAmount (this single claim). Only fill if AGENT explicitly asks
                        "total bulk payment" and USER gives a figure. Do not confuse with PaymentAmount.

            "Payment_Method": "EFT"
            SOURCE → USER line: "EFT." — spoken in response to AGENT asking check or EFT.
            REASON → Valid values: "EFT" or "Check". Exact match here.

            "Check_Number": "145ASBVW"
            SOURCE → USER line: "145ASBVW." — spoken in response to AGENT asking for EFT reference number.
            REASON → This field stores BOTH check numbers AND EFT trace/reference numbers.
                        For EFT calls, the EFT reference number goes here.
                        Remove any spaces: "C C 9 A" → "CC9A".

            "Deposit_Date": "02/25/2025"
            SOURCE → USER line: "25 February." — spoken in response to AGENT asking for deposit date.
            REASON → When EFT funds were deposited/cleared. Day=25, Month=February=02, Year=current year.
            NOTE   → No year given → use current year from TODAY'S DATE. Format MM/DD/YYYY → "02/25/2025".

            "Bank_Account": null
            SOURCE → USER line: "I do not have the bank account number listed."
            REASON → Agent explicitly said not available. No actual number given → null.
            CRITICAL → Capture this refusal in Other_information. Do NOT put text like "not available" here — null only.

            "Check_Cashed": null
            SOURCE → Not mentioned in this transcript.
            REASON → Only fill "Yes" or "No" if agent explicitly says check was or was not cashed.
                        For EFT payments this field is typically null. No mention → null.

            "Pay_to_address": null
            SOURCE → Not mentioned in this transcript.
            REASON → Agent did not provide a payment mailing address. Output null.

            "Appealable": "Yes"
            SOURCE → USER line: "Yes it is appealable."
            REASON → Valid values: "Yes", "No", "Exhausted". Exact match here.

            "Appeals_TFL": "180 days"
            SOURCE → USER line: "You have 180 days to file an appeal."
            REASON → Timely filing limit for appeals specifically. Store as spoken: "180 days".

            "Appeals_Submission_Method": "Mail"
            SOURCE → USER line: "You can submit via mail to PO Box 9000 Atlanta GA 30301."
            REASON → Valid values: "Mail", "Fax", "Online Portal". Agent said "via mail" → "Mail".

            "Appeals_Address": "PO Box 9000 Atlanta GA 30301"
            SOURCE → USER line: "...to PO Box 9000 Atlanta GA 30301." — given when submission method was mail.
            REASON → Mailing address specifically for appeals submission.

            "Online_Portal": "availity.com"
            SOURCE → USER line: "Yes, you can use availity.com."
            REASON → Portal name or URL for appeals. Capture exactly as spoken.
            NOTE   → Also note "Registration is required" in Other_information since it has no dedicated field.

            "Appeals_Fax_Number": "1-800-555-1234"
            SOURCE → USER line: "The fax number for appeals is 1-800-555-1234."
            REASON → Fax number specifically for appeals. Preserve format as spoken.

            "Corrected_claims": "Yes"
            SOURCE → USER line: "Yes we accept corrected claims."
            REASON → Valid values: "Yes" or "No". Agent said yes → "Yes".

            "Claims_TFL": "1 year from date of service"
            SOURCE → USER line: "Timely filing limit is 1 year from date of service."
            REASON → Timely filing limit for regular claims (not appeals). Store as spoken.

            "Prefered_Payer_ID": "68069"
            SOURCE → USER line: "You should use payer ID 68069."
            REASON → Insurance agent confirmed/provided the payer ID to use for electronic submissions.
            NOTE   → Only fill if agent provides or CORRECTS the payer ID during the call.
                        If it matches what was already in claim data, still capture it here as confirmed.

            "Prefered_Claims_Address": "PO Box 1000 Miami FL 33101"
            SOURCE → USER line: "Send paper claims to PO Box 1000 Miami FL 33101."
            REASON → Mailing address for paper claim submissions. Different from Appeals_Address.

            "Eligible": "Yes"
            SOURCE → USER line: "Yes the patient was eligible on that date."
            REASON → Valid values: "Yes", "No", "Pending". Patient eligibility on date of service.

            "Processing_Delays": "30 to 45 day delay due to system backlog"
            SOURCE → USER line: "we are experiencing a 30 to 45 day delay due to system backlog."
            REASON → Live agent specifically mentioned a processing delay for this claim.
            EXCLUDE → Generic IVR messages like "we are experiencing higher call volumes" — those are NOT delays.
            RULE   → Only capture if a LIVE HUMAN agent mentions it AND it is specific (not a generic announcement).

            "CS_Agent_Name": "Alina"
            SOURCE → USER line: "My name is Alina." — spoken at END of call when AGENT asked "May I get your name."
            REASON → This is the insurance company representative's name.
            CRITICAL → AGENT also said "I am Alina" at introduction — IGNORE THAT. That was our bot's greeting.
                        ONLY extract from USER lines at end of call after AGENT asks for name.
            WRONG  → Do not extract from AGENT lines. Do not extract from IVR lines.

            "Call_Ref": "55BDWS03262026"
            SOURCE → USER line: "55BDWS03262026." — spoken when AGENT asked for reference number.
            REASON → Call reference number from insurance company. Preserve exact alphanumeric format.
            RELATIVE DATE RULE → If USER says "today's date is your reference" → resolve to actual call date MM/DD/YYYY.

            "Department_Phone_Number": null
            SOURCE → Not mentioned in this transcript.
            REASON → Only fill if agent provides a different department's phone number or transfer number.

            "Appeals_Address" (already shown above): "PO Box 9000 Atlanta GA 30301"

            "Processing_Period": null
            SOURCE → USER said "after 10 days" but this was used to CALCULATE Processed_Date.
            REASON → Processing_Period = expected future processing time ("30 days", "2-3 weeks").
                        "After 10 days" here was about when it WAS processed (past), not expected time.
            NOTE   → If agent says "it will take 30 days to process" → Processing_Period = "30 days".

            "Denial_reason": null
            SOURCE → Not mentioned. Claim is paid, not denied.
            REASON → Only fill for denied claims with explicit denial reason/code from agent.

            "Appeal_Ticket": null
            SOURCE → Not mentioned in this transcript.
            REASON → Only fill if agent provides a reprocessing or appeal ticket/reference number.

            "Other_information": "Payment was made to the provider; EFT reference number is 145ASBVW; Bank account number not available per agent; Claim was fully paid with no lines denied; Patient responsibility: $10 deductible; Availity portal requires registration for appeals submission; Processing delay: 30-45 days due to system backlog (also in Processing_Delays field)"
            SOURCE → Multiple USER lines throughout transcript.
            REASON → Safety net for ALL useful agent statements. Captures:
                    - Payment context and method details
                    - Bank account refusal (even though Bank_Account = null, record WHY here)
                    - "No lines denied" confirmation (no dedicated field for this)
                    - PR breakdown detail
                    - Portal registration requirement (no dedicated field)
                    - Delay detail (cross-reference with Processing_Delays field)
            RULE   → When in doubt, ALSO put it here. Better to duplicate than to lose information.
            FORMAT → Separate items with semicolons. Clear readable statements.

            ═══════════════════════════════════════════════════
            SUMMARY: FIELD SOURCE REFERENCE TABLE
            ═══════════════════════════════════════════════════

            FROM CLAIM DATA (passed through, not extracted from transcript):
            Callers_Name, Callers_Intent, Company_Name, NPI, Tax_ID, CLIA_Number,
            Callback_Number, Payer_ID, Payer_Name, Payer_Phone_Number, Provider_Address,
            Provider_Fax, Provider_Email, Patient_FirstName, Patient_LastName,
            Subscriber_ID, Subscriber_FirstName, Subscriber_LastName, Relationship,
            DOB, DOS, Charge, Insurance_Payment, Submission_Method, Appeal_Submission_Date,
            Claim_Appeal, Unresponded_Claim, Unresponded_Appeal

            FROM USER LINES IN TRANSCRIPT (insurance agent speaks → extract here):
            Status, Received_date, Processed_Date, Paid_date, Deposit_Date,
            PaymentAmount, Bulk_Payment, PR_Amount, PR_Type, Paid_To, Payment_Method,
            Check_Number, Bank_Account, Check_Cashed, Pay_to_address,
            Claim_Number, Appealable, Appeals_TFL, Appeals_Submission_Method,
            Appeals_Address, Online_Portal, Appeals_Fax_Number, Corrected_claims,
            Claims_TFL, Prefered_Payer_ID, Prefered_Claims_Address, Eligible,
            Processing_Delays, Processing_Period, Denial_reason, Appeal_Ticket,
            CS_Agent_Name, Call_Ref, Department_Phone_Number, Other_information

            NEVER extract from AGENT lines:
            AGENT lines are our AI bot asking questions. Data never comes from AGENT lines.
            Exception: AGENT lines confirm values from claim data (NPI, DOB etc.) — those are
            already in claim data so they are passed through, not re-extracted from AGENT speech.

            ═══════════════════════════════════════════════════
            CRITICAL RULES RECAP (most commonly missed):
            ═══════════════════════════════════════════════════

            DATES:
            Paid_date    → when payment ISSUED ("we paid on", "check cut on", "payment date")
            Deposit_Date → when payment LANDED ("deposited on", "cleared on", "funds received")
            Processed_Date → when CLAIM was processed ("claim processed on", or calculate from received + period)
            Received_date → when claim was RECEIVED by insurance ("received on", "we got it on")
            → These are FOUR different dates. Never put one in another's field.

            AMOUNTS:
            Charge        → what provider billed (from claim data)
            PaymentAmount → what insurance paid for THIS claim (from agent in transcript)
            Bulk_Payment  → total payment across multiple claims in batch (from agent in transcript)
            PR_Amount     → patient's share (deductible + coinsurance + copay, summed if multiple)
            → Never mix these up.

            NAMES:
            Callers_Name  → our bot's name (from claim data, from AGENT lines)
            CS_Agent_Name → insurance rep's name (from USER lines at END of call only)
            → NEVER swap these.

            IDs:
            Check_Number  → check number OR EFT trace number (same field for both)
            Bank_Account  → last 4 digits of bank account for EFT (null if not given, capture refusal in Other_info)
            Claim_Number  → preserve ALL letters and digits exactly as spoken
            Call_Ref      → reference number from insurance agent at end of call

            NULL RULES:
            → null means not mentioned or not available
            → Never put "not available" or "unknown" as a text value — use null
            → BUT always explain in Other_information WHY it is null if agent said something about it
            """
        # schema template with only data fields
        schema = "{\n" + ",\n".join([f'  "{k}": null' for k in self._DATA_FIELDS]) + "\n}"
        
        rules = (
            "EXTRACTION RULES:\n"
            "1) Extract ONLY what is explicitly stated in the transcript\n"
            "2) Use null for missing information - DO NOT GUESS\n"
            "3) For dates, use MM/DD/YYYY format when possible\n"
            "4) For amounts, use numeric values only (no currency symbols)\n"
            "5) For Status field, use exact values from the guide\n"
            "6) If multiple values exist in chunk, use the first one\n"
            "7) Output exactly one JSON object, no extra text\n"
            "8) ALPHANUMERIC PRESERVATION: For Claim_Number and all IDs, preserve EXACT format including letters. Never strip letters from alphanumeric values!\n"
            "9) NEGATIVE RESPONSES: If agent says something is 'not available', 'we don't have that', etc., capture that in Other_information\n"
            "10) PHONETIC NORMALIZATION: Convert phonetic clarifications to just the letter (e.g., 'E as in Edward' → 'E', 'B as in Bravo' → 'B')\n"
            "11) PR AGGREGATION: If multiple patient responsibility types mentioned (Deductible + Coinsurance + Copay), SUM them for PR_Amount and list all types comma-separated in PR_Type\n"
            "12) RELATIVE DATE RESOLUTION: When agent says 'today's date', 'current date', 'this call date' as a reference value, resolve it to the actual call date format (MM/DD/YYYY). Don't leave it as 'today's date' - convert to actual date.\n"
            "13) OTHER_INFORMATION IS CRITICAL: Capture ALL useful agent statements that don't have dedicated fields - payment methods, draft numbers, corrections, website references, TFL details, partial payments, agent mistakes/clarifications. Leave nothing useful behind.\n"
            "14) EFT/PR FALLBACK: Any EFT-related info (trace numbers, deposit dates, bank details, 'no bank account available') or PR-related info (deductible, coinsurance, copay amounts) that doesn't fit a dedicated field MUST be captured in Other_information. Never lose EFT or payment context.\n"
            "15) DATE FIELD DISAMBIGUATION: Different dates go to different fields - Paid_date (when payment issued), Deposit_Date (when deposited/cleared), Processed_Date (when claim processed). Listen for context clues like 'paid on', 'deposited on', 'processed on' to assign correctly.\n"
            "16) MISSING YEAR IN DATES: If agent provides only month and day (e.g., '5/28', 'May 28th', 'the 15th'), use the CURRENT YEAR from TODAY'S DATE shown above. Format as MM/DD/YYYY.\n"
            "17) FIELD PRIORITY: If data matches a dedicated field in the schema, put it in that field NOT in Other_information. Only use Other_information for data that has NO matching dedicated field.\n"
            "18) CALL END REASON EXTRACTION: When call ends WITHOUT claim status, you MUST identify and record the ACTUAL reason in Other_information. Analyze the transcript for: IVR hang-up, call disconnected during hold/transfer, agent couldn't locate account, after-hours message, system error, coverage terminated, insufficient verification info, etc. Use 'Due to incorrect member ID' ONLY when the agent EXPLICITLY states the member ID is invalid/incomplete/not found. Never default to incorrect member ID without explicit evidence in transcript."
        )
       
        print(f"--- Transcript Chunk for Extraction ---\n{transcript_chunk}\n--- End of Chunk ---")
        return (
            f"{field_guidance}\n\n"
            f"{rules}\n\n"
            f"{worked_example}\n\n"
            f"SCHEMA:\n{schema}\n\n"
            f"⚠️ REAL TRANSCRIPT BELOW — EXTRACT ONLY FROM THIS. IGNORE ALL VALUES FROM THE EXAMPLE ABOVE.\n\n"
            f"TRANSCRIPT CHUNK START\n{transcript_chunk}\nTRANSCRIPT CHUNK END\n\n"
            f"Extract the fields based on the guide above. Return ONLY the JSON object."
        )
        
    async def _extract_from_chunk(self, transcript_chunk: str) -> dict:
        sys_msg = {"role": "system", "content": self._build_extractor_system_prompt()}
        user_msg = {"role": "user", "content": self._build_extractor_user_prompt_for_chunk(transcript_chunk)}

        try:
            resp = await self._openai.ChatCompletion.acreate(
                model="gpt-4o",
                temperature=0.1,
                messages=[
                    sys_msg,
                    {"role": "system", "content": "You must respond ONLY with a single valid JSON object. No explanations, no markdown, just the JSON."},
                    user_msg
                ],
                max_tokens=1500  # Increased for more fields
            )
            raw = resp["choices"][0]["message"]["content"] or "{}"
            # Clean any potential markdown formatting
            raw = raw.strip()
            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
            
            data = json.loads(raw)
            if not isinstance(data, dict):
                data = {}
        except Exception as e:
            logger.error(f"Extractor chunk error: {e}")
            data = {}

        # sanitize to schema (only data fields, not audit fields)
        clean = {}
        for k in self._DATA_FIELDS:
            v = data.get(k, None)
            if isinstance(v, (list, dict)):
                v = json.dumps(v, ensure_ascii=False)
            clean[k] = v
        return clean

    async def ai_extract_and_build_output_record(self) -> dict:
        acc = self._blank_output_record()

        # logger.info(f"[EXTRACTION] Starting with {len(self.conversation_history)} history entries")
        
        if not self.conversation_history:
            logger.warning("[EXTRACTION] conversation_history is EMPTY - no data to extract!")
            return acc

        # logger.info(f"[EXTRACTION] Processing {len(self.conversation_history)} conversation turns")
        
        async def _run():
            chunk_count = 0
            for chunk in self._iter_history_chunks(max_chars=30000):
                chunk_count += 1
                if not chunk.strip():
                    continue
                # logger.info(f"[EXTRACTION] Processing chunk {chunk_count}: {len(chunk)} chars")
                partial = await self._extract_from_chunk(chunk)
                # logger.info(f"[EXTRACTION] Chunk {chunk_count} extracted: {partial}")
                self._merge_records_latest_wins(acc, partial)

        try:
            await _run()
        except Exception as e:
            logger.error(f"AI full-history extractor failed: {e}")

        return acc
    