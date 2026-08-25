export interface KpiCard {
  title: string;
  value: string;
  change: number;
  changeLabel: string;
  icon: string;
  color: string;
  trend: 'up' | 'down' | 'neutral';
  sparkline: number[];
}
