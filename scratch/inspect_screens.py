with open("frontend/js/app.js", "r", encoding="utf-8") as f:
    text = f.read()

import re
matches = [m.start() for m in re.finditer(r'showScreen\(', text)]
screens = set()
for m in matches:
    end = text.find(')', m)
    screens.add(text[m:end+1])

print("Screens referenced in showScreen calls:")
for s in sorted(screens):
    print(" ", s)
