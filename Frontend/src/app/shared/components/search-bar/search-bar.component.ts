import { Component, Input, Output, EventEmitter } from '@angular/core';

@Component({
  selector: 'app-search-bar',
  standalone: false,
  template: `
    <p-iconfield>
      <p-inputicon styleClass="pi pi-search" />
      <input pInputText type="text" [(ngModel)]="query" (input)="onInput()" [placeholder]="placeholder" class="text-sm w-full" />
    </p-iconfield>
  `
})
export class SearchBarComponent {
  @Input() placeholder: string = 'Search...';
  @Output() search = new EventEmitter<string>();
  query: string = '';

  onInput(): void {
    this.search.emit(this.query);
  }
}
