import { Component, OnInit } from '@angular/core';
import { SharedModule } from '../../../shared/shared.module';
import { ThresholdService } from '../../../services/threshold.service';
import { ThresholdSettings } from '../../../models/threshold.models';
import { MessageService, ConfirmationService } from 'primeng/api';
import { CheckboxModule } from 'primeng/checkbox';
import { InputNumberModule } from 'primeng/inputnumber';

@Component({
  selector: 'app-lead-threshold',
  standalone: true,
  imports: [SharedModule, CheckboxModule, InputNumberModule],
  templateUrl: './lead-threshold.component.html',
  styleUrl: './lead-threshold.component.scss',
})
export class LeadThresholdComponent implements OnInit {
  settings: ThresholdSettings | null = null;
  loading = true;
  saving = false;

  // Editable fields
  scoreThreshold = 50;
  hideBelowThreshold = false;
  notifyOnThreshold = true;
  autoConvertThreshold: number | null = null;
  enableAutoConvert = false;

  constructor(
    private thresholdService: ThresholdService,
    private messageService: MessageService,
    private confirmationService: ConfirmationService,
  ) {}

  ngOnInit(): void {
    this.loadThreshold();
  }

  loadThreshold(): void {
    this.loading = true;
    this.thresholdService.getThreshold().subscribe({
      next: (settings: ThresholdSettings) => {
        this.settings = settings;
        this.scoreThreshold = settings.lead_score_threshold;
        this.hideBelowThreshold = settings.hide_below_threshold;
        this.notifyOnThreshold = settings.notify_on_threshold;
        this.autoConvertThreshold = settings.auto_convert_threshold;
        this.enableAutoConvert = settings.auto_convert_threshold !== null;
        this.loading = false;
      },
      error: () => {
        this.loading = false;
      },
    });
  }

  get hasChanges(): boolean {
    if (!this.settings) return false;
    const effectiveAutoConvert = this.enableAutoConvert
      ? this.autoConvertThreshold
      : null;
    return (
      this.scoreThreshold !== this.settings.lead_score_threshold ||
      this.hideBelowThreshold !== this.settings.hide_below_threshold ||
      this.notifyOnThreshold !== this.settings.notify_on_threshold ||
      effectiveAutoConvert !== this.settings.auto_convert_threshold
    );
  }

  /** Compute preview of impact based on delta vs current threshold */
  get previewMessage(): string {
    if (!this.settings) return '';
    if (this.scoreThreshold < this.settings.lead_score_threshold) {
      // Lowering threshold — surfaces more leads
      return `This will surface older leads that were previously hidden. ${this.settings.leads_below} leads are currently below the threshold.`;
    } else if (this.scoreThreshold > this.settings.lead_score_threshold) {
      return `This will hide leads scoring below ${this.scoreThreshold}. Currently ${this.settings.leads_above} leads are above the threshold.`;
    }
    return '';
  }

  saveThreshold(): void {
    const effectiveAutoConvert = this.enableAutoConvert
      ? this.autoConvertThreshold
      : null;

    this.confirmationService.confirm({
      message: `Save these threshold settings? This will recompute every lead's flag immediately. ${this.previewMessage}`,
      header: 'Update Lead Threshold',
      icon: 'pi pi-sliders-h',
      accept: () => {
        this.saving = true;
        this.thresholdService
          .updateThreshold({
            lead_score_threshold: this.scoreThreshold,
            hide_below_threshold: this.hideBelowThreshold,
            notify_on_threshold: this.notifyOnThreshold,
            auto_convert_threshold: effectiveAutoConvert,
          })
          .subscribe({
            next: (updated: ThresholdSettings) => {
              this.settings = updated;
              this.saving = false;
              this.messageService.add({
                severity: 'success',
                summary: 'Threshold Updated',
                detail:
                  'All lead flags have been recomputed. Changes are live.',
              });
            },
            error: () => {
              this.saving = false;
              this.messageService.add({
                severity: 'error',
                summary: 'Error',
                detail: 'Failed to update threshold.',
              });
            },
          });
      },
    });
  }
}
