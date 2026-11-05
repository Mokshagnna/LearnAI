import os, sys
sys.path.insert(0, 'backend')
from services.providers.base import BaseAIProvider
import services.llm_service as llm

class DeterministicMockProvider(BaseAIProvider):
    @property
    def name(self): return 'mock'
    def is_configured(self): return True
    def generate_quiz_raw(self, **kwargs):
        return '{"questions": [{"question": "Test Question", "options": ["A. 1", "B. 2", "C. 3", "D. 4"], "correct_answer": "A", "difficulty": "Easy", "concept": "TestConcept"}]}'
    def generate_analysis_raw(self, **kwargs):
        return '{"summary": "Analysis summary", "strengths": ["Concepts"], "weaknesses": []}'

llm._CACHED_PROVIDERS['mock'] = DeterministicMockProvider()
os.environ['PROVIDER_ORDER'] = 'mock'
providers = llm._get_providers()
print('Active providers:', [p.name for p in providers])
assert providers[0].name == 'mock'
print('Mock provider injection verified!')
