import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { AccordionModule } from 'primeng/accordion';

interface AiTool {
  label: string;
  action: string;
}

interface AiCategory {
  title: string;
  value: string;
  icon: string;
  iconColor: string;
  description: string;
  tools: AiTool[];
}

@Component({
  selector: 'app-ai-tools',
  standalone: true,
  imports: [CommonModule, AccordionModule],
  templateUrl: './ai-tools.html',
  styleUrl: './ai-tools.scss',
})
export class AiTools {
  categories: AiCategory[] = [
    {
      title: 'Content',
      value: '0',
      icon: 'pi pi-file-edit',
      iconColor: 'text-emerald-500 dark:text-emerald-400',
      description: 'Write, expand and structure your post copy.',
      tools: [
        { label: 'Caption', action: 'caption' },
        { label: 'Tone', action: 'tone' },
        { label: 'Length', action: 'length' },
      ],
    },
    {
      title: 'Enhancement',
      value: '1',
      icon: 'pi pi-sparkles',
      iconColor: 'text-purple-500 dark:text-purple-400',
      description: 'Improve clarity, grammar and style quality.',
      tools: [
        { label: 'Grammar', action: 'grammar' },
        { label: 'Rewrite', action: 'rewrite' },
        { label: 'Translate', action: 'translate' },
      ],
    },
    {
      title: 'Engagement',
      value: '2',
      icon: 'pi pi-heart',
      iconColor: 'text-amber-500 dark:text-amber-400',
      description: 'Increase engagement with hashtags & call-to-actions.',
      tools: [
        { label: 'Hashtags', action: 'hashtags' },
        { label: 'CTA', action: 'cta' },
        { label: 'Hook', action: 'hook' },
      ],
    },
  ];

  onToolClick(tool: AiTool) {
    console.log('AI Tool clicked:', tool.action);
  }
}
