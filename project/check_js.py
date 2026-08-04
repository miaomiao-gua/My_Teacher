"""Check JavaScript syntax and function existence"""
with open('static/js/app.js', 'r', encoding='utf-8') as f:
    content = f.read()

# Check for balanced braces
braces = 0
in_string = False
string_char = None
escape_next = False
for i, c in enumerate(content):
    if escape_next:
        escape_next = False
        continue
    if c == '\\':
        escape_next = True
        continue
    if in_string:
        if c == string_char:
            in_string = False
    elif c in ('"', "'"):
        in_string = True
        string_char = c
    elif c == '{':
        braces += 1
    elif c == '}':
        braces -= 1

print(f'Brace balance: {braces} (should be 0)')
print(f'File size: {len(content)} chars')
print(f'Lines: {content.count(chr(10))}')

# Check if submitExam function exists
if 'function submitExam' in content:
    print('submitExam function found')
else:
    print('WARNING: submitExam function NOT found!')

# Check if generateExam function exists
if 'function generateExam' in content:
    print('generateExam function found')
else:
    print('WARNING: generateExam function NOT found!')

# Check for event listener
if "addEventListener('click', submitExam)" in content:
    print('Click event listener found on submit button')
else:
    print('WARNING: Click event listener NOT found!')

# Check for extractMath function
if 'function extractMath' in content:
    print('extractMath function found')
else:
    print('WARNING: extractMath function NOT found!')

# Check for renderMathItems
if 'function renderMathItems' in content:
    print('renderMathItems function found')
else:
    print('WARNING: renderMathItems function NOT found!')

print('\nDone')