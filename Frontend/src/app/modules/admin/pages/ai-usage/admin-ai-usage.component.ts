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
  AdminAiUsageResponse,
  CompanyUsageRow,
  ProcessUsageRow,
  ModelUsageRow,
  DailyTrendPoint,
} from '../../../../services/ai-usage.service';
import { ToastService } from '../../../../shared/services/toast.service';

// PrimeNG
import { TableModule } from 'primeng/table';
import { ButtonModule } from 'primeng/button';
import { TagModule } from 'primeng/tag';
import { SkeletonModule } from 'primeng/skeleton';
import { TooltipModule } from 'primeng/tooltip';
import { SelectModule } from 'primeng/select';
import { DatePickerModule } from 'primeng/datepicker';
import { IconFieldModule } from 'primeng/iconfield';
import { InputIconModule } from 'primeng/inputicon';
import { InputTextModule } from 'primeng/inputtext';

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
  selector: 'app-admin-ai-usage',
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
    SelectModule,
    DatePickerModule,
    IconFieldModule,
    InputIconModule,
    InputTextModule,
  ],
  templateUrl: './admin-ai-usage.component.html',
  styleUrl: './admin-ai-usage.component.scss',
})
export class AdminAiUsageComponent implements OnInit, OnDestroy {
  private usageService = inject(AiUsageService);
  private toastService = inject(ToastService);
  private cdr = inject(ChangeDetectorRef);
  private destroy$ = new Subject<void>();

  // Expose Math to template
  Math = Math;

  // ── Filter state ──────────────────────────────────────────────────────────
  startDate: Date = daysAgo(30);
  endDate: Date = today();
  filterClientId: string = '';

  // ── Data state ────────────────────────────────────────────────────────────
  loading = false;
  data: AdminAiUsageResponse | null = null;

  // ── Tabs ──────────────────────────────────────────────────────────────────
  activeTab: 'companies' | 'process' | 'model' | 'trend' = 'companies';

  // ── Sorting ───────────────────────────────────────────────────────────────
  companySortField = 'total_cost_usd';
  companySortOrder = -1;

  // ── Chart (mini sparkline via inline SVG) ─────────────────────────────────
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
    this.data = null;
    this.cdr.markForCheck();

    this.usageService
      .getAdminUsage({
        start_date: toIsoDate(this.startDate),
        end_date: toIsoDate(this.endDate),
        client_id: this.filterClientId || undefined,
      })
      .pipe(takeUntil(this.destroy$))
      .subscribe({
        next: (res: AdminAiUsageResponse) => {
          this.data = res;
          this.buildSparkline(res.daily_trend);
          this.loading = false;
          this.cdr.markForCheck();
        },
        error: (err: any) => {
          this.loading = false;
          this.toastService.error(
            'Failed to load AI usage data: ' + (err?.error?.detail ?? err.message)
          );
          this.cdr.markForCheck();
        },
      });
  }

  resetFilters(): void {
    this.startDate = daysAgo(30);
    this.endDate = today();
    this.filterClientId = '';
    this.load();
  }

  setTab(tab: 'companies' | 'process' | 'model' | 'trend'): void {
    this.activeTab = tab;
  }

  // ── Sparkline ─────────────────────────────────────────────────────────────
  private buildSparkline(trend: DailyTrendPoint[]): void {
    if (!trend || trend.length === 0) {
      this.chartPoints = '';
      this.chartMax = 1;
      return;
    }
    const vals = trend.map((d) => d.tokens);
    this.chartMax = Math.max(...vals, 1);
    const W = 600;
    const H = 80;
    const step = W / Math.max(trend.length - 1, 1);
    this.chartPoints = trend
      .map((d, i) => `${(i * step).toFixed(1)},${(H - (d.tokens / this.chartMax) * H).toFixed(1)}`)
      .join(' ');
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  get summary() {
    return this.data?.summary;
  }
  get companies(): CompanyUsageRow[] {
    return this.data?.companies ?? [];
  }
  get byProcess(): ProcessUsageRow[] {
    return this.data?.by_process ?? [];
  }
  get byModel(): ModelUsageRow[] {
    return this.data?.by_model ?? [];
  }
  get dailyTrend(): DailyTrendPoint[] {
    return this.data?.daily_trend ?? [];
  }

  fmtTokens(n: number): string {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
    if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k';
    return String(n);
  }

  fmtCost(usd: number): string {
    if (usd === null || usd === undefined) return '—';
    return '$' + usd.toFixed(4);
  }

  categoryColor(cat: string): string {
    const map: Record<string, string> = {
      voice: 'var(--color-accent)',
      chat: 'var(--color-primary)',
      content: '#8b5cf6',
      research: '#f59e0b',
      general: '#6b7280',
    };
    return map[cat] ?? '#6b7280';
  }
}
