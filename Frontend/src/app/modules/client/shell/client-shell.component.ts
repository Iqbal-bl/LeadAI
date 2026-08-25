import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { NotificationPanelComponent } from '../../../layout/notification-panel/notification-panel.component';
import { ToolbarComponent } from '../../../layout/toolbar/toolbar.component';
import { SidebarComponent } from '../../../layout/sidebar/sidebar.component';
import {
  LayoutService,
  SidebarSection,
} from '../../../services/layout.service';
import { Subscription } from 'rxjs';
import { ClientNavigationalMenu } from '../constants/client-navigational-menu';
import { ClientPermissionService } from '../services/client-permission.service';
import { AuthService } from '../../../services/auth.service';
import { LeadService } from '../../../services/lead.service';
import { ToastService } from '../../../shared/services/toast.service';

@Component({
  selector: 'client-shell',
  standalone: true,
  imports: [
    RouterOutlet,
    NotificationPanelComponent,
    ToolbarComponent,
    SidebarComponent,
  ],
  templateUrl: './client-shell.component.html',
  styleUrl: './client-shell.component.scss',
})
export class ClientShellComponent implements OnInit, OnDestroy {
  private layoutService = inject(LayoutService);
  private authService = inject(AuthService);
  private permissionService = inject(ClientPermissionService);
  private leadService = inject(LeadService);
  private toastService = inject(ToastService);
  private sub = new Subscription();

  navigationalMenu: SidebarSection[] = ClientNavigationalMenu;

  sidebarCollapsed = false;

  ngOnInit(): void {
    this.sub.add(
      this.layoutService.sidebarCollapsed$.subscribe((collapsed) => {
        this.sidebarCollapsed = collapsed;
      }),
    );

    this.sub.add(
      this.authService.currentUser$.subscribe((user) => {
        if (!user) {
          this.navigationalMenu = ClientNavigationalMenu;
          return;
        }
        const permissions = user.permissions || [];
        if (permissions.length > 0) {
          this.navigationalMenu = this.permissionService.filterMenuByPermissions(
            ClientNavigationalMenu,
            permissions,
          );
        } else {
          this.navigationalMenu = ClientNavigationalMenu;
        }
      }),
    );

    // Global Inbox WebSocket connection management
    this.sub.add(
      this.authService.selectedCompanyId$.subscribe((clientId) => {
        if (clientId) {
          this.leadService.connectInbox(clientId);
        } else {
          this.leadService.disconnectInbox();
        }
      })
    );

    // Handle lead threshold crossed notification events
    this.sub.add(
      this.leadService.inboxMessages$.subscribe((data: any) => {
        if (data && data.type === 'lead_threshold_crossed') {
          // Toast message
          this.toastService.info(
            `Lead crossed threshold to ${data.score} (${data.status})`,
            'Threshold Crossed'
          );

          // Add to layout service notifications list (updates count badge)
          this.layoutService.addNotification({
            id: Date.now(),
            type: 'ai-alert',
            title: 'Lead Threshold Crossed',
            message: `Lead scored ${data.score} on channel "${data.channel}"`,
            time: 'Just now',
            read: false,
            icon: 'pi pi-exclamation-triangle',
            severity: 'warn',
          });
        }
      })
    );
  }

  ngOnDestroy(): void {
    this.sub.unsubscribe();
    this.leadService.disconnectInbox();
  }

  onSidebarToggle(collapsed: boolean): void {
    this.layoutService.setSidebarCollapsed(collapsed);
  }
}
