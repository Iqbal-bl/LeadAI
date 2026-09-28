import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { map } from 'rxjs/operators';
import { ApiService } from './api.service';
import {
  AdminDashboardData,
  DateRangeKey,
  DateRangeOption,
  PlanTierSummary,
} from '../models/admin-analytics.models';

@Injectable({
  providedIn: 'root',
})
export class AdminAnalyticsService {
  readonly rangeOptions: DateRangeOption[] = [
    { label: '7 Days', value: '7d', description: 'Past week trend', days: 7 },
    {
      label: '30 Days',
      value: '30d',
      description: 'Past month overview',
      days: 30,
    },
    {
      label: '3 Months',
      value: '3m',
      description: 'Quarterly trajectory',
      days: 90,
    },
    {
      label: '6 Months',
      value: '6m',
      description: 'Half-year performance',
      days: 180,
    },
    {
      label: '1 Year',
      value: '1y',
      description: 'Annual macro growth',
      days: 365,
    },
  ];

  constructor(private apiService: ApiService) {}

  public getDashboardData(
    range: DateRangeKey = '30d',
  ): Observable<AdminDashboardData> {
    return this.apiService
      .get<any>('analytics/admin-dashboard', {
        params: { range },
        companyScoped: false,
      })
      .pipe(map((res) => this.normalizeBackendData(res, range)));
  }

  private normalizeBackendData(
    res: any,
    range: DateRangeKey,
  ): AdminDashboardData {
    if (!res || !res.onboarding || !res.plan_purchases) {
      throw new Error(
        'Invalid or empty analytics response received from server.',
      );
    }

    const tierSummaries: PlanTierSummary[] = [
      {
        name: 'Basic Plan',
        key: 'Basic',
        color: '#0284c7',
        lightBg: 'rgba(2, 132, 199, 0.1)',
        borderColor: '#38bdf8',
        totalPurchases: 0,
        sharePercentage: 0,
        averagePrice: 499,
        visible: true,
      },
      {
        name: 'Standard Plan',
        key: 'Standard',
        color: '#6366f1',
        lightBg: 'rgba(99, 102, 241, 0.1)',
        borderColor: '#818cf8',
        totalPurchases: 0,
        sharePercentage: 0,
        averagePrice: 1499,
        visible: true,
      },
      {
        name: 'Premium Plan',
        key: 'Premium',
        color: '#059669',
        lightBg: 'rgba(5, 150, 105, 0.1)',
        borderColor: '#34d399',
        totalPurchases: 0,
        sharePercentage: 0,
        averagePrice: 3499,
        visible: true,
      },
      {
        name: 'Enterprise Plan',
        key: 'Enterprise',
        color: '#d97706',
        lightBg: 'rgba(217, 119, 6, 0.1)',
        borderColor: '#fbbf24',
        totalPurchases: 0,
        sharePercentage: 0,
        averagePrice: 8999,
        visible: true,
      },
    ];

    const rawPlans: Record<string, number[]> = res.plan_purchases.plans || {};
    let totalPurchasesAll = 0;

    tierSummaries.forEach((tier) => {
      const counts: number[] = rawPlans[tier.key] || [];
      const sum = counts.reduce((acc, c) => acc + c, 0);
      tier.totalPurchases = sum;
      totalPurchasesAll += sum;
    });

    tierSummaries.forEach((tier) => {
      tier.sharePercentage =
        totalPurchasesAll > 0
          ? Math.round((tier.totalPurchases / totalPurchasesAll) * 100)
          : 0;
    });

    const onboardingCounts: number[] = res.onboarding.counts || [];
    const peak = onboardingCounts.length ? Math.max(...onboardingCounts) : 0;
    const peakIdx = onboardingCounts.indexOf(peak);
    const peakDate =
      peakIdx >= 0 && res.onboarding.display_dates?.[peakIdx]
        ? res.onboarding.display_dates[peakIdx]
        : 'N/A';

    return {
      range,
      summary: {
        totalUsers: res.summary?.total_users ?? 0,
        totalUsersGrowthPct: res.summary?.total_users_growth_pct ?? 0,
        newUsers: res.summary?.new_users ?? 0,
        newUsersGrowthPct: res.summary?.new_users_growth_pct ?? 0,
        totalPlanPurchases:
          res.summary?.total_plan_purchases ?? totalPurchasesAll,
        totalPlanPurchasesGrowthPct:
          res.summary?.total_plan_purchases_growth_pct ?? 0,
        activeSubscriptions: res.summary?.active_subscriptions ?? 0,
        activeSubscriptionsGrowthPct:
          res.summary?.active_subscriptions_growth_pct ?? 0,
        totalRevenue: res.summary?.total_revenue ?? 0,
        conversionRate: res.summary?.conversion_rate ?? 0,
      },
      onboarding: {
        dates: res.onboarding.dates || [],
        displayDates:
          res.onboarding.display_dates || res.onboarding.labels || [],
        fullDates: res.onboarding.full_dates || [],
        counts: onboardingCounts,
        cumulative: res.onboarding.cumulative || [],
        peakCount: res.onboarding.peak_count ?? peak,
        peakDate: res.onboarding.peak_date || peakDate,
        avgDaily: res.onboarding.avg_daily ?? 0,
        totalInRange: res.onboarding.total_in_range ?? 0,
      },
      planPurchases: {
        dates: res.plan_purchases.dates || [],
        displayDates:
          res.plan_purchases.display_dates || res.plan_purchases.labels || [],
        fullDates: res.plan_purchases.full_dates || [],
        plans: rawPlans,
        totals: res.plan_purchases.totals || [],
        tierSummaries,
      },
    };
  }
}
