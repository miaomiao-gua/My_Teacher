"""Test exam APIs after code changes"""
import requests
import json

BASE = 'http://127.0.0.1:5000'

# Test the APIs
print('=== Testing /api/exam/generate ===')
resp = requests.post(f'{BASE}/api/exam/generate', json={'topic': 'Python 基础'}, timeout=120)
data = resp.json()
print(f'Status: {resp.status_code}')
print(f'Questions count: {len(data.get("questions", []))}')
for i, q in enumerate(data.get('questions', [])):
    qtype = q.get('type', 'single')
    question = q.get('question', '')[:50]
    options = q.get('options', [])
    print(f'  Q{i}: type={qtype}, q={question}...')
    print(f'    options={len(options)} items')
    for opt in options:
        print(f'      - {opt}')
print()

# Test submission
print('=== Testing /api/exam/submit ===')
answers = ['A']  # Answer for single-choice question
resp2 = requests.post(f'{BASE}/api/exam/submit', json={'answers': answers}, timeout=60)
data2 = resp2.json()
print(f'Result: {json.dumps(data2, ensure_ascii=False, indent=2)}')