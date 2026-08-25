export interface FlowNode {
  id: string;
  name: string;
  content: string;
  order: number;
  isEditing?: boolean;
  children: FlowNode[];
  depth: number;
  isCollapsed?: boolean;
}
