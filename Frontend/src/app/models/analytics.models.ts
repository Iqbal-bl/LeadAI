export interface AnalyticsDailyStats {
  date: string;
  leads: number;
  hot: number;
  calls: number;
}

export interface AnalyticsAgentStats {
  email: string;
  name: string;
  role: string;
  assigned: number;
  closed: number;
  qualified: number;
  calls: number;
}

export interface AnalyticsData {
  client_id: string;
  leads_today: number;
  total_leads: number;
  cold: number;
  warm: number;
  hot: number;
  qualified: number;
  assigned: number;
  unassigned: number;
  needs_human: number;
  closed: number;
  calls: number;
  completed_calls: number;
  failed_calls: number;
  avg_call_duration: number;
  conversion_rate: number;
  avg_lead_score: number;
  ai_containment_rate: number;
  documents: number;
  chunks: number;
  daily: AnalyticsDailyStats[];
  agents: AnalyticsAgentStats[];
  channels: Record<string, number>;
}

export interface AnalyticsFunnelStage {
  stage: string;
  count: number;
  percentage: number;
}
