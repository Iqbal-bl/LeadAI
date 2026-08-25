import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-confirmation-dialog',
  standalone: false,
  template: `
    <p-confirmDialog
      [header]="header"
      [icon]="icon"
      [style]="style"
      [breakpoints]="{ '960px': '75vw', '640px': '90vw' }"
    ></p-confirmDialog>
  `,
})
export class ConfirmationDialogComponent {
  @Input() header: string = 'Confirmation';
  @Input() icon: string = 'pi pi-exclamation-triangle';
  @Input() style: Record<string, string> = { width: '450px' };
}
