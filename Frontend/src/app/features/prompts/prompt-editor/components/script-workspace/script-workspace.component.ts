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

  insertToken(token: string): void {
    const textarea = document.querySelector(
      'textarea[placeholder*="Write XML schema flow here"]',
    ) as HTMLTextAreaElement;
    if (textarea && textarea.selectionStart !== undefined) {
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const current = this.scriptXmlEditorContent || '';
      this.scriptXmlEditorContent =
        current.substring(0, start) + token + current.substring(end);
      this.scriptXmlEditorContentChange.emit(this.scriptXmlEditorContent);
      setTimeout(() => {
        textarea.focus();
        textarea.setSelectionRange(start + token.length, start + token.length);
      }, 0);
    } else {
      this.scriptXmlEditorContent = (this.scriptXmlEditorContent || '') + token;
      this.scriptXmlEditorContentChange.emit(this.scriptXmlEditorContent);
    }
  }
}
