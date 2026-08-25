import { Component, OnInit } from '@angular/core';
import { AnalyticsService } from '../../services/analytics.service';
import { AuthService } from '../../services/auth.service';
import { AnalyticsData, AnalyticsAgentStats, AnalyticsFunnelStage } from '../../models/analytics.models';
import { SharedModule } from '../../shared/shared.module';

interface LeadSegment {
  label: string;
  count: number;
  percentage: number;
  color: string;
  bgClass: string;
  textClass: string;
  borderClass: string;
  icon: string;
}

@Component({
  selector: 'app-analytics',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './analytics.component.html',
  styleUrl: './analytics.component.scss'
})
export class AnalyticsComponent implements OnInit {
  analyticsData: AnalyticsData | null = null;
  isLoading = true;
  selectedDays = 7;
  selectedDateRange: Date[] | null = null;
  isTrendLoading = false;
  private funnelLoaded = false;

  // Header Greetings & User
  greeting = '';
  userName = 'Admin';
  currentDate = '';

  // Lead Temperature & Status segments
  leadSegments: LeadSegment[] = [];
  channelBreakdown: { name: string; count: number; percentage: number; color: string; icon: string }[] = [];
  agentsList: AnalyticsAgentStats[] = [];
  funnelSteps: { label: string; count: number; percentage: number; color: string }[] = [];

  // Chart datasets
  dailyTrendChartData: any;
  leadStatusDoughnutData: any;
  channelDoughnutData: any;
  callOutcomeChartData: any;
  aiContainmentChartData: any;

  // Chart Options
  lineChartOptions: any;
  doughnutChartOptions: any;
  barChartOptions: any;

  constructor(
    private analyticsService: AnalyticsService,
    private authService: AuthService
  ) {}

  ngOnInit(): void {
    this.setGreeting();
    this.loadUser();
    this.setupChartOptions();
    this.loadAnalytics(this.selectedDays);
    this.loadFunnel();
  }

  private setGreeting(): void {
    const hour = new Date().getHours();
    if (hour < 12) this.greeting = 'Good morning';
    else if (hour < 17) this.greeting = 'Good afternoon';
    else this.greeting = 'Good evening';

    this.currentDate = new Date().toLocaleDateString('en-US', {
      weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'
    });
  }

  private loadUser(): void {
    this.authService.currentUser$.subscribe(user => {
      if (user) {
        this.userName = user.full_name || user.email?.split('@')[0] || 'Admin';
      }
    });
  }

