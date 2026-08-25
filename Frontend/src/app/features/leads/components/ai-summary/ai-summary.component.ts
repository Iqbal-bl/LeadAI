import { Component, Input } from '@angular/core';
import { AiSummary } from '../../../../models/lead.models';

import { SharedModule } from '../../../../shared/shared.module';

@Component({
  selector: 'app-ai-summary',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './ai-summary.component.html',
})
export class AiSummaryComponent {
  @Input() summary!: AiSummary;
}
