import {
  Component,
  Input,
  Output,
  EventEmitter,
  OnInit,
  OnChanges,
  SimpleChanges,
} from '@angular/core';
import { SharedModule } from '../../../../../shared/shared.module';
import { FlowNode } from './flow-node.model';

@Component({
  selector: 'app-flowchart-editor',
  standalone: true,
  imports: [SharedModule],
  templateUrl: './flowchart-editor.component.html',
  styleUrls: ['./flowchart-editor.component.scss'],
})
export class FlowchartEditorComponent implements OnInit, OnChanges {
  @Input() xmlContent = '';
  @Output() xmlContentChange = new EventEmitter<string>();

  nodes: FlowNode[] = [];

  // Drag state (id-based for nested support)
  dragNodeId: string | null = null;
  dragSourceParent: FlowNode[] | null = null;
  dragOverNodeId: string | null = null;
  dragPosition: 'top' | 'bottom' | null = null;

  private idCounter = 0;

  // Depth colors for visual hierarchy
  private depthColors = [
    '#6366f1', // indigo-500 — depth 0
    '#a78bfa', // violet-400 — depth 1
    '#c084fc', // purple-400 — depth 2
    '#f0abfc', // fuchsia-300 — depth 3
    '#f9a8d4', // pink-300 — depth 4+
  ];