  setupChartOptions(): void {
    const textColor = '#64748b';
    const gridColor = 'rgba(148, 163, 184, 0.12)';

    this.lineChartOptions = {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: 'index',
        intersect: false
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
            padding: 16,
            font: { size: 12, weight: '600' }
          }
        },
        tooltip: {
          padding: 12,
          cornerRadius: 10,
          backgroundColor: 'rgba(15, 23, 42, 0.9)',
          titleColor: '#fff',
          bodyColor: '#cbd5e1',
          borderColor: 'rgba(255, 255, 255, 0.1)',
          borderWidth: 1
        }
      },
      scales: {
        x: {
          ticks: {
            color: textColor,
            font: { size: 11, weight: '500' },
            autoSkip: true,
            maxRotation: 0
          },
          grid: { display: false }
        },
        y: {
          beginAtZero: true,
          grace: '10%',
          ticks: { color: textColor, font: { size: 11, weight: '500' }, precision: 0 },
          grid: { color: gridColor }
        }
      }
    };

    this.doughnutChartOptions = {
      responsive: true,
      maintainAspectRatio: true,
      aspectRatio: 1,
      cutout: '68%',
      plugins: {
        legend: {
          display: false
        },
        tooltip: {
          padding: 10,
          cornerRadius: 8,
          backgroundColor: 'rgba(15, 23, 42, 0.9)',
          titleColor: '#fff',
          bodyColor: '#cbd5e1'
        }
      },
      layout: {
        padding: 4
      }
    };

    this.barChartOptions = {
      responsive: true,
      maintainAspectRatio: false,
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
            padding: 14,
            font: { size: 11, weight: '600' }
          }
        },
        tooltip: {
          padding: 10,
          cornerRadius: 8,
          backgroundColor: 'rgba(15, 23, 42, 0.9)'
        }
      },
      scales: {
        x: {
          ticks: { color: textColor, font: { size: 11 } },
          grid: { display: false }
        },
        y: {
          beginAtZero: true,
          grace: '10%',
          ticks: { color: textColor, font: { size: 11 }, precision: 0 },
          grid: { color: gridColor }
        }
      }
    };
  }

  setTrendPeriod(days: number): void {
    if (this.selectedDays === days && this.dailyTrendChartData) return;
    this.selectedDays = days;
    this.isTrendLoading = true;
    this.analyticsService.getAnalytics(days).subscribe({
      next: (data: AnalyticsData) => {
        if (data && data.daily) {
          this.dailyTrendChartData = this.buildTrendChartData(data.daily);
        }
        this.isTrendLoading = false;
      },
      error: () => {
        this.isTrendLoading = false;
      }
    });
  }

  setPeriod(days: number): void {
    this.setTrendPeriod(days);
  }

  refreshAll(): void {
    this.loadAnalytics(this.selectedDays);
    this.loadFunnel();
  }

  loadAnalytics(days: number = 7): void {
    this.isLoading = true;
    this.analyticsService.getAnalytics(days).subscribe({
      next: (data: AnalyticsData) => {
        this.analyticsData = data;
        this.processAnalyticsData(data);
        if (!this.funnelLoaded || this.funnelSteps.length === 0) {
          this.computeFallbackFunnel();
        }
        this.isLoading = false;
      },
      error: () => {
        this.isLoading = false;
      }
    });
  }

  loadFunnel(): void {
    this.analyticsService.getFunnel().subscribe({
      next: (funnel: AnalyticsFunnelStage[]) => {
        const colors = ['#6366f1', '#8b5cf6', '#a855f7', '#ec4899', '#10b981'];
        if (funnel && funnel.length > 0) {
          this.funnelSteps = funnel.map((step, idx) => ({
            label: step.stage,
            count: step.count,
            percentage: step.percentage,
            color: colors[idx % colors.length]
          }));
          this.funnelLoaded = true;
        } else {
          this.computeFallbackFunnel();
        }
      },
      error: () => {
        this.computeFallbackFunnel();
      }
    });
  }

  private computeFallbackFunnel(): void {
    if (!this.analyticsData) return;
    const total = this.analyticsData.total_leads || 0;
    const denominator = total || 1;
    const warmOrHot = (this.analyticsData.warm || 0) + (this.analyticsData.hot || 0);
    const qualified = this.analyticsData.qualified || 0;
    const assigned = this.analyticsData.assigned || 0;
    const closed = this.analyticsData.closed || 0;

    this.funnelSteps = [
      { label: 'Total Leads', count: total, percentage: 100, color: '#6366f1' },
      { label: 'Warm / Hot Interest', count: warmOrHot, percentage: Math.min(100, Math.round((warmOrHot / denominator) * 100)), color: '#8b5cf6' },
      { label: 'Assigned to Agent', count: assigned, percentage: Math.min(100, Math.round((assigned / denominator) * 100)), color: '#a855f7' },
      { label: 'Qualified Opportunities', count: qualified, percentage: Math.min(100, Math.round((qualified / denominator) * 100)), color: '#ec4899' },
      { label: 'Closed Deals', count: closed, percentage: Math.min(100, Math.round((closed / denominator) * 100)), color: '#10b981' }
    ];
    this.funnelLoaded = true;
  }

  private processAnalyticsData(data: AnalyticsData): void {
    const totalLeads = data.total_leads || 1;

    // 1. Temperature & Quality Breakdown
    this.leadSegments = [
      {
        label: 'Hot Leads',
        count: data.hot || 0,
        percentage: Math.round(((data.hot || 0) / totalLeads) * 100),
        color: '#ef4444',
        bgClass: 'bg-rose-500/10 text-rose-500 border-rose-500/20',
        textClass: 'text-rose-500',
        borderClass: 'border-rose-500',
        icon: 'pi pi-bolt'
      },
      {
        label: 'Warm Leads',
        count: data.warm || 0,
        percentage: Math.round(((data.warm || 0) / totalLeads) * 100),
        color: '#f59e0b',
        bgClass: 'bg-amber-500/10 text-amber-500 border-amber-500/20',
        textClass: 'text-amber-500',
        borderClass: 'border-amber-500',
        icon: 'pi pi-sun'
      },
      {
        label: 'Cold Leads',
        count: data.cold || 0,
        percentage: Math.round(((data.cold || 0) / totalLeads) * 100),
        color: '#0ea5e9',
        bgClass: 'bg-sky-500/10 text-sky-500 border-sky-500/20',
        textClass: 'text-sky-500',
        borderClass: 'border-sky-500',
        icon: 'pi pi-snowflake'
      },
      {
        label: 'Qualified',
        count: data.qualified || 0,
        percentage: Math.round(((data.qualified || 0) / totalLeads) * 100),
        color: '#10b981',
        bgClass: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20',
        textClass: 'text-emerald-500',
        borderClass: 'border-emerald-500',
        icon: 'pi pi-verified'
      },
      {
        label: 'Needs Human',
        count: data.needs_human || 0,
        percentage: Math.round(((data.needs_human || 0) / totalLeads) * 100),
        color: '#8b5cf6',
        bgClass: 'bg-purple-500/10 text-purple-500 border-purple-500/20',
        textClass: 'text-purple-500',
        borderClass: 'border-purple-500',
        icon: 'pi pi-user-edit'
      },
      {
        label: 'Closed Deals',
        count: data.closed || 0,
        percentage: Math.round(((data.closed || 0) / totalLeads) * 100),
        color: '#06b6d4',
        bgClass: 'bg-cyan-500/10 text-cyan-500 border-cyan-500/20',
        textClass: 'text-cyan-500',
        borderClass: 'border-cyan-500',
        icon: 'pi pi-check-circle'
      }
    ];

    // 2. Channels Breakdown
    const channelIcons: Record<string, { icon: string; color: string }> = {
      voice: { icon: 'pi pi-phone', color: '#6366f1' },
      web: { icon: 'pi pi-globe', color: '#10b981' },
      messenger: { icon: 'pi pi-comments', color: '#0ea5e9' },
      email: { icon: 'pi pi-envelope', color: '#f59e0b' },
      whatsapp: { icon: 'pi pi-whatsapp', color: '#22c55e' }
    };

    const channels = data.channels || {};
    const totalChannelLeads = Object.values(channels).reduce((a, b) => a + b, 0) || 1;

    this.channelBreakdown = Object.entries(channels).map(([key, val]) => {
      const info = channelIcons[key.toLowerCase()] || { icon: 'pi pi-share-alt', color: '#8b5cf6' };
      return {
        name: key.toUpperCase(),
        count: val,
        percentage: Math.round((val / totalChannelLeads) * 100),
        color: info.color,
        icon: info.icon
      };
    });

    // 3. Agents Data
    this.agentsList = data.agents || [];

    // 4. Daily Trends Line Chart (Aggregated to 1-week intervals for 90 days)
    this.dailyTrendChartData = this.buildTrendChartData(data.daily || []);

    // 5. Lead Temperature Doughnut Chart
    this.leadStatusDoughnutData = {
      labels: ['Hot', 'Warm', 'Cold', 'Qualified', 'Needs Human'],
      datasets: [
        {
          data: [
            data.hot || 0,
            data.warm || 0,
            data.cold || 0,
            data.qualified || 0,
            data.needs_human || 0
          ],
          backgroundColor: ['#ef4444', '#f59e0b', '#0ea5e9', '#10b981', '#8b5cf6'],
          hoverBackgroundColor: ['#dc2626', '#d97706', '#0284c7', '#059669', '#7c3aed'],
          borderWidth: 2,
          borderColor: 'transparent'
        }
      ]
    };

    // 6. Channel Source Doughnut Chart
    this.channelDoughnutData = {
      labels: this.channelBreakdown.map(c => c.name),
      datasets: [
        {
          data: this.channelBreakdown.map(c => c.count),
          backgroundColor: this.channelBreakdown.map(c => c.color),
          borderWidth: 2,
          borderColor: 'transparent'
        }
      ]
    };

    // 7. AI Containment vs Human Escalation
    const totalCalls = data.calls || 0;
    const aiRate = data.ai_containment_rate || 0;
    const aiHandledCalls = Math.round((aiRate / 100) * totalCalls);
    const humanEscalated = Math.max(0, totalCalls - aiHandledCalls);

    this.aiContainmentChartData = {
      labels: ['AI Autonomous Handled', 'Human Agent Escalated'],
      datasets: [
        {
          data: [aiHandledCalls || 1, humanEscalated || 0],
          backgroundColor: ['#8b5cf6', '#cbd5e1'],
          hoverBackgroundColor: ['#7c3aed', '#94a3b8'],
          borderWidth: 0
        }
      ]
    };

    // 8. Calls Outcome Comparison Bar
    this.callOutcomeChartData = {
      labels: ['Calls Overview'],
      datasets: [
        {
          label: 'Completed',
          data: [data.completed_calls || 0],
          backgroundColor: '#10b981',
          borderRadius: 6
        },
        {
          label: 'Failed / Missed',
          data: [data.failed_calls || 0],
          backgroundColor: '#ef4444',
          borderRadius: 6
        },
        {
          label: 'Total Volume',
          data: [data.calls || 0],
          backgroundColor: '#6366f1',
          borderRadius: 6
        }
      ]
    };
  }

  private buildTrendChartData(dailyData: any[]): any {
    if (!dailyData || dailyData.length === 0) {
      return { labels: [], datasets: [] };
    }

    if (this.selectedDays === 90 && dailyData.length > 14) {
      // Group daily records into 1-week (7 days) bins
      const weekLabels: string[] = [];
      const weekCalls: number[] = [];
      const weekLeads: number[] = [];
      const weekHot: number[] = [];

      const chunkSize = 7;
      for (let i = 0; i < dailyData.length; i += chunkSize) {
        const chunk = dailyData.slice(i, i + chunkSize);
        const startLabel = this.formatDateLabel(chunk[0].date);
        const endLabel = this.formatDateLabel(chunk[chunk.length - 1].date);
        
        // Label format: "May 21 - 27", "May 28 - Jun 3"
        const label = chunk.length > 1 ? `${startLabel} - ${endLabel}` : startLabel;
        weekLabels.push(label);

        const totalCalls = chunk.reduce((sum, d) => sum + (d.calls || 0), 0);
        const totalLeads = chunk.reduce((sum, d) => sum + (d.leads || 0), 0);
        const totalHot = chunk.reduce((sum, d) => sum + (d.hot || 0), 0);

        weekCalls.push(totalCalls);
        weekLeads.push(totalLeads);
        weekHot.push(totalHot);
      }

      return {
        labels: weekLabels,
        datasets: [
          {
            label: 'Weekly Calls',
            data: weekCalls,
            borderColor: '#6366f1',
            backgroundColor: 'rgba(99, 102, 241, 0.12)',
            fill: true,
            tension: 0.4,
            pointRadius: 4,
            pointHoverRadius: 6,
            pointBackgroundColor: '#6366f1'
          },
          {
            label: 'New Leads',
            data: weekLeads,
            borderColor: '#10b981',
            backgroundColor: 'rgba(16, 185, 129, 0.08)',
            fill: true,
            tension: 0.4,
            pointRadius: 4,
            pointHoverRadius: 6,
            pointBackgroundColor: '#10b981'
          },
          {
            label: 'Hot Leads',
            data: weekHot,
            borderColor: '#ef4444',
            backgroundColor: 'transparent',
            borderDash: [4, 4],
            fill: false,
            tension: 0.3,
            pointRadius: 3,
            pointHoverRadius: 5,
            pointBackgroundColor: '#ef4444'
          }
        ]
      };
    }

    // Default: Day by day points for 7D, 14D, 30D
    const dates = dailyData.map(d => this.formatDateLabel(d.date));
    const dailyCalls = dailyData.map(d => d.calls);
    const dailyLeads = dailyData.map(d => d.leads);
    const dailyHot = dailyData.map(d => d.hot || 0);

    return {
      labels: dates,
      datasets: [
        {
          label: 'Total Calls',
          data: dailyCalls,
          borderColor: '#6366f1',
          backgroundColor: 'rgba(99, 102, 241, 0.12)',
          fill: true,
          tension: 0.4,
          pointRadius: 4,
          pointHoverRadius: 6,
          pointBackgroundColor: '#6366f1'
        },
        {
          label: 'New Leads',
          data: dailyLeads,
          borderColor: '#10b981',
          backgroundColor: 'rgba(16, 185, 129, 0.08)',
          fill: true,
          tension: 0.4,
          pointRadius: 4,
          pointHoverRadius: 6,
          pointBackgroundColor: '#10b981'
        },
        {
          label: 'Hot Leads',
          data: dailyHot,
          borderColor: '#ef4444',
          backgroundColor: 'transparent',
          borderDash: [4, 4],
          fill: false,
          tension: 0.3,
          pointRadius: 3,
          pointHoverRadius: 5,
          pointBackgroundColor: '#ef4444'
        }
      ]
    };
  }

  private formatDateLabel(dateStr: string): string {
    if (!dateStr) return '';
    try {
      const parts = dateStr.split('-');
      if (parts.length === 3) {
        const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        const mIdx = parseInt(parts[1], 10) - 1;
        return `${monthNames[mIdx] || ''} ${parseInt(parts[2], 10)}`;
      }
      return dateStr;
    } catch {
      return dateStr;
    }
  }

  formatDuration(seconds: number): string {
    if (!seconds || seconds <= 0) return '0s';
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    if (m === 0) return `${s}s`;
    return `${m}m ${s}s`;
  }
}
