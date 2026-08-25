import { Injectable, inject } from '@angular/core';
import { ConfirmationService as PrimeConfirmationService, Confirmation } from 'primeng/api';

export interface ConfirmOptions extends Confirmation {
  message: string;
  header?: string;
  icon?: string;
  accept?: () => void;
  reject?: () => void;
  acceptLabel?: string;
  rejectLabel?: string;
  acceptButtonStyleClass?: string;
  rejectButtonStyleClass?: string;
  [key: string]: any;
}

@Injectable({
  providedIn: 'root',
})
export class ConfirmationService {
  private primeConfirmationService = inject(PrimeConfirmationService);

  /**
   * Display a confirmation dialog
   * @param options Configuration for the confirmation dialog
   */
  confirm(options: ConfirmOptions): void {
    this.primeConfirmationService.confirm({
      header: options.header || 'Confirmation',
      icon: options.icon || 'pi pi-exclamation-triangle',
      acceptButtonStyleClass: options.acceptButtonStyleClass || 'p-button-danger',
      ...options,
    });
  }

  /**
   * Shorthand to confirm deletion of a resource
   * @param message Prompt message
   * @param onAccept Callback executed on confirmation
   * @param header Optional dialog header title
   */
  confirmDelete(
    message: string,
    onAccept: () => void,
    header: string = 'Confirm Deletion'
  ): void {
    this.confirm({
      message,
      header,
      icon: 'pi pi-trash',
      acceptButtonStyleClass: 'p-button-danger',
      accept: onAccept,
    });
  }

  /**
   * Close the active confirmation dialog
   */
  close(): void {
    this.primeConfirmationService.close();
  }
}
