import sys
sys.path.insert(0, 'backend')

from services.providers.base import extract_json_from_text, calculate_max_output_tokens

# Test schema parsing & sanitization
valid_json_text = '{"questions": [{"question": "What is an inverse?", "options": ["A", "B", "C", "D"], "difficulty": "easy", "concept": "Invertibility"}]}'
res = extract_json_from_text(valid_json_text)
assert isinstance(res, dict) and 'questions' in res
print('1. Valid JSON extracted:', len(res['questions']), 'questions')

# Test markdown fenced JSON
fenced_text = '```json\n' + valid_json_text + '\n```'
res_fenced = extract_json_from_text(fenced_text)
assert isinstance(res_fenced, dict) and len(res_fenced['questions']) == 1
print('2. Fenced JSON extracted:', len(res_fenced['questions']), 'questions')

# Test malformed JSON
res_malformed = extract_json_from_text('Not a json string at all')
assert res_malformed is None or res_malformed == {}
print('3. Malformed JSON handled gracefully:', res_malformed)

# Test truncated JSON
truncated_text = '{"questions": [{"question": "incomplete...'
res_trunc = extract_json_from_text(truncated_text)
print('4. Truncated JSON handled gracefully:', res_trunc)
