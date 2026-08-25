import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';

import { SecondarySidebarComponent } from '../../layout/secondary-sidebar/secondary-sidebar.component';
import { TopHeaderComponent } from '../../layout/top-header/top-header';

@Component({
  selector: 'app-analytics-layout',
  standalone: true,
  imports: [
    CommonModule,
    RouterModule, 
    SecondarySidebarComponent,
    TopHeaderComponent,
  ],
  templateUrl: './analytics-layout.html',
  styleUrl: './analytics-layout.scss',
})
export class AnalyticsLayoutComponent {}
