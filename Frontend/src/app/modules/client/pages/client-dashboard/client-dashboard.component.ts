import { Component } from '@angular/core';
import { AnalyticsComponent } from "../../../../features/analytics/analytics.component";

@Component({
  selector: 'client-dashboard',
  imports: [AnalyticsComponent],
  templateUrl: './client-dashboard.component.html',
  styleUrl: './client-dashboard.component.scss',
})
export class ClientDashboardComponent {}