  ngOnInit(): void {
    this.parseXml(this.xmlContent);
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['xmlContent'] && !changes['xmlContent'].firstChange) {
      const incoming = changes['xmlContent'].currentValue || '';
      const current = this.serializeToXml();
      if (incoming.trim() !== current.trim()) {
        this.parseXml(incoming);
      }
    }
  }

  // ─── XML Parsing (recursive) ───
  parseXml(xml: string): void {
    this.nodes = [];
    if (!xml || !xml.trim()) return;

    // Extract the content inside <script>...</script>
    const scriptMatch = /<script>([\s\S]*)<\/script>/i.exec(xml);
    const body = scriptMatch ? scriptMatch[1] : xml;

    this.nodes = this.parseSections(body, 0);
  }

  private parseSections(xmlFragment: string, depth: number): FlowNode[] {
    const nodes: FlowNode[] = [];

    // Match top-level <section> tags only (not nested ones)
    // We'll use a manual approach to handle nesting properly
    let pos = 0;
    let order = 0;

    while (pos < xmlFragment.length) {
      const openTagStart = xmlFragment.indexOf('<section ', pos);
      if (openTagStart === -1) break;

      // Extract section name
      const nameMatch = /name="([^"]*)"/.exec(
        xmlFragment.substring(openTagStart),
      );
      if (!nameMatch) {
        pos = openTagStart + 1;
        continue;
      }

      const openTagEnd = xmlFragment.indexOf('>', openTagStart);
      if (openTagEnd === -1) break;

      // Find matching closing tag (handle nesting)
      const innerStart = openTagEnd + 1;
      const closingIndex = this.findMatchingClose(xmlFragment, innerStart);
      if (closingIndex === -1) break;

      const innerContent = xmlFragment.substring(innerStart, closingIndex);

      // Check for nested <section> inside
      const hasNestedSections = /<section\s+name="/.test(innerContent);

      let textContent = '';
      let children: FlowNode[] = [];

      if (hasNestedSections) {
        // Extract text before first nested section
        const firstNested = innerContent.indexOf('<section ');
        textContent = innerContent.substring(0, firstNested).trim();
        // Parse nested sections recursively
        children = this.parseSections(innerContent, depth + 1);
      } else {
        textContent = innerContent.trim();
      }

      nodes.push({
        id: this.generateId(),
        name: nameMatch[1].trim(),
        content: textContent,
        order: order++,
        children: children,
        depth: depth,
      });

      // Move past </section>
      pos = closingIndex + '</section>'.length;
    }

    return nodes;
  }

  private findMatchingClose(xml: string, startPos: number): number {
    let depth = 1;
    let pos = startPos;

    while (pos < xml.length && depth > 0) {
      const nextOpen = xml.indexOf('<section ', pos);
      const nextClose = xml.indexOf('</section>', pos);

      if (nextClose === -1) return -1;

      if (nextOpen !== -1 && nextOpen < nextClose) {
        depth++;
        pos = nextOpen + 9; // move past '<section '
      } else {
        depth--;
        if (depth === 0) return nextClose;
        pos = nextClose + 10; // move past '</section>'
      }
    }

    return -1;
  }

  // ─── XML Serialization (recursive) ───
  serializeToXml(): string {
    if (this.nodes.length === 0) return '<script>\n</script>';
    const sections = this.serializeNodes(this.nodes, 1);
    return `<script>\n${sections}</script>`;
  }

  private serializeNodes(nodes: FlowNode[], indentLevel: number): string {
    const indent = '  '.repeat(indentLevel);
    let result = '';

    for (const node of nodes) {
      result += `${indent}<section name="${this.escapeXmlAttr(node.name)}">\n`;

      if (node.content) {
        result += `${indent}  ${node.content}\n`;
      }

      if (node.children && node.children.length > 0) {
        result += this.serializeNodes(node.children, indentLevel + 1);
      }

      result += `${indent}</section>\n`;
    }

    return result;
  }

  // ─── Node CRUD ───
  addNodeAt(parent: FlowNode[], index: number, depth: number): void {
    const newNode: FlowNode = {
      id: this.generateId(),
      name: 'new_section',
      content: '',
      order: index,
      isEditing: true,
      children: [],
      depth: depth,
    };
    parent.splice(index, 0, newNode);
    this.reindexList(parent);
    this.emitChange();
  }

  addSubSection(parentNode: FlowNode): void {
    parentNode.isCollapsed = false; // expand to show new child
    const child: FlowNode = {
      id: this.generateId(),
      name: 'sub_section',
      content: '',
      order: parentNode.children.length,
      isEditing: true,
      children: [],
      depth: parentNode.depth + 1,
    };
    parentNode.children.push(child);
    this.reindexList(parentNode.children);
    this.emitChange();
  }

  deleteNode(parent: FlowNode[], index: number): void {
    parent.splice(index, 1);
    this.reindexList(parent);
    this.emitChange();
  }

  startEditing(node: FlowNode): void {
    node.isEditing = true;
  }

  stopEditing(node: FlowNode): void {
    node.isEditing = false;
    this.emitChange();
  }

  toggleEditing(node: FlowNode): void {
    node.isEditing = !node.isEditing;
    if (!node.isEditing) {
      this.emitChange();
    }
  }

  toggleCollapse(node: FlowNode): void {
    node.isCollapsed = !node.isCollapsed;
  }

  onNodeChange(): void {
    this.emitChange();
  }

  // ─── Visual Helpers ───
  getDepthColor(depth: number): string {
    return this.depthColors[Math.min(depth, this.depthColors.length - 1)];
  }

  getDepthGradient(depth: number): string {
    const c1 = this.depthColors[Math.min(depth, this.depthColors.length - 1)];
    const c2 =
      this.depthColors[Math.min(depth + 1, this.depthColors.length - 1)];
    return `linear-gradient(135deg, ${c1}, ${c2})`;
  }

  getNodeDisplayIndex(node: FlowNode, parent: FlowNode[]): number {
    return parent.indexOf(node) + 1;
  }

  // ─── Drag & Drop ───
  onDragStart(event: DragEvent, node: FlowNode, parent: FlowNode[]): void {
    this.dragNodeId = node.id;
    this.dragSourceParent = parent;
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', node.id);
    }
  }

  onDragEnd(): void {
    this.dragNodeId = null;
    this.dragSourceParent = null;
    this.dragOverNodeId = null;
    this.dragPosition = null;
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault();
  }

  onCardDragOver(event: DragEvent, node: FlowNode): void {
    event.preventDefault();
    event.stopPropagation();
    if (this.dragNodeId === null || this.dragNodeId === node.id) return;

    this.dragOverNodeId = node.id;
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    const midY = rect.top + rect.height / 2;
    this.dragPosition = event.clientY < midY ? 'top' : 'bottom';
  }

  onCardDragLeave(): void {
    this.dragOverNodeId = null;
    this.dragPosition = null;
  }

  onCardDrop(
    event: DragEvent,
    targetNode: FlowNode,
    targetParent: FlowNode[],
  ): void {
    event.preventDefault();
    event.stopPropagation();
    if (
      this.dragNodeId === null ||
      this.dragNodeId === targetNode.id ||
      !this.dragSourceParent
    ) {
      this.onDragEnd();
      return;
    }

    // Find and remove the dragged node from its original parent
    const sourceIndex = this.dragSourceParent.findIndex(
      (n) => n.id === this.dragNodeId,
    );
    if (sourceIndex === -1) {
      this.onDragEnd();
      return;
    }
    const [movedNode] = this.dragSourceParent.splice(sourceIndex, 1);

    // Update depth if moving between different nesting levels
    const targetIndex = targetParent.indexOf(targetNode);
    const depthDiff = targetNode.depth - movedNode.depth;
    this.updateNodeDepth(movedNode, depthDiff);

    // Determine insert position
    let insertAt: number;
    if (this.dragPosition === 'top') {
      insertAt = targetIndex;
    } else {
      insertAt = targetIndex + 1;
    }
    // Adjust if we removed from same parent above
    if (this.dragSourceParent === targetParent && sourceIndex < insertAt) {
      insertAt = Math.max(0, insertAt);
    }
    insertAt = Math.max(0, Math.min(insertAt, targetParent.length));

    targetParent.splice(insertAt, 0, movedNode);
    this.reindexList(this.dragSourceParent);
    this.reindexList(targetParent);
    this.onDragEnd();
    this.emitChange();
  }

  onCanvasDrop(event: DragEvent): void {
    event.preventDefault();
    this.onDragEnd();
  }

  // ─── Helpers ───
  private emitChange(): void {
    const xml = this.serializeToXml();
    this.xmlContentChange.emit(xml);
  }

  private reindexList(list: FlowNode[]): void {
    list.forEach((n, i) => (n.order = i));
  }

  private updateNodeDepth(node: FlowNode, depthDiff: number): void {
    node.depth += depthDiff;
    if (node.children) {
      node.children.forEach((child) => this.updateNodeDepth(child, depthDiff));
    }
  }

  private generateId(): string {
    return `node_${Date.now()}_${this.idCounter++}`;
  }

  private escapeXmlAttr(str: string): string {
    return str
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }
}
