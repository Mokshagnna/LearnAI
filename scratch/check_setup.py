with open('frontend/js/app.js', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if 'showScreen("setup")' in line:
            print(f"{i+1}: {line.strip()}")
