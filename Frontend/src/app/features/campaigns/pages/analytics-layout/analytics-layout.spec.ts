import { ComponentFixture, TestBed } from '@angular/core/testing';

import { AnalyticsLayout } from './analytics-layout';

describe('AnalyticsLayout', () => {
  let component: AnalyticsLayout;
  let fixture: ComponentFixture<AnalyticsLayout>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AnalyticsLayout],
    }).compileComponents();

    fixture = TestBed.createComponent(AnalyticsLayout);
    component = fixture.componentInstance;
    await fixture.whenStable();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
