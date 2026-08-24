"""
XML Parser for Voice Agent
Parses agent_template.xml into structured sections for the system prompt
"""
import xml.etree.ElementTree as ET
from typing import Dict, List, Any
import re
import os


def parse_xml_to_sections(xml_content: str) -> List[Dict[str, Any]]:
    """
    Parse XML content into a list of section dictionaries.
    Returns sections that can be edited in the UI and converted to prompt text.
    """
    sections = []
    
    try:
        root = ET.fromstring(xml_content)
        
        # Parse features
        features_elem = root.find('features')
        if features_elem is not None:
            features = []
            for feature in features_elem.findall('feature'):
                feat_data = {
                    'name': feature.get('name', ''),
                    'enabled': feature.get('enabled', 'false') == 'true',
                    'data': {}
                }
                for data_elem in feature.findall('data'):
                    feat_data['data'][data_elem.get('key', '')] = data_elem.text or ''
                features.append(feat_data)
            
            if features:
                sections.append({
                    'id': 'features',
                    'title': 'Features',
                    'type': 'features',
                    'content': features,
                    'editable': True
                })
        
        # Parse all other sections
        for section_elem in root.findall('section'):
            section = parse_section(section_elem)
            if section:
                sections.append(section)
                
    except ET.ParseError as e:
        raise ValueError(f"Invalid XML: {str(e)}")
    
    return sections


def parse_section(section_elem: ET.Element) -> Dict[str, Any]:
    """Parse a single section element into a dictionary."""
    title = section_elem.get('title', 'Untitled')
    section_type = section_elem.get('type', 'text')
    section_id = title.lower().replace(' ', '_')
    
    content = ''
    
    if section_type == 'identity':
        fields = []
        for field in section_elem.findall('field'):
            fields.append({
                'name': field.get('name', ''),
                'value': field.text or ''
            })
        content = fields
        
    elif section_type == 'text':
        content = (section_elem.text or '').strip()
        
    elif section_type == 'list':
        items = []
        for item in section_elem.findall('item'):
            items.append({
                'priority': item.get('priority', 'normal'),
                'text': item.text or ''
            })
        content = items
        
    elif section_type == 'data':
        fields = []
        for field in section_elem.findall('field'):
            fields.append({
                'name': field.get('name', ''),
                'label': field.get('label', field.get('name', '')),
                'value': field.text or ''
            })
        content = fields
        
    elif section_type == 'data-unavailable':
        items = [item.text or '' for item in section_elem.findall('item')]
        content = items
        
    elif section_type == 'rules':
        must_do = [item.text or '' for item in section_elem.findall('.//must-do/item')]
        must_not = [item.text or '' for item in section_elem.findall('.//must-not/item')]
        content = {'must_do': must_do, 'must_not': must_not}
        
    elif section_type == 'scenarios':
        scenarios = []
        for scenario in section_elem.findall('scenario'):
            scenarios.append({
                'if': scenario.get('if', ''),
                'then': scenario.text or ''
            })
        content = scenarios
        
    elif section_type == 'script':
        steps = []
        for step in section_elem.findall('step'):
            steps.append({
                'order': int(step.get('order', 0)),
                'text': step.text or ''
            })
        steps.sort(key=lambda x: x['order'])
        content = steps
        
    elif section_type == 'personality':
        tones = [t.text or '' for t in section_elem.findall('tone')]
        style = section_elem.find('style')
        filler = section_elem.find('filler-words')
        content = {
            'tones': tones,
            'style': style.text if style is not None else '',
            'filler_words': filler.text == 'true' if filler is not None else False
        }
        
    elif section_type == 'output':
        content = {
            'max_length': (section_elem.find('max-length').text if section_elem.find('max-length') is not None else ''),
            'style': (section_elem.find('style').text if section_elem.find('style') is not None else ''),
            'special': [s.text or '' for s in section_elem.findall('special')]
        }
    
    return {
        'id': section_id,
        'title': title,
        'type': section_type,
        'content': content,
        'editable': True
    }


