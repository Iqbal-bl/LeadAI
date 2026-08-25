import { Component } from '@angular/core';
import { RouterModule } from '@angular/router';

@Component({
  selector: 'app-primary-sidebar',
  standalone: true,
  imports: [RouterModule],
  templateUrl: './primary-sidebar.component.html',
  styleUrl: './primary-sidebar.component.scss',
})
export class PrimarySidebarComponent {}
