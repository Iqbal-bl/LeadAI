import { Component } from '@angular/core';
import { AnalyticsComponent } from '../../../../features/analytics/analytics.component';

@Component({
  selector: 'admin-dashboard',
  standalone: true,
  imports: [AnalyticsComponent],
  templateUrl: './admin-dashboard.component.html',
  styleUrl: './admin-dashboard.component.scss'
})
export class AdminDashboardComponent {

}
