import { Component, Input, OnInit, OnDestroy, inject, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { TooltipModule } from 'primeng/tooltip';
import { DialogModule } from 'primeng/dialog';
import { SharedModule as PrimeSharedModule } from 'primeng/api';
import { ActivityService, ActivityQueryParams } from '../../../services/activity.service';
import { ActivityLogItem } from '../../../models/activity.models';

@Component({
  selector: 'app-module-activity-logs',
  standalone: true,
  imports: [CommonModule, FormsModule, TooltipModule, DialogModule, PrimeSharedModule],
  templateUrl: './module-activity-logs.component.html',
  styleUrl: './module-activity-logs.component.scss',
})
export class ModuleActivityLogsComponent implements OnInit, OnDestroy {
  @Input() entityType?: string;
  @Input() actionPrefix?: string;
  @Input() title: string = 'Activity & Automation Logs';
  @Input() subtitle: string = 'Real-time chronological audit trail of automated background jobs and user actions.';

  private activityService = inject(ActivityService);
  private cdr = inject(ChangeDetectorRef);

  // State
  logs: ActivityLogItem[] = [];
  loading = false;
  totalRecords = 0;
  page = 1;
  pageSize = 20;

  // Filters
  searchQuery: string = '';
  selectedLogType: string = 'ALL';
  selectedAction: string = 'ALL';
  availableActions: string[] = [];

  // Auto-refresh state
  autoRefreshEnabled = false;
  private autoRefreshTimer: any = null;

  // Metadata Detail Modal
  selectedLog: ActivityLogItem | null = null;
  showDetailModal = false;

  ngOnInit(): void {
    this.loadAvailableActions();
    this.loadLogs();
  }

  ngOnDestroy(): void {
    this.stopAutoRefresh();
  }

  loadAvailableActions(): void {
    this.activityService.getActions().subscribe({
      next: (actions) => {
        if (this.actionPrefix) {
          this.availableActions = (actions || []).filter((a) => a.startsWith(this.actionPrefix!));
        } else if (this.entityType) {
          this.availableActions = (actions || []).filter((a) => a.startsWith(this.entityType!));
        } else {
          this.availableActions = actions || [];
        }
        this.cdr.markForCheck();
      },
      error: () => {},
    });
  }

  loadLogs(): void {
    this.loading = true;
    const params: ActivityQueryParams = {
      page: this.page,
      page_size: this.pageSize,
    };

    if (this.entityType) {
      params.entity_type = this.entityType;
    }
    if (this.actionPrefix && this.selectedAction === 'ALL') {
      params.action_prefix = this.actionPrefix;
    }
    if (this.selectedAction && this.selectedAction !== 'ALL') {
      params.action = this.selectedAction;
    }
    if (this.selectedLogType && this.selectedLogType !== 'ALL') {
      params.log_type = this.selectedLogType;
    }

    this.activityService.getActivity(params).subscribe({
      next: (res) => {
        this.logs = res.items || [];
        this.totalRecords = res.total_items || 0;
        this.loading = false;
        this.cdr.markForCheck();
      },
      error: (err) => {
        console.error('[ModuleActivityLogs] Error loading logs:', err);
        this.loading = false;
        this.cdr.markForCheck();
      },
    });
  }

  onFilterChange(): void {
    this.page = 1;
    this.loadLogs();
  }

  onPageChange(newPage: number): void {
    this.page = newPage;
    this.loadLogs();
  }

  toggleAutoRefresh(): void {
    this.autoRefreshEnabled = !this.autoRefreshEnabled;
    if (this.autoRefreshEnabled) {
      this.autoRefreshTimer = setInterval(() => {
        this.loadLogs();
      }, 10000);
    } else {
      this.stopAutoRefresh();
    }
  }

  private stopAutoRefresh(): void {
    if (this.autoRefreshTimer) {
      clearInterval(this.autoRefreshTimer);
      this.autoRefreshTimer = null;
    }
  }

  get filteredLogs(): ActivityLogItem[] {
    if (!this.searchQuery.trim()) {
      return this.logs;
    }
    const q = this.searchQuery.toLowerCase().trim();
    return this.logs.filter(
      (item) =>
        (item.message || '').toLowerCase().includes(q) ||
        (item.action || '').toLowerCase().includes(q) ||
        (item.actor_email || '').toLowerCase().includes(q) ||
        (item.entity_id || '').toLowerCase().includes(q)
    );
  }

  viewDetails(item: ActivityLogItem): void {
    this.selectedLog = item;
    this.showDetailModal = true;
  }

  closeDetailModal(): void {
    this.showDetailModal = false;
    this.selectedLog = null;
  }

  formatActionName(action: string): string {
    if (!action) return 'Action';
    const parts = action.split('.');
    const main = parts.length > 1 ? parts.slice(1).join(' ') : action;
    return main
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  getSeverityTag(logType: string): 'success' | 'info' | 'warn' | 'danger' | 'secondary' {
    const t = (logType || '').toLowerCase();
    if (t === 'error' || t === 'security') return 'danger';
    if (t === 'warning' || t === 'warn') return 'warn';
    if (t === 'success') return 'success';
    return 'info';
  }

  getSeverityIcon(logType: string): string {
    const t = (logType || '').toLowerCase();
    if (t === 'error' || t === 'security') return 'pi pi-times-circle text-rose-500';
    if (t === 'warning' || t === 'warn') return 'pi pi-exclamation-triangle text-amber-500';
    if (t === 'success') return 'pi pi-check-circle text-emerald-500';
    return 'pi pi-info-circle text-sky-500';
  }

  getActionBadgeColor(action: string): string {
    if (!action) return 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300';
    const a = action.toLowerCase();
    if (a.includes('fail') || a.includes('error') || a.includes('reject')) {
      return 'bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300 border-red-200 dark:border-red-900';
    }
    if (a.includes('publish') || a.includes('accept') || a.includes('success') || a.includes('approve')) {
      return 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300 border-emerald-200 dark:border-emerald-900';
    }
    if (a.includes('schedul') || a.includes('auto') || a.includes('generate')) {
      return 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/40 dark:text-indigo-300 border-indigo-200 dark:border-indigo-900';
    }
    if (a.includes('search') || a.includes('connect') || a.includes('reply') || a.includes('message')) {
      return 'bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300 border-blue-200 dark:border-blue-900';
    }
    return 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300 border-slate-200 dark:border-slate-700';
  }

  hasMeta(item: ActivityLogItem): boolean {
    return !!item.meta && Object.keys(item.meta).length > 0;
  }

  formatJson(obj: any): string {
    try {
      return JSON.stringify(obj, null, 2);
    } catch {
      return String(obj);
    }
  }
}
