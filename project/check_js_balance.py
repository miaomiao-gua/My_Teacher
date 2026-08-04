"""Find where JS syntax imbalance occurs"""
with open('static/js/app.js', 'r', encoding='utf-8') as f:
    content = f.read()

lines = content.split('\n')

paren = 0
brackets = 0
braces = 0
in_str = False
str_char = None
escape = False
in_template = False

for line_num, line in enumerate(lines, 1):
    for j, c in enumerate(line):
        if escape:
            escape = False
            continue
        if c == '\\' and in_str:
            escape = True
            continue
        if in_template:
            if c == '`':
                in_template = False
            continue
        if in_str:
            if c == str_char:
                in_str = False
            continue
        if c == '`':
            in_template = True
            continue
        if c in ('"', "'"):
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
    
    # Check after each line
    if line_num <= 10 or abs(paren) > 10 or abs(brackets) > 10 or abs(braces) > 10:
        if 'function' in line or '=>' in line or 'const ' in line or 'let ' in line or 'var ' in line:
            pass  # Log interesting lines
    
    # Print lines where balance changes significantly
    if abs(paren) + abs(brackets) + abs(braces) > 50 and line_num % 50 == 0:
        print(f'Line {line_num}: paren={paren}, brackets={brackets}, braces={braces}')

print(f'\nFinal: paren={paren}, brackets={brackets}, braces={braces}')
print(f'Imbalance starts becoming significant at certain lines')

# Find the line where paren first goes negative
paren2 = 0
for line_num, line in enumerate(lines, 1):
    in_str2 = False
    str_char2 = None
    escape2 = False
    in_template2 = False
    for j, c in enumerate(line):
        if escape2:
            escape2 = False
            continue
        if c == '\\' and in_str2:
            escape2 = True
            continue
        if in_template2:
            if c == '`':
                in_template2 = False
            continue
        if in_str2:
            if c == str_char2:
                in_str2 = False
            continue
        if c == '`':
            in_template2 = True
            continue
        if c in ('"', "'"):
            in_str2 = True
            str_char2 = c
            continue
        if c == '(':
            paren2 += 1
        elif c == ')':
            paren2 -= 1
    if paren2 < 0 and line_num < 200:
        print(f'Paren goes negative at line {line_num}: paren={paren2}')
        print(f'  Content: {line.rstrip()[:100]}')
        break

# Find the line where braces first goes negative
braces2 = 0
for line_num, line in enumerate(lines, 1):
    in_str2 = False
    str_char2 = None
    escape2 = False
    in_template2 = False
    for j, c in enumerate(line):
        if escape2:
            escape2 = False
            continue
        if c == '\\' and in_str2:
            escape2 = True
            continue
        if in_template2:
            if c == '`':
                in_template2 = False
            continue
        if in_str2:
            if c == str_char2:
                in_str2 = False
            continue
        if c == '`':
            in_template2 = True
            continue
        if c in ('"', "'"):
            in_str2 = True
            str_char2 = c
            continue
        if c == '{':
            braces2 += 1
        elif c == '}':
            braces2 -= 1
    if braces2 < 0:
        print(f'Braces goes negative at line {line_num}: braces={braces2}')
        print(f'  Content: {line.rstrip()[:100]}')
        break

print('\nDone')