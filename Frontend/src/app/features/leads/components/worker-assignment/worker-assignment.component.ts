import { Component, Input, Output, EventEmitter, OnChanges, SimpleChanges } from '@angular/core';
import { AssignableUser } from '../../../../models/auth.models';
import { SharedModule } from '../../../../shared/shared.module';

@Component({
  selector: 'app-worker-assignment',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './worker-assignment.component.html',
})
export class WorkerAssignmentComponent implements OnChanges {
  @Input() lead!: any;
  @Input() teamMembers: AssignableUser[] = [];
  @Output() assign = new EventEmitter<string | null>();

  selectedAgent: string = '';
  teamOptions: { label: string; value: string }[] = [];

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['teamMembers']) {
      this.teamOptions = [
        { label: 'Unassigned (None)', value: '' },
        ...this.teamMembers.map(u => ({
          label: `${u.full_name} (${u.role})`,
          value: u.user_email
        }))
      ];
    }
    if (changes['lead']) {
      // Find matches in options
      this.selectedAgent = this.lead?.assignedTo && this.lead.assignedTo !== 'Unassigned' ? this.lead.assignedTo : '';
    }
  }

  onAgentChange(email: string): void {
    this.assign.emit(email || null);
  }
}
