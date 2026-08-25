import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-top-header',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './top-header.html',
  styleUrl: './top-header.scss'
})
export class TopHeaderComponent {

  @Input()
  platform = 'Facebook';

}