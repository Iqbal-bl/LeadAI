import { Component, OnInit, OnDestroy, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import { SharedModule } from '../../../../shared/shared.module';
import { AdminAnalyticsService } from '../../../../services/admin-analytics.service';
import { ThemeService } from '../../../../shared/services/theme.service';
import {
  AdminDashboardData,
  DateRangeKey,
  DateRangeOption,
  PlanTierSummary,
} from '../../../../models/admin-analytics.models';

@Component({
  selector: 'admin-dashboard',
  standalone: true,
  imports: [CommonModule, FormsModule, SharedModule],
  templateUrl: './admin-dashboard.component.html',
  styleUrl: './admin-dashboard.component.scss',
})
export class AdminDashboardComponent implements OnInit, OnDestroy {
  // State management
  isLoading = true;
  hasError = false;
  errorMessage = '';
  dashboardData: AdminDashboardData | null = null;
  lastSyncTime = 'Just now';

  // Filters & Controls
  selectedRange: DateRangeKey = '30d';
  rangeOptions: DateRangeOption[] = [];
  onboardingViewMode: 'daily' | 'cumulative' = 'daily';

  // Chart data objects for PrimeNG p-chart
  onboardingChartData: any = null;
  onboardingChartOptions: any = null;

  planPurchasesChartData: any = null;
  planPurchasesChartOptions: any = null;

  // Active theme tracking
  isDarkMode = false;
  private subs = new Subscription();

  constructor(
    private analyticsService: AdminAnalyticsService,
    private themeService: ThemeService,
    private cdr: ChangeDetectorRef,
  ) {
    this.rangeOptions = this.analyticsService.rangeOptions;
  }

  ngOnInit(): void {
    // Subscribe to theme changes
    this.subs.add(
      this.themeService.darkMode$.subscribe((isDark) => {
        this.isDarkMode = isDark;
        this.rebuildChartOptions();
        this.cdr.markForCheck();
      }),
    );

    // Initial data fetch
    this.loadData();
  }

  ngOnDestroy(): void {
    this.subs.unsubscribe();
  }

  public loadData(): void {
    this.isLoading = true;
    this.hasError = false;
    this.errorMessage = '';

    this.subs.add(
      this.analyticsService.getDashboardData(this.selectedRange).subscribe({
        next: (data) => {
          this.dashboardData = data;
          this.lastSyncTime = new Date().toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
          });
          this.buildCharts();
          this.isLoading = false;
          this.cdr.markForCheck();
        },
        error: (err) => {
          console.error('[AdminDashboard] Error loading analytics data:', err);
          this.hasError = true;
          this.errorMessage =
            err?.message || 'Failed to retrieve analytics data from server.';
          this.isLoading = false;
          this.cdr.markForCheck();
        },
      }),
    );
  }

  public setRange(range: DateRangeKey): void {
    if (this.selectedRange === range && !this.isLoading) return;
    this.selectedRange = range;
    this.loadData();
  }

  public setOnboardingViewMode(mode: 'daily' | 'cumulative'): void {
    this.onboardingViewMode = mode;
    this.buildOnboardingChart();
  }

  public togglePlanVisibility(tier: PlanTierSummary, event?: Event): void {
    if (event) {
      event.stopPropagation();
    }
    tier.visible = !tier.visible;
    this.buildPlanPurchasesChart();
  }

  public get isAllPlansVisible(): boolean {
    if (!this.dashboardData) return true;
    return this.dashboardData.planPurchases.tierSummaries.every(
      (t) => t.visible,
    );
  }

  public toggleAllPlans(): void {
    if (!this.dashboardData) return;
    const makeVisible = !this.isAllPlansVisible;
    this.dashboardData.planPurchases.tierSummaries.forEach(
      (t) => (t.visible = makeVisible),
    );
    this.buildPlanPurchasesChart();
  }

  public resetAllPlansVisible(): void {
    if (!this.dashboardData) return;
    this.dashboardData.planPurchases.tierSummaries.forEach(
      (t) => (t.visible = true),
    );
    this.buildPlanPurchasesChart();
  }

  public retry(): void {
    this.loadData();
  }

  public exportSummaryCsv(): void {
    if (!this.dashboardData) return;
    const ob = this.dashboardData.onboarding;
    const pp = this.dashboardData.planPurchases;

    let csv =
      'Date,New Onboarded Users,Cumulative Users,Basic Purchases,Standard Purchases,Premium Purchases,Enterprise Purchases,Total Purchases\n';

    ob.dates.forEach((date, i) => {
      const basic = pp.plans['Basic']?.[i] ?? 0;
      const standard = pp.plans['Standard']?.[i] ?? 0;
      const premium = pp.plans['Premium']?.[i] ?? 0;
      const enterprise = pp.plans['Enterprise']?.[i] ?? 0;
      const total = pp.totals?.[i] ?? 0;
      const uCount = ob.counts?.[i] ?? 0;
      const cumCount = ob.cumulative?.[i] ?? 0;

      csv += `"${date}",${uCount},${cumCount},${basic},${standard},${premium},${enterprise},${total}\n`;
    });

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.setAttribute('href', url);
    link.setAttribute(
      'download',
      `leadai-analytics-${this.selectedRange}-${new Date().toISOString().slice(0, 10)}.csv`,
    );
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  // ==========================================
  // Chart Builders
  // ==========================================
  private buildCharts(): void {
    if (!this.dashboardData) return;
    this.buildOnboardingChart();
    this.buildPlanPurchasesChart();
  }

  private buildOnboardingChart(): void {
    if (!this.dashboardData) return;
    const ob = this.dashboardData.onboarding;
    const isCumulative = this.onboardingViewMode === 'cumulative';
    const seriesData = (isCumulative ? ob.cumulative : ob.counts) || [];

    const isDark = this.isDarkMode;
    const primaryColor = '#6366f1';
    const fillColor = isDark
      ? 'rgba(99, 102, 241, 0.16)'
      : 'rgba(99, 102, 241, 0.08)';

    this.onboardingChartData = {
      labels: ob.displayDates,
      datasets: [
        {
          label: isCumulative ? 'Cumulative Users' : 'Users Onboarded',
          data: seriesData,
          fill: true,
          borderColor: primaryColor,
          backgroundColor: fillColor,
          tension: 0.38,
          borderWidth: 2.5,
          pointRadius: seriesData.length > 35 ? 0 : 3.5,
          pointHoverRadius: 6,
          pointBackgroundColor: primaryColor,
          pointBorderColor: isDark ? '#0f172a' : '#ffffff',
          pointBorderWidth: 2,
        },
      ],
    };

    this.onboardingChartOptions = this.getOnboardingChartOptions(
      ob.fullDates,
      isCumulative,
    );
  }

  private buildPlanPurchasesChart(): void {
    if (!this.dashboardData) return;
    const pp = this.dashboardData.planPurchases;
    const isDark = this.isDarkMode;

    const datasets = pp.tierSummaries.map((tier) => {
      const counts = pp.plans[tier.key] || [];
      return {
        label: tier.name,
        data: counts,
        hidden: !tier.visible,
        borderColor: tier.color,
        backgroundColor: tier.lightBg,
        fill: false,
        tension: 0.35,
        borderWidth: 2.5,
        pointRadius: counts.length > 35 ? 0 : 3.5,
        pointHoverRadius: 6,
        pointBackgroundColor: tier.color,
        pointBorderColor: isDark ? '#0f172a' : '#ffffff',
        pointBorderWidth: 2,
      };
    });

    this.planPurchasesChartData = {
      labels: pp.displayDates,
      datasets,
    };

    this.planPurchasesChartOptions = this.getPlanPurchasesChartOptions(
      pp.fullDates,
    );
  }

  private rebuildChartOptions(): void {
    if (!this.dashboardData) return;
    const ob = this.dashboardData.onboarding;
    const pp = this.dashboardData.planPurchases;

    this.onboardingChartOptions = this.getOnboardingChartOptions(
      ob.fullDates,
      this.onboardingViewMode === 'cumulative',
    );
    this.planPurchasesChartOptions = this.getPlanPurchasesChartOptions(
      pp.fullDates,
    );

    this.buildCharts();
  }

  // ==========================================
  // Chart Options & Tooltip Handlers
  // ==========================================
  private getOnboardingChartOptions(
    fullDates: string[],
    isCumulative: boolean,
  ): any {
    const isDark = this.isDarkMode;
    const textColor = isDark ? '#cbd5e1' : '#334155';
    const mutedColor = isDark ? '#64748b' : '#94a3b8';
    const gridColor = isDark
      ? 'rgba(255, 255, 255, 0.05)'
      : 'rgba(0, 0, 0, 0.04)';
    const tooltipBg = isDark ? '#0f172a' : '#ffffff';
    const tooltipTitleColor = isDark ? '#f8fafc' : '#0f172a';
    const tooltipBorder = isDark ? '#334155' : '#e2e8f0';

    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: {
        duration: 500,
        easing: 'easeOutQuart',
      },
      interaction: {
        mode: 'index',
        intersect: false,
      },
      plugins: {
        legend: {
          display: false,
        },
        tooltip: {
          enabled: true,
          backgroundColor: tooltipBg,
          titleColor: tooltipTitleColor,
          bodyColor: textColor,
          borderColor: tooltipBorder,
          borderWidth: 1,
          padding: 12,
          boxPadding: 6,
          cornerRadius: 10,
          usePointStyle: true,
          titleFont: {
            family: 'Inter, system-ui, sans-serif',
            weight: '600',
            size: 13,
          },
          bodyFont: {
            family: 'Inter, system-ui, sans-serif',
            size: 12,
          },
          callbacks: {
            title: (items: any[]) => {
              if (!items || !items.length) return '';
              const idx = items[0].dataIndex;
              return fullDates[idx] || items[0].label;
            },
            label: (context: any) => {
              const val = context.parsed.y || 0;
              const prefix = isCumulative
                ? 'Cumulative Users: '
                : 'New Users: ';
              return `${prefix} ${val.toLocaleString()} accounts`;
            },
          },
        },
      },
      scales: {
        x: {
          grid: {
            display: false,
            drawBorder: false,
          },
          ticks: {
            color: mutedColor,
            font: {
              family: 'Inter, system-ui, sans-serif',
              size: 11,
              weight: '500',
            },
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 12,
          },
        },
        y: {
          beginAtZero: true,
          grid: {
            color: gridColor,
            drawBorder: false,
            borderDash: [4, 4],
          },
          ticks: {
            color: mutedColor,
            font: {
              family: 'Inter, system-ui, sans-serif',
              size: 11,
            },
            precision: 0,
            callback: (value: any) => {
              if (value >= 1000) {
                return (value / 1000).toFixed(1) + 'k';
              }
              return value;
            },
          },
        },
      },
    };
  }

  private getPlanPurchasesChartOptions(fullDates: string[]): any {
    const isDark = this.isDarkMode;
    const textColor = isDark ? '#cbd5e1' : '#334155';
    const mutedColor = isDark ? '#64748b' : '#94a3b8';
    const gridColor = isDark
      ? 'rgba(255, 255, 255, 0.05)'
      : 'rgba(0, 0, 0, 0.04)';
    const tooltipBg = isDark ? '#0f172a' : '#ffffff';
    const tooltipTitleColor = isDark ? '#f8fafc' : '#0f172a';
    const tooltipBorder = isDark ? '#334155' : '#e2e8f0';

    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: {
        duration: 500,
        easing: 'easeOutQuart',
      },
      interaction: {
        mode: 'index',
        intersect: false,
      },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          align: 'end',
          labels: {
            color: textColor,
            usePointStyle: true,
            pointStyle: 'circle',
            boxWidth: 8,
            boxHeight: 8,
            padding: 16,
            font: {
              family: 'Inter, system-ui, sans-serif',
              size: 12,
              weight: '500',
            },
          },
          onClick: (e: any, legendItem: any, legend: any) => {
            const ci = legend.chart;
            const index = legendItem.datasetIndex;
            const meta = ci.getDatasetMeta(index);
            meta.hidden =
              meta.hidden === null ? !ci.data.datasets[index].hidden : null;
            ci.update();

            if (this.dashboardData?.planPurchases.tierSummaries[index]) {
              this.dashboardData.planPurchases.tierSummaries[index].visible =
                !meta.hidden;
              this.cdr.markForCheck();
            }
          },
        },
        tooltip: {
          enabled: true,
          backgroundColor: tooltipBg,
          titleColor: tooltipTitleColor,
          bodyColor: textColor,
          borderColor: tooltipBorder,
          borderWidth: 1,
          padding: 12,
          boxPadding: 6,
          cornerRadius: 10,
          usePointStyle: true,
          titleFont: {
            family: 'Inter, system-ui, sans-serif',
            weight: '600',
            size: 13,
          },
          bodyFont: {
            family: 'Inter, system-ui, sans-serif',
            size: 12,
          },
          footerFont: {
            family: 'Inter, system-ui, sans-serif',
            weight: '600',
            size: 12,
          },
          footerColor: isDark ? '#38bdf8' : '#0284c7',
          callbacks: {
            title: (items: any[]) => {
              if (!items || !items.length) return '';
              const idx = items[0].dataIndex;
              return fullDates[idx] || items[0].label;
            },
            label: (context: any) => {
              const val = context.parsed.y || 0;
              return ` ${context.dataset.label}: ${val.toLocaleString()} purchases`;
            },
            footer: (items: any[]) => {
              const total = items.reduce(
                (sum, item) => sum + (item.parsed.y || 0),
                0,
              );
              return `Total Purchases: ${total.toLocaleString()}`;
            },
          },
        },
      },
      scales: {
        x: {
          grid: {
            display: false,
            drawBorder: false,
          },
          ticks: {
            color: mutedColor,
            font: {
              family: 'Inter, system-ui, sans-serif',
              size: 11,
              weight: '500',
            },
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 12,
          },
        },
        y: {
          beginAtZero: true,
          grid: {
            color: gridColor,
            drawBorder: false,
            borderDash: [4, 4],
          },
          ticks: {
            color: mutedColor,
            font: {
              family: 'Inter, system-ui, sans-serif',
              size: 11,
            },
            precision: 0,
            callback: (value: any) => {
              if (value >= 1000) {
                return (value / 1000).toFixed(1) + 'k';
              }
              return value;
            },
          },
        },
      },
    };
  }

  public getSelectedRangeDescription(): string {
    const opt = this.rangeOptions.find((o) => o.value === this.selectedRange);
    return opt ? opt.description : 'Custom time range';
  }
}
