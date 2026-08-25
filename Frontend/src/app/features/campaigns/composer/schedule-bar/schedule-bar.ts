import { Component, EventEmitter, Input, Output } from '@angular/core';

import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { CardModule } from 'primeng/card';
import { DatePickerModule } from 'primeng/datepicker';
import { PopoverModule, Popover } from 'primeng/popover';

@Component({
  selector: 'app-schedule-bar',
  standalone: true,

  imports: [CommonModule, FormsModule, CardModule, DatePickerModule, PopoverModule],

  templateUrl: './schedule-bar.html',
  styleUrl: './schedule-bar.scss',
})
export class ScheduleBar {
  /* ==========================================================
     Publish Mode
  ========================================================== */

  @Input()
  publishMode: 'now' | 'schedule' = 'now';

  @Output()
  publishModeChange = new EventEmitter<'now' | 'schedule'>();

  /* ==========================================================
     Schedule Outputs
  ========================================================== */

  @Output()
  scheduledUtcChange = new EventEmitter<string | null>();

  @Output()
  scheduledDateChange = new EventEmitter<string | null>();

  /* ==========================================================
     Schedule Data
  ========================================================== */

  selectedDate: Date | null = null;

  selectedTime: Date | null = null;

  /* ==========================================================
     Change Publish Mode
  ========================================================== */

  setPublishMode(mode: 'now' | 'schedule'): void {
    this.publishMode = mode;

    this.publishModeChange.emit(mode);
  }

  /* ==========================================================
     Scheduled UTC Conversion
  ========================================================== */

  getScheduledUtc(): string | null {
    if (!this.selectedDate) {
      return null;
    }

    const scheduledDate = new Date(this.selectedDate);

    if (this.selectedTime) {
      scheduledDate.setHours(
        this.selectedTime.getHours(),
        this.selectedTime.getMinutes(),
        this.selectedTime.getSeconds(),
        0
      );
    } else {
      scheduledDate.setHours(0, 0, 0, 0);
    }

    return scheduledDate.toISOString();
  }

  emitScheduledUtc(): void {
    const utcString = this.getScheduledUtc();
    this.scheduledUtcChange.emit(utcString);
    this.scheduledDateChange.emit(utcString);
  }

  /* ==========================================================
     Date Picker
  ========================================================== */

  onDateSelect(popover?: Popover): void {
    if (popover) {
      popover.hide();
    }
    this.emitScheduledUtc();
  }

  /* ==========================================================
     Time Picker
  ========================================================== */

  onTimeSelect(popover?: Popover): void {
    if (popover) {
      popover.hide();
    }
    this.emitScheduledUtc();
  }
}

