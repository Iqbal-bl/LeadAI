import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { SharedModule } from '../../../../../shared/shared.module';
import { DataPointService } from '../../../../../services/data-point.service';
import {
  DataPoint,
  DataPointType,
  CreateDataPointPayload,
  UpdateDataPointPayload,
} from '../../../../../models/data-point.models';
import { ToastService } from '../../../../../shared/services/toast.service';
import { ConfirmationService } from '../../../../../shared/services/confirmation.service';

@Component({
  selector: 'app-data-points',
  standalone: true,
  imports: [CommonModule, FormsModule, SharedModule],
  templateUrl: './data-points.component.html',
  styleUrls: ['./data-points.component.scss'],
})
export class DataPointsComponent implements OnInit {
  private dataPointService = inject(DataPointService);
  private toastService = inject(ToastService);
  private confirmationService = inject(ConfirmationService);

  // Data List State
  dataPoints: DataPoint[] = [];
  isLoading = false;
  includeInactive = true;

  // Dialog State
  dialogVisible = false;
  isEditing = false;
  editingId: string | null = null;
  isSaving = false;

  // Form State
  formLabel = '';
  formKey = '';
  keyManuallyEdited = false;
  formDataType: DataPointType = 'text';
  formDescription = '';
  formRequired = false;
  formDisplayOrder: number | null = null;
  formIsActive = true;
  formOptions: string[] = [];
  newOptionText = '';

  // Data Type Options
  readonly dataTypes: {
    label: string;
    value: DataPointType;
    icon: string;
    description: string;
  }[] = [
    {
      label: 'Text',
      value: 'text',
      icon: 'pi pi-align-left',
      description: 'Freeform text (e.g., preferred location, notes)',
    },
    {
      label: 'Number',
      value: 'number',
      icon: 'pi pi-hashtag',
      description: 'Numerical value or budget amount',
    },
    {
      label: 'Boolean',
      value: 'boolean',
      icon: 'pi pi-check-circle',
      description: 'Yes / No flag (e.g., is decision maker)',
    },
    {
      label: 'Select',
      value: 'select',
      icon: 'pi pi-list',
      description: 'Single choice from a predefined list of options',
    },
    {
      label: 'Date',
      value: 'date',
      icon: 'pi pi-calendar',
      description: 'Calendar date or milestone deadline',
    },
    {
      label: 'Email',
      value: 'email',
      icon: 'pi pi-envelope',
      description: 'Customer or stakeholder email address',
    },
  ];

  ngOnInit(): void {
    this.loadDataPoints();
  }

  loadDataPoints(): void {
    this.isLoading = true;
    this.dataPointService.getDataPoints(this.includeInactive).subscribe({
      next: (data) => {
        this.dataPoints = (data || []).sort(
          (a, b) => (a.display_order ?? 999) - (b.display_order ?? 999),
        );
        this.isLoading = false;
      },
      error: (err: any) => {
        this.isLoading = false;
        this.toastService.error(
          err?.error?.detail || 'Failed to load company data points.',
          'Data Points Error',
        );
      },
    });
  }

  openCreateDialog(): void {
    this.isEditing = false;
    this.editingId = null;
    this.formLabel = '';
    this.formKey = '';
    this.keyManuallyEdited = false;
    this.formDataType = 'text';
    this.formDescription = '';
    this.formRequired = false;
    this.formDisplayOrder = this.dataPoints.length + 1;
    this.formIsActive = true;
    this.formOptions = [];
    this.newOptionText = '';
    this.dialogVisible = true;
  }

  openEditDialog(dp: DataPoint): void {
    this.isEditing = true;
    this.editingId = dp.id;
    this.formLabel = dp.label;
    this.formKey = dp.key;
    this.keyManuallyEdited = true;
    this.formDataType = dp.data_type;
    this.formDescription = dp.description || '';
    this.formRequired = dp.required;
    this.formDisplayOrder = dp.display_order ?? 1;
    this.formIsActive = dp.is_active;
    this.formOptions = Array.isArray(dp.options) ? [...dp.options] : [];
    this.newOptionText = '';
    this.dialogVisible = true;
  }

  closeDialog(): void {
    this.dialogVisible = false;
  }

  onLabelChange(): void {
    if (!this.keyManuallyEdited && !this.isEditing) {
      this.formKey = this.slugify(this.formLabel);
    }
  }

  onKeyInput(): void {
    this.keyManuallyEdited = true;
    this.formKey = this.slugify(this.formKey);
  }

