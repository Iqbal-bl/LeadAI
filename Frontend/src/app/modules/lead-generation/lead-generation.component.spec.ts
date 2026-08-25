import { ComponentFixture, TestBed } from '@angular/core/testing';

import { LeadGenerationComponent } from './lead-generation.component';

describe('LeadGenerationComponent', () => {
  let component: LeadGenerationComponent;
  let fixture: ComponentFixture<LeadGenerationComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [LeadGenerationComponent]
    })
    .compileComponents();

    fixture = TestBed.createComponent(LeadGenerationComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
