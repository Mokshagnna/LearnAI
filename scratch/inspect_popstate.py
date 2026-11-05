import sys
sys.stdout.reconfigure(encoding='utf-8')
with open("frontend/js/app.js", "r", encoding="utf-8") as f:
    text = f.read()

idx = text.find('popstate')
while idx != -1:
    print("=== popstate match ===")
    print(text[max(0, idx-100):idx+400])
    idx = text.find('popstate', idx+1)
