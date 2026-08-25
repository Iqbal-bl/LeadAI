import { Component } from '@angular/core';

@Component({
  selector: 'app-confirmation-dialog',
  standalone: false,
  template: `
    <p-confirmDialog header="Confirmation" icon="pi pi-exclamation-triangle" [style]="{ width: '450px' }"></p-confirmDialog>
  `
})
export class ConfirmationDialogComponent {}
