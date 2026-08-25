import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-loading-skeleton',
  standalone: false,
  template: `
    <div class="space-y-4 p-4">
      <div *ngIf="type === 'card'" class="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div *ngFor="let i of [1,2,3]" class="card-base p-4 space-y-3">
          <p-skeleton width="30%" height="1.5rem"></p-skeleton>
          <p-skeleton width="60%" height="2rem"></p-skeleton>
          <p-skeleton width="100%" height="1rem"></p-skeleton>
        </div>
      </div>

      <div *ngIf="type === 'table'" class="card-base p-4 space-y-3">
        <p-skeleton width="100%" height="2.5rem"></p-skeleton>
        <div *ngFor="let i of [1,2,3,4,5]" class="flex gap-4">
          <p-skeleton width="20%" height="1.5rem"></p-skeleton>
          <p-skeleton width="30%" height="1.5rem"></p-skeleton>
          <p-skeleton width="30%" height="1.5rem"></p-skeleton>
          <p-skeleton width="20%" height="1.5rem"></p-skeleton>
        </div>
      </div>
    </div>
  `
})
export class LoadingSkeletonComponent {
  @Input() type: 'card' | 'table' = 'card';
}
