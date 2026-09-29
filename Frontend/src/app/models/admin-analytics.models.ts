export type DateRangeKey = '7d' | '30d' | '3m' | '6m' | '1y';

export interface DateRangeOption {
  label: string;
  value: DateRangeKey;
  description: string;
  days: number;
}

export interface AdminKpiSummary {
  totalUsers: number;
  totalUsersGrowthPct: number;
  newUsers: number;
  newUsersGrowthPct: number;
  totalPlanPurchases: number;
  totalPlanPurchasesGrowthPct: number;
  activeSubscriptions: number;
  activeSubscriptionsGrowthPct: number;
  totalRevenue: number;
  conversionRate: number;
}

export interface OnboardingDataPoint {
  date: string;
  displayDate: string;
  fullDate: string;
  users: number;
  cumulativeUsers: number;
}

export interface PlanTierSummary {
  name: string;
  key: string;
  color: string;
  lightBg: string;
  borderColor: string;
  totalPurchases: number;
  sharePercentage: number;
  averagePrice: number;
  visible: boolean;
}

export interface PlanPurchasesData {
  dates: string[];
  displayDates: string[];
  fullDates: string[];
  plans: Record<string, number[]>;
  totals: number[];
  tierSummaries: PlanTierSummary[];
}

export interface AdminDashboardData {
  range: DateRangeKey;
  summary: AdminKpiSummary;
  onboarding: {
    dates: string[];
    displayDates: string[];
    fullDates: string[];
    counts: number[];
    cumulative: number[];
    peakCount: number;
    peakDate: string;
    avgDaily: number;
    totalInRange: number;
  };
  planPurchases: PlanPurchasesData;
}
