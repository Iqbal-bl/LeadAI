import { ComponentFixture, TestBed } from '@angular/core/testing';

import { SecondarySidebarComponent } from './secondary-sidebar.component';

describe('SecondarySidebarComponent', () => {
  let component: SecondarySidebarComponent;
  let fixture: ComponentFixture<SecondarySidebarComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SecondarySidebarComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(SecondarySidebarComponent);
    component = fixture.componentInstance;
    await fixture.whenStable();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
