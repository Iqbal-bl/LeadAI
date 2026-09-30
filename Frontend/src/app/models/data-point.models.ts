export type DataPointType =
  | 'text'
  | 'number'
  | 'boolean'
  | 'select'
  | 'date'
  | 'email';

export interface DataPoint {
  id: string;
  company_id?: string;
  key: string;
  label: string;
  data_type: DataPointType;
  options?: string[] | null;
  description?: string | null;
  required: boolean;
  display_order?: number | null;
  is_active: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface CreateDataPointPayload {
  key: string;
  label: string;
  data_type: DataPointType;
  options?: string[];
  description?: string;
  required?: boolean;
  display_order?: number;
}

export interface UpdateDataPointPayload {
  key?: string;
  label?: string;
  data_type?: DataPointType;
  options?: string[];
  description?: string;
  required?: boolean;
  display_order?: number;
  is_active?: boolean;
}
