import { Component, Input, Output, EventEmitter } from '@angular/core';
import { Script } from '../../../../../models/script.models';
import { SharedModule } from '../../../../../shared/shared.module';
import { CLIENT_PERMISSIONS } from '../../../../../modules/client/constants/permission.constants';

@Component({
  selector: 'app-script-list',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './script-list.component.html'
})
export class ScriptListComponent {
  PERMISSIONS = CLIENT_PERMISSIONS;
  @Input() scripts: Script[] = [];
  @Input() selectedScript: Script | null = null;

  @Output() onSelect = new EventEmitter<Script>();
  @Output() onCreate = new EventEmitter<void>();
  @Output() onImport = new EventEmitter<void>();
  @Output() onSetDefault = new EventEmitter<{ script: Script; event: Event }>();
  @Output() onOpenSettings = new EventEmitter<{ script: Script; event: Event }>();
  @Output() onDelete = new EventEmitter<{ script: Script; event: Event }>();

  triggerDefault(script: Script, event: Event): void {
    this.onSetDefault.emit({ script, event });
  }

  triggerSettings(script: Script, event: Event): void {
    this.onOpenSettings.emit({ script, event });
  }

  triggerDelete(script: Script, event: Event): void {
    this.onDelete.emit({ script, event });
  }
}