def sections_to_prompt(sections: List[Dict[str, Any]]) -> str:
    """Convert sections back to a formatted system prompt text."""
    prompt_parts = []

    for section in sections:
        # Defensive: a section may arrive as a plain string (e.g. malformed or
        # externally-supplied data). Indexing/`.get` on a string raises
        # "string indices must be integers"; treat such entries as raw text.
        if not isinstance(section, dict):
            if section:
                prompt_parts.append(str(section))
            continue

        title = section.get('title', '')

        # If there's rawContent (edited in UI), use it directly
        raw_content = section.get('rawContent')
        if raw_content is not None:
            part = f"## {title}\n{raw_content}\n"
            prompt_parts.append(part)
            continue

        section_type = section.get('type', 'text')
        content = section.get('content', '')

        part = f"## {title}\n"

        if section_type == 'features':
            enabled = [f['name'] for f in content
                       if isinstance(f, dict) and f.get('enabled')]
            if enabled:
                part += "Enabled features: " + ", ".join(enabled) + "\n"

        elif section_type == 'identity':
            # content should be a list of {name, value} dicts. If it's a string
            # (or a list of non-dicts), emit it as plain text instead of crashing.
            if isinstance(content, str):
                part += content + "\n"
            else:
                for field in content:
                    if isinstance(field, dict):
                        part += f"- {field.get('name', '').title()}: {field.get('value', '')}\n"
                    elif field:
                        part += f"- {field}\n"
                
        elif section_type == 'text':
            part += (content or "") + "\n"
            
        elif section_type == 'list':
            if isinstance(content, str):
                part += content + "\n"
            else:
                for item in (content or []):
                    if isinstance(item, dict):
                        priority = f"[{item.get('priority', '').upper()}] " if item.get('priority') not in (None, '', 'normal') else ''
                        text = item.get('text', '')
                    else:
                        priority, text = '', item
                    part += f"- {priority}{text}\n"

        elif section_type == 'data':
            if isinstance(content, str):
                part += content + "\n"
            else:
                for field in (content or []):
                    if isinstance(field, dict):
                        part += f"- {field.get('label', field.get('name', ''))}: {field.get('value', '')}\n"
                    elif field:
                        part += f"- {field}\n"

        elif section_type == 'data-unavailable':
            part += "You do NOT have access to:\n"
            if isinstance(content, str):
                part += content + "\n"
            else:
                for item in (content or []):
                    part += f"- {item}\n"

        elif section_type == 'rules':
            part += "MUST DO:\n"
            for item in (content.get('must_do') if isinstance(content, dict) else []):
                part += f"- {item}\n"
            part += "\nMUST NOT:\n"
            for item in (content.get('must_not') if isinstance(content, dict) else []):
                part += f"- {item}\n"

        elif section_type == 'scenarios':
            if isinstance(content, str):
                part += content + "\n"
            else:
                for scenario in (content or []):
                    if isinstance(scenario, dict):
                        part += f"- IF: {scenario.get('if')}\n  THEN: {scenario.get('then')}\n"
                    elif scenario:
                        part += f"- {scenario}\n"

        elif section_type == 'script':
            if isinstance(content, str):
                part += content + "\n"
            else:
                for step in (content or []):
                    if isinstance(step, dict):
                        part += f"{step.get('order')}. {step.get('text')}\n"
                    elif step:
                        part += f"- {step}\n"
                
        elif section_type == 'personality':
            if isinstance(content, dict):
                if content.get('tones'):
                    part += f"Tone: {', '.join(content['tones'])}\n"
                if content.get('style'):
                    part += f"Style: {content['style']}\n"
                
        elif section_type == 'output':
            if isinstance(content, dict):
                if content.get('max_length'):
                    part += f"Response length: {content['max_length']}\n"
                for special in content.get('special', []):
                    part += f"- {special}\n"
        
        prompt_parts.append(part)
    
    return "\n".join(prompt_parts)


