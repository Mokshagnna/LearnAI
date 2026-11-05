with open("frontend/index.html", "r", encoding="utf-8") as f:
    text = f.read()

idx = text.find('id="quizSection"')
idx2 = text.find('</section>', idx)
print(text[idx-50:idx2+10])
