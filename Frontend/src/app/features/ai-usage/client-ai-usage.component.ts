import {
  Component,
  OnInit,
  OnDestroy,
  inject,
  ChangeDetectionStrategy,
  ChangeDetectorRef,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subject, takeUntil } from 'rxjs';

import {
  AiUsageService,
  ClientAiUsageResponse,
  ProcessUsageRow,
  DailyTrendPoint,
} from '../../services/ai-usage.service';
import { ToastService } from '../../shared/services/toast.service';

// PrimeNG
import { TableModule } from 'primeng/table';
import { ButtonModule } from 'primeng/button';
import { TagModule } from 'primeng/tag';
import { SkeletonModule } from 'primeng/skeleton';
import { TooltipModule } from 'primeng/tooltip';
import { DatePickerModule } from 'primeng/datepicker';
import { ProgressBarModule } from 'primeng/progressbar';

function today(): Date {
  return new Date();
}
function daysAgo(n: number): Date {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d;
}
function toIsoDate(d: Date): string {
  return d.toISOString().split('T')[0];
}

@Component({
  selector: 'app-client-ai-usage',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    FormsModule,
    TableModule,
    ButtonModule,
    TagModule,
    SkeletonModule,
    TooltipModule,
    DatePickerModule,
    ProgressBarModule,
  ],
  templateUrl: './client-ai-usage.component.html',
  styleUrl: './client-ai-usage.component.scss',
})
export class ClientAiUsageComponent implements OnInit, OnDestroy {
  private usageService = inject(AiUsageService);
  private toastService = inject(ToastService);
  private cdr = inject(ChangeDetectorRef);
  private destroy$ = new Subject<void>();

  startDate: Date = daysAgo(30);
  endDate: Date = today();

  loading = false;
  data: ClientAiUsageResponse | null = null;

  // Expose Math for template
  Math = Math;

  // Sparkline
  chartPoints: string = '';
  chartMax = 1;

  ngOnInit(): void {
    this.load();
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  load(): void {
    this.loading = true;
    this.cdr.markForCheck();

    this.usageService
      .getClientUsage({
        start_date: toIsoDate(this.startDate),
        end_date: toIsoDate(this.endDate),
      })
      .pipe(takeUntil(this.destroy$))
      .subscribe({
        next: (res: ClientAiUsageResponse) => {
          this.data = res;
          this.buildSparkline(res.daily_trend);
          this.loading = false;
          this.cdr.markForCheck();
        },
        error: (err: any) => {
          this.loading = false;
          this.toastService.error(
            'Failed to load AI usage data: ' + (err?.error?.detail ?? err?.message ?? 'Unknown error')
          );
          this.cdr.markForCheck();
        },
      });
  }

  resetFilters(): void {
    this.startDate = daysAgo(30);
    this.endDate = today();
    this.load();
  }

  private buildSparkline(trend: DailyTrendPoint[]): void {
    if (!trend || trend.length === 0) { this.chartPoints = ''; return; }
    const vals = trend.map((d) => d.tokens);
    this.chartMax = Math.max(...vals, 1);
    const W = 600; const H = 70;
    const step = W / Math.max(trend.length - 1, 1);
    this.chartPoints = trend
      .map((d, i) => `${(i * step).toFixed(1)},${(H - (d.tokens / this.chartMax) * H).toFixed(1)}`)
      .join(' ');
  }

  get summary() { return this.data?.summary; }
  get byProcess(): ProcessUsageRow[] { return this.data?.by_process ?? []; }
  get dailyTrend(): DailyTrendPoint[] { return this.data?.daily_trend ?? []; }
  get showCost(): boolean { return this.data?.show_cost ?? false; }

  /** Percentage of total tokens for a given process (for inline bar) */
  processTokenPct(row: ProcessUsageRow): number {
    const total = this.summary?.total_tokens ?? 0;
    return total > 0 ? Math.round((row.total_tokens / total) * 100) : 0;
  }

  fmtTokens(n: number): string {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
    if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k';
    return String(n);
  }

  fmtCost(usd: number | null): string {
    if (usd === null || usd === undefined) return '—';
    return '$' + usd.toFixed(4);
  }

  categoryColor(cat: string): string {
    const map: Record<string, string> = {
      voice: '#06b6d4',
      chat: '#6366f1',
      content: '#8b5cf6',
      research: '#f59e0b',
      general: '#6b7280',
    };
    return map[cat] ?? '#6b7280';
  }
}
