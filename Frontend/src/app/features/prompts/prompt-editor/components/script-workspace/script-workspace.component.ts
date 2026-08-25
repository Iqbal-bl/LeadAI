import { Component, Input, Output, EventEmitter } from '@angular/core';
import { Script, ScriptPreview } from '../../../../../models/script.models';
import { SharedModule } from '../../../../../shared/shared.module';
import { FlowchartEditorComponent } from '../flowchart-editor/flowchart-editor.component';
import { CLIENT_PERMISSIONS } from '../../../../../modules/client/constants/permission.constants';

@Component({
  selector: 'app-script-workspace',
  standalone: true,
  imports: [SharedModule, FlowchartEditorComponent],
  templateUrl: './script-workspace.component.html',
})
export class ScriptWorkspaceComponent {
  PERMISSIONS = CLIENT_PERMISSIONS;
  @Input() selectedScript:
    | (Script & {
        script_xml?: string;
        sections?: any[];
        rendered_prompt?: string;
      })
    | null = null;
  @Input() scriptXmlEditorContent = '';
  @Input() scriptPreview: ScriptPreview | null = null;
  @Input() previewChannel: 'chat' | 'voice' = 'chat';
  @Input() previewLoading = false;
  @Input() scriptLoading = false;
  @Input() listCollapsed = false;

  @Output() onSaveXml = new EventEmitter<string>();
  @Output() onResetXml = new EventEmitter<void>();
  @Output() onChannelChange = new EventEmitter<'chat' | 'voice'>();
  @Output() scriptXmlEditorContentChange = new EventEmitter<string>();
  @Output() toggleList = new EventEmitter<void>();

  editorMode: 'code' | 'flowchart' = 'code';

  onFlowchartChange(xml: string): void {
    this.scriptXmlEditorContent = xml;
    this.scriptXmlEditorContentChange.emit(xml);
  }
}
