import json
import os

with open('D:\\Pikorua\\AI Digital Marketing\\pikorua-adflow\\graphify-out\\graph.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

roadmap_id = None
for node in data.get('nodes', []):
    if 'AUTOOPTIMISER_ROADMAP.md' in str(node.get('id', '')) or 'AUTOOPTIMISER_ROADMAP.md' in str(node.get('name', '')):
        roadmap_id = node.get('id')
        print(f"Found node: {node}")
        break

if roadmap_id:
    connected_ids = set()
    for link in data.get('links', []):
        if link.get('source') == roadmap_id:
            connected_ids.add(link.get('target'))
        elif link.get('target') == roadmap_id:
            connected_ids.add(link.get('source'))
    
    print(f"\nConnected IDs ({len(connected_ids)}):")
    for node in data.get('nodes', []):
        if node.get('id') in connected_ids:
            print(f"  - {node.get('id')} ({node.get('type', 'Unknown')})")
else:
    print('AUTOOPTIMISER_ROADMAP.md not found in graph nodes.')
