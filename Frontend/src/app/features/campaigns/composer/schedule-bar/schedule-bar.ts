import { Component, EventEmitter, Input, Output } from '@angular/core';

import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { CardModule } from 'primeng/card';
import { DatePickerModule } from 'primeng/datepicker';
import { SelectModule } from 'primeng/select';
import { PopoverModule, Popover } from 'primeng/popover';

@Component({
  selector: 'app-schedule-bar',
  standalone: true,

  imports: [CommonModule, FormsModule, CardModule, DatePickerModule, SelectModule, PopoverModule],

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
     Schedule Data
  ========================================================== */

  selectedDate: Date | null = null;

  selectedTime: Date | null = null;

  selectedTimezone = 'Asia/Kolkata';

  /* ==========================================================
     Timezones
  ========================================================== */

  timezones = [
    {
      label: 'Asia/Kolkata (GMT +05:30)',
      value: 'Asia/Kolkata',
    },

    {
      label: 'UTC',
      value: 'UTC',
    },

    {
      label: 'America/New_York',
      value: 'America/New_York',
    },

    {
      label: 'Europe/London',
      value: 'Europe/London',
    },
  ];

  /* ==========================================================
     Change Publish Mode
  ========================================================== */

  setPublishMode(mode: 'now' | 'schedule'): void {
    this.publishMode = mode;

    this.publishModeChange.emit(mode);
  }

  /* ==========================================================
     Date Picker
  ========================================================== */

  onDateSelect(popover: Popover): void {
    popover.hide();
  }

  /* ==========================================================
     Time Picker
  ========================================================== */

  onTimeSelect(popover: Popover): void {
    popover.hide();
  }
}
