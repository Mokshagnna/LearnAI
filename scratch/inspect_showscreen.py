import sys
sys.stdout.reconfigure(encoding='utf-8')
with open("frontend/js/app.js", "r", encoding="utf-8") as f:
    text = f.read()

idx = text.find('function showScreen(')
print(text[idx:idx+1500])
