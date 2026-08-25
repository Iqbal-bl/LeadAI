import { Injectable, inject } from '@angular/core';
import { MessageService } from 'primeng/api';

@Injectable({
  providedIn: 'root',
})
export class ToastService {
  private messageService = inject(MessageService);

  /**
   * Show a success toast message
   * @param detail The detailed message content
   * @param summary The optional header/summary of the toast (defaults to 'Success')
   * @param life The optional duration in milliseconds before automatic dismissal
   */
  success(detail: string, summary: string = 'Success', life?: number): void {
    this.messageService.add({
      severity: 'success',
      summary,
      detail,
      life,
    });
  }

  /**
   * Show an error toast message
   * @param detail The detailed message content
   * @param summary The optional header/summary of the toast (defaults to 'Error')
   * @param life The optional duration in milliseconds before automatic dismissal
   */
  error(detail: string, summary: string = 'Error', life?: number): void {
    this.messageService.add({
      severity: 'error',
      summary,
      detail,
      life,
    });
  }

  /**
   * Show an info toast message
   * @param detail The detailed message content
   * @param summary The optional header/summary of the toast (defaults to 'Info')
   * @param life The optional duration in milliseconds before automatic dismissal
   */
  info(detail: string, summary: string = 'Info', life?: number): void {
    this.messageService.add({
      severity: 'info',
      summary,
      detail,
      life,
    });
  }

  /**
   * Show a warning toast message
   * @param detail The detailed message content
   * @param summary The optional header/summary of the toast (defaults to 'Warning')
   * @param life The optional duration in milliseconds before automatic dismissal
   */
  warn(detail: string, summary: string = 'Warning', life?: number): void {
    this.messageService.add({
      severity: 'warn',
      summary,
      detail,
      life,
    });
  }

  /**
   * Clear all active toast messages
   */
  clear(): void {
    this.messageService.clear();
  }
}
