with open('backend/test_persistent_auth_and_quiz_restoration.py', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if 'register' in line.lower() or 'login' in line.lower() or 'password' in line.lower():
            if i < 100:
                print(f"{i+1}: {line.strip()}")
