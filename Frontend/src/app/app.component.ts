import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { ToastModule } from 'primeng/toast';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, ToastModule],
  template: '<router-outlet></router-outlet><p-toast position="top-right"></p-toast>',
  styles: [':host { display: block; }']
})
export class AppComponent {
  title = 'LeadAI';
}
