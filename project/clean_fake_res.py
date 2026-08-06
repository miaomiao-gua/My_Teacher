# -*- coding: utf-8 -*-
import json, glob, os

for cfg_path in glob.glob('lessons/*/config.json'):
    try:
        cfg = json.load(open(cfg_path, encoding='utf-8'))
    except Exception:
        continue
    folder = os.path.basename(os.path.dirname(cfg_path))
    resources = cfg.get('resources', []) or []
    fake = [r for r in resources if 'example.com' in (r.get('url') or '')]
    if fake:
        print(f'{folder}: {len(fake)} 个假资源 (example.com)')
        for r in fake:
            print(f'  - {r.get("title")} | {r.get("url")}')
        # 清理假资源
        cfg['resources'] = [r for r in resources if 'example.com' not in (r.get('url') or '')]
        # 同时清理 units 里的假资源
        for u in cfg.get('units', []) or []:
            sfs = u.get('source_files', []) or []
            u['source_files'] = [sf for sf in sfs if 'example.com' not in (sf.get('url') or '')]
        json.dump(cfg, open(cfg_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print(f'  ✅ 已清理 {len(fake)} 个假资源')
