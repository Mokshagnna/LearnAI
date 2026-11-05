with open('backend/services/auth_service.py', 'r', encoding='utf-8') as f:
    for line in f:
        if 'register' in line.lower() or 'def login' in line.lower():
            print(line.strip())
