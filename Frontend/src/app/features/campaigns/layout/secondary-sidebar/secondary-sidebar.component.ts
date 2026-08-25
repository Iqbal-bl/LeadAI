import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';

@Component({
  selector: 'app-secondary-sidebar',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './secondary-sidebar.component.html',
  styleUrl: './secondary-sidebar.component.scss',
})
export class SecondarySidebarComponent {}
