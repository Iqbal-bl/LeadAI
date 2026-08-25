import { Component, OnInit, OnDestroy, inject } from '@angular/core';
import { Subscription } from 'rxjs';
import { LayoutService } from '../../services/layout.service';

import { SharedModule } from '../../shared/shared.module';
import { Notification } from '../../models/notification.models';

@Component({
  selector: 'app-notification-panel',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './notification-panel.component.html',
  styleUrl: './notification-panel.component.scss'
})
export class NotificationPanelComponent implements OnInit, OnDestroy {
  private layoutService = inject(LayoutService);
  private sub = new Subscription();

  private _visible = false;
  notifications: Notification[] = [];

  get visible(): boolean {
    return this._visible;
  }
  set visible(val: boolean) {
    this._visible = val;
    this.layoutService.setNotificationPanelOpen(val);
  }

  get unreadCount(): number {
    return this.notifications.filter(n => !n.read).length;
  }

  ngOnInit(): void {
    // Monitor panel visibility from service
    this.sub.add(
      this.layoutService.notificationPanelOpen$.subscribe(open => {
        this._visible = open;
      })
    );

    // Monitor role-specific notification stream from service
    this.sub.add(
      this.layoutService.notifications$.subscribe(notes => {
        this.notifications = notes;
      })
    );
  }

  ngOnDestroy(): void {
    this.sub.unsubscribe();
  }

  open(): void {
    this.visible = true;
  }

  close(): void {
    this.visible = false;
  }

  markAsRead(notification: Notification): void {
    this.layoutService.markAsRead(notification.id);
  }

  markAllAsRead(): void {
    this.layoutService.markAllAsRead();
  }

  dismiss(notification: Notification): void {
    this.layoutService.dismissNotification(notification.id);
  }

  getSeverityClass(severity: string): string {
    const map: { [key: string]: string } = {
      'info': 'bg-info-100 text-info-600 dark:bg-info-500/20 dark:text-info-400',
      'success': 'bg-success-100 text-success-600 dark:bg-success-500/20 dark:text-success-400',
      'warn': 'bg-warning-100 text-warning-600 dark:bg-warning-500/20 dark:text-warning-400',
      'danger': 'bg-danger-100 text-danger-600 dark:bg-danger-500/20 dark:text-danger-400',
    };
    return map[severity] || map['info'];
  }
}
