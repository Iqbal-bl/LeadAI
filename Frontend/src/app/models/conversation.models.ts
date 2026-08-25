export interface Conversation {
  id: number;
  leadId: number;
  leadName: string;
  type: 'AI' | 'Human' | 'Scheduled';
  status: 'Completed' | 'In Progress' | 'Missed' | 'Scheduled';
  startTime: string;
  duration: string;
  summary: string;
  confidence: number;
  agent: string;
  callSid?: string | null;
  channel?: string;
  aboveThreshold?: boolean;
}

