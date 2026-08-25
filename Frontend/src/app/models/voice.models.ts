export interface CallStatus {
  voice_provider: string;
  exotel_configured: boolean;
  stt: string;
  tts: string;
  default_language: string;
}

export interface CallDetails {
  id: string;
  conversation_id: string;
  call_sid: string;
  provider: string;
  mode: 'ai_voice' | 'agent';
  status: 'in-progress' | 'failed' | 'completed' | string;
  duration_sec: number;
  phone_masked: string;
  language: string;
  script_id: string | null;
  initiated_by_email: string | null;
  failure_reason: string | null;
  recording_url: string | null;
  created_at: string;
}

export interface CallTranscriptMessage {
  id: string;
  sender: 'ai' | 'customer' | 'agent' | 'system';
  sender_email: string | null;
  content: string;
  confidence: number | null;
  model_used: string | null;
  call_sid: string;
  created_at: string;
}

export interface CallTranscript {
  id: string;
  call_sid: string;
  conversation_id: string;
  provider: string;
  mode: 'ai_voice' | 'agent';
  status: string;
  handed_off: boolean;
  duration_sec: number;
  phone_masked: string;
  language: string;
  script_id: string | null;
  initiated_by_email: string | null;
  failure_reason: string | null;
  recording_url: string | null;
  created_at: string;
  messages: CallTranscriptMessage[];
}

export interface VoiceTurnResponse {
  reply: string;
  confidence: number;
  handed_off: boolean;
  tts: {
    provider: string;
    language: string;
    text: string;
  };
  lead_status: string;
  lead_score: number;
}

export interface CallSyncResponse {
  imported: number;
  total_messages: number;
  lead_status: string;
  lead_score: number;
  reason: string | null;
}

