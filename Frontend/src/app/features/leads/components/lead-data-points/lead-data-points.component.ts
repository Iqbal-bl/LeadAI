import { Component, Input, OnInit, OnChanges, SimpleChanges, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { SharedModule } from '../../../../shared/shared.module';
import { DataPointService } from '../../../../services/data-point.service';
import { DataPoint } from '../../../../models/data-point.models';
import { LeadDetail } from '../../../../models/inbox.models';

export interface DataPointDisplayItem {
  key: string;
  label: string;
  data_type: string;
  required: boolean;
  value: any;
  hasValue: boolean;
  formattedValue: string;
  isCustom?: boolean;
}

@Component({
  selector: 'app-lead-data-points',
  standalone: true,
  imports: [CommonModule, SharedModule],
  templateUrl: './lead-data-points.component.html',
  styleUrls: ['./lead-data-points.component.scss'],
})
export class LeadDataPointsComponent implements OnInit, OnChanges {
  @Input() lead?: LeadDetail;

  private dataPointService = inject(DataPointService);

  dataPointsConfig: DataPoint[] = [];
  displayItems: DataPointDisplayItem[] = [];
  isLoading = false;

  get collectedCount(): number {
    return this.displayItems.filter((i) => i.hasValue).length;
  }

  get missingRequiredCount(): number {
    return this.displayItems.filter((i) => i.required && !i.hasValue).length;
  }

  ngOnInit(): void {
    this.loadDataPointsConfig();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['lead'] && this.dataPointsConfig.length > 0) {
      this.computeDisplayItems();
    }
  }

  loadDataPointsConfig(): void {
    this.isLoading = true;
    this.dataPointService.getDataPoints(false).subscribe({
      next: (points) => {
        this.dataPointsConfig = (points || []).sort(
          (a, b) => (a.display_order ?? 999) - (b.display_order ?? 999),
        );
        this.computeDisplayItems();
        this.isLoading = false;
      },
      error: () => {
        this.isLoading = false;
        this.computeDisplayItems();
      },
    });
  }

  private extractLeadDataPoints(): Record<string, any> {
    if (!this.lead) return {};

    const raw =
      this.lead.data_points_json ||
      this.lead.DataPointsJson ||
      this.lead.data_points ||
      (this.lead.lead as any)?.data_points_json ||
      (this.lead.lead as any)?.DataPointsJson ||
      (this.lead.lead as any)?.data_points ||
      {};

    if (typeof raw === 'string') {
      try {
        return JSON.parse(raw);
      } catch {
        return {};
      }
    }

    return typeof raw === 'object' && raw !== null ? raw : {};
  }

  computeDisplayItems(): void {
    const rawData = this.extractLeadDataPoints();
    const normalizedRaw: Record<string, any> = {};

    Object.keys(rawData).forEach((k) => {
      normalizedRaw[k.toLowerCase().replace(/[^a-z0-9_]/g, '_')] = rawData[k];
    });

    const items: DataPointDisplayItem[] = [];
    const matchedKeys = new Set<string>();

    // 1. Process configured company data points
    for (const dp of this.dataPointsConfig) {
      const normKey = dp.key.toLowerCase().replace(/[^a-z0-9_]/g, '_');
      matchedKeys.add(normKey);

      let val: any = undefined;
      if (normalizedRaw[normKey] !== undefined) {
        val = normalizedRaw[normKey];
      } else if (rawData[dp.key] !== undefined) {
        val = rawData[dp.key];
      }

      const hasVal = val !== undefined && val !== null && val !== '';
      items.push({
        key: dp.key,
        label: dp.label,
        data_type: dp.data_type,
        required: dp.required,
        value: val,
        hasValue: hasVal,
        formattedValue: this.formatValue(val, dp.data_type),
        isCustom: false,
      });
    }

    // 2. Any additional custom captured keys in raw JSON not in definitions
    Object.keys(rawData).forEach((key) => {
      const normKey = key.toLowerCase().replace(/[^a-z0-9_]/g, '_');
      if (!matchedKeys.has(normKey)) {
        const val = rawData[key];
        const hasVal = val !== undefined && val !== null && val !== '';
        items.push({
          key,
          label: this.humanizeKey(key),
          data_type: typeof val === 'number' ? 'number' : typeof val === 'boolean' ? 'boolean' : 'text',
          required: false,
          value: val,
          hasValue: hasVal,
          formattedValue: String(val ?? ''),
          isCustom: true,
        });
      }
    });

    this.displayItems = items;
  }

  private formatValue(val: any, type: string): string {
    if (val === undefined || val === null || val === '') return '';

    if (type === 'boolean') {
      return val === true || val === 'true' || val === 1 ? 'Yes' : 'No';
    }

    if (type === 'date') {
      try {
        const d = new Date(val);
        if (!isNaN(d.getTime())) {
          return d.toLocaleDateString(undefined, {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
          });
        }
      } catch {
        // fallback
      }
    }

    if (type === 'number') {
      const num = Number(val);
      if (!isNaN(num)) {
        return num.toLocaleString();
      }
    }

    return String(val);
  }

  private humanizeKey(key: string): string {
    return key
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  getTypeIcon(type: string): string {
    switch (type) {
      case 'number':
        return 'pi pi-hashtag';
      case 'boolean':
        return 'pi pi-check-circle';
      case 'select':
        return 'pi pi-list';
      case 'date':
        return 'pi pi-calendar';
      case 'email':
        return 'pi pi-envelope';
      default:
        return 'pi pi-align-left';
    }
  }
}
