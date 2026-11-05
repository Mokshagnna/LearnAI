import sys
sys.path.insert(0, 'backend')

from services.providers.groq_provider import GroqProvider
from services.providers.cloudflare_provider import CloudflareProvider
from services.providers.nvidia_provider import NvidiaProvider
from services.providers.gemini_provider import GeminiProvider

gp = GroqProvider()
cf = CloudflareProvider()
nv = NvidiaProvider()
gem = GeminiProvider()

class MockHTTPError(Exception):
    def __init__(self, status_code, message):
        self.status_code = status_code
        self.code = status_code
        super().__init__(message)

test_cases = [
    (400, 'Bad Request: invalid payload'),
    (401, 'Unauthorized: invalid api key'),
    (403, 'Forbidden: permission denied'),
    (404, 'Not Found: model not found'),
    (408, 'Request Timeout'),
    (410, 'Gone: model deprecated'),
    (429, 'Too Many Requests: rate limit exceeded'),
    (500, 'Internal Server Error'),
    (502, 'Bad Gateway'),
    (503, 'Service Unavailable: high demand'),
    (504, 'Gateway Timeout'),
]

header = f"{'Status':<8} | {'Groq Cat':<24} | {'CF Cat':<24} | {'NV Cat':<24} | {'Gem Cat':<24}"
print(header)
print('-' * len(header))
for code, msg in test_cases:
    err = MockHTTPError(code, msg)
    g_cat = gp._categorize(err)
    c_cat = cf._categorize(err)
    n_cat = nv._categorize(err)
    m_cat = gem._categorize(err)
    print(f"{code:<8} | {g_cat:<24} | {c_cat:<24} | {n_cat:<24} | {m_cat:<24}")

print("\n--- RETRYABILITY CHECK ---")
header_ret = f"{'Status':<8} | {'Groq Retry':<12} | {'CF Retry':<12} | {'NV Retry':<12} | {'Gem Retry':<12}"
print(header_ret)
print('-' * len(header_ret))
for code, msg in test_cases:
    err = MockHTTPError(code, msg)
    print(f"{code:<8} | {str(gp._is_retryable(err)):<12} | {str(cf._is_retryable(err)):<12} | {str(nv._is_retryable(err)):<12} | {str(gem._is_retryable(err)):<12}")
