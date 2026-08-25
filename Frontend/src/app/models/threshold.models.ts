export interface ThresholdSettings {
  lead_score_threshold: number;
  hide_below_threshold: boolean;
  notify_on_threshold: boolean;
  auto_convert_threshold: number | null;
  leads_above: number;
  leads_below: number;
}

export interface ThresholdUpdateRequest {
  lead_score_threshold: number;
  hide_below_threshold: boolean;
  notify_on_threshold: boolean;
  auto_convert_threshold: number | null;
}