  private slugify(text: string): string {
    return text
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9_]+/g, '_')
      .replace(/^_+|_+$/g, '');
  }

  addOption(): void {
    const trimmed = this.newOptionText.trim();
    if (!trimmed) return;
    if (this.formOptions.includes(trimmed)) {
      this.toastService.warn('Option already added.', 'Duplicate Option');
      return;
    }
    this.formOptions.push(trimmed);
    this.newOptionText = '';
  }

  removeOption(index: number): void {
    this.formOptions.splice(index, 1);
  }

  saveDataPoint(): void {
    if (!this.formLabel.trim()) {
      this.toastService.warn('Please enter a display label.', 'Validation');
      return;
    }

    if (!this.formKey.trim()) {
      this.toastService.warn('Please enter a valid key.', 'Validation');
      return;
    }

    const keyRegex = /^[a-z0-9_]+$/;
    if (!keyRegex.test(this.formKey)) {
      this.toastService.warn(
        'Key must only contain lowercase letters, numbers, and underscores (e.g., preferred_location).',
        'Invalid Key',
      );
      return;
    }

    if (this.formDataType === 'select' && this.formOptions.length < 2) {
      this.toastService.warn(
        'Select data type requires at least 2 predefined options.',
        'Options Required',
      );
      return;
    }

    this.isSaving = true;

    if (this.isEditing && this.editingId) {
      const payload: UpdateDataPointPayload = {
        key: this.formKey,
        label: this.formLabel.trim(),
        data_type: this.formDataType,
        options: this.formDataType === 'select' ? this.formOptions : undefined,
        description: this.formDescription.trim() || undefined,
        required: this.formRequired,
        display_order: this.formDisplayOrder ?? undefined,
        is_active: this.formIsActive,
      };

      this.dataPointService.updateDataPoint(this.editingId, payload).subscribe({
        next: () => {
          this.isSaving = false;
          this.dialogVisible = false;
          this.toastService.success(
            `Data point "${this.formLabel}" updated successfully.`,
            'Updated',
          );
          this.loadDataPoints();
        },
        error: (err: any) => {
          this.isSaving = false;
          this.toastService.error(
            err?.error?.detail || 'Failed to update data point.',
            'Update Failed',
          );
        },
      });
    } else {
      const payload: CreateDataPointPayload = {
        key: this.formKey,
        label: this.formLabel.trim(),
        data_type: this.formDataType,
        options: this.formDataType === 'select' ? this.formOptions : undefined,
        description: this.formDescription.trim() || undefined,
        required: this.formRequired,
        display_order: this.formDisplayOrder ?? undefined,
      };

      this.dataPointService.createDataPoint(payload).subscribe({
        next: () => {
          this.isSaving = false;
          this.dialogVisible = false;
          this.toastService.success(
            `Data point "${this.formLabel}" created successfully.`,
            'Created',
          );
          this.loadDataPoints();
        },
        error: (err: any) => {
          this.isSaving = false;
          this.toastService.error(
            err?.error?.detail || 'Failed to create data point.',
            'Creation Failed',
          );
        },
      });
    }
  }

  toggleRequired(dp: DataPoint): void {
    const updated = !dp.required;
    this.dataPointService
      .updateDataPoint(dp.id, { required: updated })
      .subscribe({
        next: () => {
          dp.required = updated;
          this.toastService.success(
            `"${dp.label}" marked as ${updated ? 'required' : 'optional'}.`,
            'Status Updated',
          );
        },
        error: (err: any) => {
          this.toastService.error(
            err?.error?.detail || 'Could not update required status.',
            'Error',
          );
        },
      });
  }

  toggleActive(dp: DataPoint): void {
    const updated = !dp.is_active;
    this.dataPointService
      .updateDataPoint(dp.id, { is_active: updated })
      .subscribe({
        next: () => {
          dp.is_active = updated;
          this.toastService.success(
            `"${dp.label}" set to ${updated ? 'active' : 'inactive'}.`,
            'Status Updated',
          );
        },
        error: (err: any) => {
          this.toastService.error(
            err?.error?.detail || 'Could not update active status.',
            'Error',
          );
        },
      });
  }

  confirmDelete(dp: DataPoint): void {
    this.confirmationService.confirmDelete(
      `Are you sure you want to delete the data point "${dp.label}"? Existing collected values for leads will be preserved.`,
      () => {
        this.dataPointService.deleteDataPoint(dp.id).subscribe({
          next: () => {
            this.toastService.success(
              `Data point "${dp.label}" removed successfully.`,
              'Deleted',
            );
            this.loadDataPoints();
          },
          error: (err: any) => {
            this.toastService.error(
              err?.error?.detail || 'Failed to delete data point.',
              'Delete Error',
            );
          },
        });
      },
      'Delete Data Point',
    );
  }

  getTypeMeta(type: DataPointType) {
    return (
      this.dataTypes.find((t) => t.value === type) || {
        label: type,
        value: type,
        icon: 'pi pi-tag',
        description: '',
      }
    );
  }
}
