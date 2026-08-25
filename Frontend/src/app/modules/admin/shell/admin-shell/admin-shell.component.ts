import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { Subscription } from 'rxjs';
import {
  LayoutService,
  SidebarSection,
} from '../../../../services/layout.service';
import { AdminNavigationalMenu } from '../../constants/admin-navigational-menu';

import { SidebarComponent } from '../../../../layout/sidebar/sidebar.component';
import { ToolbarComponent } from '../../../../layout/toolbar/toolbar.component';
import { NotificationPanelComponent } from '../../../../layout/notification-panel/notification-panel.component';
import { RouterOutlet } from '@angular/router';
import { SharedModule } from '../../../../shared/shared.module';

@Component({
  selector: 'app-admin-shell',
  standalone: true,
  imports: [
    SidebarComponent,
    ToolbarComponent,
    NotificationPanelComponent,
    RouterOutlet,
    SharedModule
  ],
  templateUrl: './admin-shell.component.html',
  styleUrl: './admin-shell.component.scss',
})
export class AdminShellComponent implements OnInit, OnDestroy {
  private layoutService = inject(LayoutService);
  private sub = new Subscription();

  navigationalMenu: SidebarSection[] = AdminNavigationalMenu;

  sidebarCollapsed = false;

  ngOnInit(): void {
    this.sub.add(
      this.layoutService.sidebarCollapsed$.subscribe((collapsed) => {
        this.sidebarCollapsed = collapsed;
      }),
    );
  }

  ngOnDestroy(): void {
    this.sub.unsubscribe();
  }

  onSidebarToggle(collapsed: boolean): void {
    this.layoutService.setSidebarCollapsed(collapsed);
  }
}