def sections_to_xml(sections: List[Dict[str, Any]]) -> str:
    """Convert sections back to XML format string."""
    root = ET.Element('agent-template', {'version': '2.0'})
    
    # Handle features separately if present
    features_section = next((s for s in sections if s.get('id') == 'features'), None)
    if features_section:
        features_elem = ET.SubElement(root, 'features')
        for feat in features_section.get('content', []):
            feat_elem = ET.SubElement(features_elem, 'feature', {
                'name': feat.get('name', ''),
                'enabled': 'true' if feat.get('enabled') else 'false'
            })
            for key, val in feat.get('data', {}).items():
                data_elem = ET.SubElement(feat_elem, 'data', {'key': key})
                data_elem.text = val
                
    # Handle other sections
    for section in sections:
        if section.get('id') == 'features':
            continue
            
        section_elem = ET.SubElement(root, 'section', {
            'title': section.get('title', ''),
            'type': section.get('type', 'text')
        })
        
        stype = section.get('type')
        content = section.get('content')
        raw = section.get('rawContent')
        
        if stype == 'identity':
            fields = []
            if raw:
                for line in raw.split('\n'):
                    if ':' in line:
                        k, v = line.split(':', 1)
                        fields.append({'name': k.strip().lower(), 'value': v.strip()})
                    elif line.strip():
                        # Fallback for lines without colons (treat as 'info' or similar)
                        fields.append({'name': 'description', 'value': line.strip()})
            else:
                fields = content
            
            for f in fields:
                field_elem = ET.SubElement(section_elem, 'field', {'name': f.get('name', 'info')})
                field_elem.text = f.get('value', '')
                
        elif stype == 'text':
            section_elem.text = raw if raw is not None else content
            
        elif stype == 'list' or stype == 'data-unavailable':
            items = []
            if raw:
                items = [line.strip().lstrip('- ') for line in raw.split('\n') if line.strip()]
            else:
                items = [i.get('text') if isinstance(i, dict) else i for i in content]
            
            for item in items:
                item_elem = ET.SubElement(section_elem, 'item')
                item_elem.text = item
                
        elif stype == 'data':
            fields = []
            if raw:
                for line in raw.split('\n'):
                    if ':' in line:
                        k, v = line.split(':', 1)
                        fields.append({'label': k.strip(), 'value': v.strip()})
            else:
                fields = content
                
            for f in fields:
                field_elem = ET.SubElement(section_elem, 'field', {
                    'name': f.get('name', f.get('label', '').lower().replace(' ', '_')),
                    'label': f.get('label', '')
                })
                field_elem.text = f.get('value', '')
                
        elif stype == 'rules':
            must_do_elem = ET.SubElement(section_elem, 'must-do')
            must_not_elem = ET.SubElement(section_elem, 'must-not')
            
            if raw:
                lines = raw.split('\n')
                current = None
                for line in lines:
                    line = line.strip()
                    if not line: continue
                    if 'MUST DO' in line.upper(): current = must_do_elem
                    elif 'MUST NOT' in line.upper(): current = must_not_elem
                    elif current is not None:
                        item_elem = ET.SubElement(current, 'item')
                        item_elem.text = line.lstrip('- ')
            else:
                for item in content.get('must_do', []):
                    item_elem = ET.SubElement(must_do_elem, 'item')
                    item_elem.text = item
                for item in content.get('must_not', []):
                    item_elem = ET.SubElement(must_not_elem, 'item')
                    item_elem.text = item
                    
        elif stype == 'scenarios':
            scenarios = []
            if raw:
                parts = re.split(r'IF:', raw, flags=re.IGNORECASE)
                for part in parts:
                    if 'THEN:' in part.upper():
                        cond, res = re.split(r'THEN:', part, flags=re.IGNORECASE, maxsplit=1)
                        scenarios.append({'if': cond.strip(), 'then': res.strip()})
            else:
                scenarios = content
                
            for s in scenarios:
                scenario_elem = ET.SubElement(section_elem, 'scenario', {'if': s.get('if', '')})
                scenario_elem.text = s.get('then', '')
                
        elif stype == 'script':
            steps = []
            if raw:
                for line in raw.split('\n'):
                    match = re.match(r'(\d+)\.\s*(.*)', line)
                    if match:
                        steps.append({'order': int(match.group(1)), 'text': match.group(2)})
            else:
                steps = content
                
            for s in steps:
                step_elem = ET.SubElement(section_elem, 'step', {'order': str(s.get('order', '0'))})
                step_elem.text = s.get('text', '')
                
        elif stype == 'personality':
            if raw:
                tones = []
                style = ""
                filler = "false"
                for line in raw.split('\n'):
                    if 'TONE' in line.upper(): tones = [t.strip() for t in line.split(':', 1)[1].split(',')]
                    if 'STYLE' in line.upper(): style = line.split(':', 1)[1].strip()
                    if 'FILLER' in line.upper(): filler = 'true' if 'true' in line.lower() else 'false'
                
                for t in tones:
                    tone_elem = ET.SubElement(section_elem, 'tone')
                    tone_elem.text = t
                style_elem = ET.SubElement(section_elem, 'style')
                style_elem.text = style
                filler_elem = ET.SubElement(section_elem, 'filler-words')
                filler_elem.text = filler
            else:
                for t in content.get('tones', []):
                    tone_elem = ET.SubElement(section_elem, 'tone')
                    tone_elem.text = t
                style_elem = ET.SubElement(section_elem, 'style')
                style_elem.text = content.get('style', '')
                filler_elem = ET.SubElement(section_elem, 'filler-words')
                filler_elem.text = 'true' if content.get('filler_words') else 'false'
                
        elif stype == 'output':
            max_len = ""
            style = ""
            special = []
            if raw:
                lines = raw.split('\n')
                current_special = False
                for line in lines:
                    line = line.strip()
                    if 'MAX LENGTH' in line.upper(): max_len = line.split(':', 1)[1].strip()
                    elif 'STYLE' in line.upper(): style = line.split(':', 1)[1].strip()
                    elif 'SPECIAL' in line.upper(): current_special = True
                    elif current_special and line.startswith('-'):
                        special.append(line.lstrip('- '))
            else:
                max_len = content.get('max_length', '')
                style = content.get('style', '')
                special = content.get('special', [])
                
            ml_elem = ET.SubElement(section_elem, 'max-length')
            ml_elem.text = max_len
            st_elem = ET.SubElement(section_elem, 'style')
            st_elem.text = style
            for s in special:
                sp_elem = ET.SubElement(section_elem, 'special')
                sp_elem.text = s

    rough_string = ET.tostring(root, 'utf-8')
    from xml.dom import minidom
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="    ")


def replace_placeholders(text: str, data: Dict[str, str]) -> str:
    """Replace {{placeholder}} with actual values from data dict."""
    def replacer(match):
        key = match.group(1)
        return data.get(key, match.group(0))
    
    return re.sub(r'\{\{(\w+)\}\}', replacer, text)
