"""JS syntax check"""
import re

with open('static/js/app.js', 'r', encoding='utf-8') as f:
    content = f.read()

# Check for common issues
lines = content.split('\n')
print(f'Total lines: {len(lines)}')
print(f'Total chars: {len(content)}')

# Check for template literals issues
# Look for backtick strings that might have issues
in_template = False
template_start = -1
for i, line in enumerate(lines, 1):
    for j, c in enumerate(line):
        if c == '`':
            if not in_template:
                in_template = True
                template_start = i
            else:
                in_template = False
                template_start = -1

if in_template:
    print(f'WARNING: Unclosed template literal starting at line {template_start}')
else:
    print('Template literals: OK')

# Check for balanced parentheses
paren = 0
brackets = 0
braces = 0
in_str = False
str_char = None
escape = False
for i, c in enumerate(content):
    if escape:
        escape = False
        continue
    if c == '\\':
        escape = True
        continue
    if in_str:
        if c == str_char:
            in_str = False
        continue
    if c in ('"', "'", '`'):
        in_str = True
        str_char = c
        continue
    if c == '(':
        paren += 1
    elif c == ')':
        paren -= 1
    elif c == '[':
        brackets += 1
    elif c == ']':
        brackets -= 1
    elif c == '{':
        braces += 1
    elif c == '}':
        braces -= 1

print(f'Parentheses: {paren} (should be 0)')
print(f'Brackets: {brackets} (should be 0)')
print(f'Braces: {braces} (should be 0)')

# Search for lines with ! that might cause issues
for i, line in enumerate(lines, 1):
    stripped = line.strip()
    # Check for standalone ! that looks suspicious
    if '!' in stripped and '!!' not in stripped and '!==' not in stripped and '!=' not in stripped and '!' not in ('!=', '!=='):
        # Check for common patterns
        pass  # Too many false positives

print('\nSyntax check complete')