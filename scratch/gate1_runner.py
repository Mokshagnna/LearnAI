"""
gate1_runner.py
===============
Gate 1: Controlled Four-Provider Real AI Smoke Test Runner.
Strictly limited to:
- Groq: exactly 1 request
- NVIDIA NIM: exactly 1 request
- Google Gemini: exactly 1 request
- Cloudflare Workers AI: exactly 1 request
Maximum total: 4 external requests.
No retries, no automatic fallback, no secondary model, no replacement loops.
Zero secret leakage.
"""

import os
import sys
import time
import json
from pathlib import Path

# Explicitly enforce staging environment
os.environ["LEARNAI_ENV_FILE"] = "backend/.env.staging.local"

backend_dir = Path("backend").resolve()
sys.path.insert(0, str(backend_dir))

from services.env_loader import resolve_and_load_env
resolve_and_load_env(backend_dir)

from services.database import is_production_mode, get_db_path
from services.quota_manager import quota_manager
from services.providers.base import extract_json_from_text, ProviderUnavailableError, ProviderConfigError

# Verify Gate 0 invariants before executing any network requests
assert not is_production_mode(), "FATAL: Production mode active!"
assert get_db_path().name == "staging_gate1.db", "FATAL: Not staging database!"
assert not os.getenv("DATABASE_URL"), "FATAL: DATABASE_URL leak!"
assert not os.getenv("RENDER"), "FATAL: RENDER environment active!"

TEST_PROMPT = (
    "Generate a 5-question multiple-choice quiz on 'Inverse of a Matrix' for an undergraduate Linear Algebra course. "
    "Output valid JSON conforming strictly to: "
    '{"questions": [{"question": "...", "options": ["A) ...", "B) ...", "C) ...", "D) ..."], "difficulty": "easy", "concept": "Invertibility"}]} '
    "Do not include markdown code fences or conversational text."
)

results = {}
total_external_calls = 0

# -------------------------------------------------------------
# 1. Groq Smoke Test (1 request max)
# -------------------------------------------------------------
print("\n[GATE 1] 1/4 Testing Groq...")
groq_model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
groq_key = os.getenv("GROQ_API_KEY", "")
avail_before, _ = quota_manager.is_model_available("groq", groq_model, groq_key)

groq_calls = 0
groq_record = {
    "provider": "groq",
    "model": groq_model,
    "quota_before": avail_before,
    "external_calls": 0,
    "http_status": None,
    "success": False,
    "latency_sec": 0.0,
    "parse_success": False,
    "question_count": 0,
    "tokens": {},
    "error": None
}

if not groq_key:
    groq_record["error"] = "GROQ_API_KEY missing"
    print("  -> SKIPPED: GROQ_API_KEY missing")
else:
    try:
        from groq import Groq
        client = Groq(api_key=groq_key, timeout=15.0)

        # Enforce exactly 1 call
        groq_calls += 1
        total_external_calls += 1
        assert groq_calls <= 1, "Exceeded Groq single call limit"
        assert total_external_calls <= 4, "Exceeded total 4 call limit"
        groq_record["external_calls"] = groq_calls

        t0 = time.perf_counter()
        resp = client.chat.completions.create(
            model=groq_model,
            messages=[{"role": "user", "content": TEST_PROMPT}],
            temperature=0.3,
            max_tokens=2500,
            response_format={"type": "json_object"}
        )
        latency = time.perf_counter() - t0
        groq_record["latency_sec"] = round(latency, 3)
        groq_record["http_status"] = 200

        content = resp.choices[0].message.content or ""
        parsed = extract_json_from_text(content)
        questions = parsed.get("questions", []) if isinstance(parsed, dict) else []
        groq_record["parse_success"] = bool(questions)
        groq_record["question_count"] = len(questions)
        groq_record["success"] = bool(questions)

        if hasattr(resp, "usage") and resp.usage:
            groq_record["tokens"] = {
                "prompt": getattr(resp.usage, "prompt_tokens", None),
                "completion": getattr(resp.usage, "completion_tokens", None),
                "total": getattr(resp.usage, "total_tokens", None)
            }
        quota_manager.record_success("groq", groq_model, groq_key)
        print(f"  -> SUCCESS: HTTP 200 in {latency:.2f}s, {len(questions)} questions parsed, tokens: {groq_record['tokens']}")
    except Exception as e:
        groq_record["error"] = str(type(e).__name__)
        groq_record["http_status"] = getattr(getattr(e, "response", None), "status_code", 500)
        print(f"  -> FAILED: {type(e).__name__} (status={groq_record['http_status']})")

avail_after, _ = quota_manager.is_model_available("groq", groq_model, groq_key)
groq_record["quota_after"] = avail_after
results["groq"] = groq_record


# -------------------------------------------------------------
# 2. NVIDIA NIM Smoke Test (1 request max)
# -------------------------------------------------------------
print("\n[GATE 1] 2/4 Testing NVIDIA NIM...")
nvidia_model = os.getenv("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct")
nvidia_key = os.getenv("NVIDIA_API_KEY", "")
avail_before, _ = quota_manager.is_model_available("nvidia", nvidia_model, nvidia_key)

nvidia_calls = 0
nvidia_record = {
    "provider": "nvidia",
    "model": nvidia_model,
    "quota_before": avail_before,
    "external_calls": 0,
    "http_status": None,
    "success": False,
    "latency_sec": 0.0,
    "parse_success": False,
    "question_count": 0,
    "tokens": {},
    "error": None
}

if not nvidia_key:
    nvidia_record["error"] = "NVIDIA_API_KEY missing"
    print("  -> SKIPPED: NVIDIA_API_KEY missing")
else:
    try:
        from openai import OpenAI
        client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=nvidia_key,
            timeout=16.0
        )

        nvidia_calls += 1
        total_external_calls += 1
        assert nvidia_calls <= 1, "Exceeded NVIDIA single call limit"
        assert total_external_calls <= 4, "Exceeded total 4 call limit"
        nvidia_record["external_calls"] = nvidia_calls

        t0 = time.perf_counter()
        resp = client.chat.completions.create(
            model=nvidia_model,
            messages=[
                {"role": "system", "content": "You are a quiz generator. Output valid JSON."},
                {"role": "user", "content": TEST_PROMPT}
            ],
            temperature=0.3,
            max_tokens=2500
        )
        latency = time.perf_counter() - t0
        nvidia_record["latency_sec"] = round(latency, 3)
        nvidia_record["http_status"] = 200

        content = resp.choices[0].message.content or ""
        parsed = extract_json_from_text(content)
        questions = parsed.get("questions", []) if isinstance(parsed, dict) else []
        nvidia_record["parse_success"] = bool(questions)
        nvidia_record["question_count"] = len(questions)
        nvidia_record["success"] = bool(questions)

        if hasattr(resp, "usage") and resp.usage:
            nvidia_record["tokens"] = {
                "prompt": getattr(resp.usage, "prompt_tokens", None),
                "completion": getattr(resp.usage, "completion_tokens", None),
                "total": getattr(resp.usage, "total_tokens", None)
            }
        quota_manager.record_success("nvidia", nvidia_model, nvidia_key)
        print(f"  -> SUCCESS: HTTP 200 in {latency:.2f}s, {len(questions)} questions parsed, tokens: {nvidia_record['tokens']}")
    except Exception as e:
        nvidia_record["error"] = str(type(e).__name__)
        nvidia_record["http_status"] = getattr(getattr(e, "response", None), "status_code", 500)
        print(f"  -> FAILED: {type(e).__name__} (status={nvidia_record['http_status']})")

avail_after, _ = quota_manager.is_model_available("nvidia", nvidia_model, nvidia_key)
nvidia_record["quota_after"] = avail_after
results["nvidia"] = nvidia_record


# -------------------------------------------------------------
# 3. Google Gemini Smoke Test (1 request max)
# -------------------------------------------------------------
print("\n[GATE 1] 3/4 Testing Google Gemini...")
gemini_model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
gemini_key = os.getenv("GEMINI_API_KEY", "")
avail_before, _ = quota_manager.is_model_available("gemini", gemini_model, gemini_key)

gemini_calls = 0
gemini_record = {
    "provider": "gemini",
    "model": gemini_model,
    "quota_before": avail_before,
    "external_calls": 0,
    "http_status": None,
    "success": False,
    "latency_sec": 0.0,
    "parse_success": False,
    "question_count": 0,
    "tokens": {},
    "error": None
}

if not gemini_key:
    gemini_record["error"] = "GEMINI_API_KEY missing"
    print("  -> SKIPPED: GEMINI_API_KEY missing")
else:
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=gemini_key)

        gemini_calls += 1
        total_external_calls += 1
        assert gemini_calls <= 1, "Exceeded Gemini single call limit"
        assert total_external_calls <= 4, "Exceeded total 4 call limit"
        gemini_record["external_calls"] = gemini_calls

        t0 = time.perf_counter()
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            http_options=types.HttpOptions(timeout=20000)
        )
        resp = client.models.generate_content(
            model=gemini_model,
            contents=TEST_PROMPT,
            config=config
        )
        latency = time.perf_counter() - t0
        gemini_record["latency_sec"] = round(latency, 3)
        gemini_record["http_status"] = 200

        content = resp.text or ""
        parsed = extract_json_from_text(content)
        questions = parsed.get("questions", []) if isinstance(parsed, dict) else []
        gemini_record["parse_success"] = bool(questions)
        gemini_record["question_count"] = len(questions)
        gemini_record["success"] = bool(questions)

        usage = getattr(resp, "usage_metadata", None)
        if usage:
            gemini_record["tokens"] = {
                "prompt": getattr(usage, "prompt_token_count", None),
                "completion": getattr(usage, "candidates_token_count", None),
                "total": getattr(usage, "total_token_count", None)
            }
        quota_manager.record_success("gemini", gemini_model, gemini_key)
        print(f"  -> SUCCESS: HTTP 200 in {latency:.2f}s, {len(questions)} questions parsed, tokens: {gemini_record['tokens']}")
    except Exception as e:
        gemini_record["error"] = str(type(e).__name__)
        gemini_record["http_status"] = getattr(getattr(e, "response", None), "status_code", 500)
        print(f"  -> FAILED: {type(e).__name__} (status={gemini_record['http_status']})")

avail_after, _ = quota_manager.is_model_available("gemini", gemini_model, gemini_key)
gemini_record["quota_after"] = avail_after
results["gemini"] = gemini_record


# -------------------------------------------------------------
# 4. Cloudflare Workers AI Smoke Test (1 request max)
# -------------------------------------------------------------
print("\n[GATE 1] 4/4 Testing Cloudflare Workers AI...")
cf_model = os.getenv("CLOUDFLARE_MODEL", "@cf/meta/llama-3.1-8b-instruct")
cf_token = os.getenv("CLOUDFLARE_API_TOKEN", "")
cf_account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
avail_before, _ = quota_manager.is_model_available("cloudflare", cf_model, cf_token)

cf_calls = 0
cf_record = {
    "provider": "cloudflare",
    "model": cf_model,
    "quota_before": avail_before,
    "external_calls": 0,
    "http_status": None,
    "success": False,
    "latency_sec": 0.0,
    "parse_success": False,
    "question_count": 0,
    "tokens": {},
    "error": None
}

if not cf_token or not cf_account:
    cf_record["error"] = "CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID missing"
    print("  -> SKIPPED: Cloudflare credentials missing")
else:
    try:
        import httpx
        url = f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/run/{cf_model}"
        headers = {
            "Authorization": f"Bearer {cf_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "prompt": TEST_PROMPT,
            "max_tokens": 2000
        }

        cf_calls += 1
        total_external_calls += 1
        assert cf_calls <= 1, "Exceeded Cloudflare single call limit"
        assert total_external_calls <= 4, "Exceeded total 4 call limit"
        cf_record["external_calls"] = cf_calls

        t0 = time.perf_counter()
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(url, headers=headers, json=payload)
        latency = time.perf_counter() - t0
        cf_record["latency_sec"] = round(latency, 3)
        cf_record["http_status"] = resp.status_code

        if resp.status_code == 200:
            data = resp.json()
            raw_text = ""
            if isinstance(data, dict):
                res_obj = data.get("result", {})
                if isinstance(res_obj, dict):
                    raw_text = res_obj.get("response", "")
            parsed = extract_json_from_text(raw_text)
            questions = parsed.get("questions", []) if isinstance(parsed, dict) else []
            cf_record["parse_success"] = bool(questions)
            cf_record["question_count"] = len(questions)
            cf_record["success"] = bool(questions)
            quota_manager.record_success("cloudflare", cf_model, cf_token)
            print(f"  -> SUCCESS: HTTP 200 in {latency:.2f}s, {len(questions)} questions parsed")
        else:
            cf_record["error"] = f"HTTP_{resp.status_code}"
            print(f"  -> FAILED: HTTP {resp.status_code} in {latency:.2f}s")
    except Exception as e:
        cf_record["error"] = str(type(e).__name__)
        cf_record["http_status"] = 500
        print(f"  -> FAILED: {type(e).__name__}")

avail_after, _ = quota_manager.is_model_available("cloudflare", cf_model, cf_token)
cf_record["quota_after"] = avail_after
results["cloudflare"] = cf_record

print(f"\n[GATE 1 SUMMARY] Total external calls issued: {total_external_calls} / 4 max")
with open("scratch/gate1_results.json", "w", encoding="utf-8") as f:
    json.dump({
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_external_calls": total_external_calls,
        "results": results
    }, f, indent=2)
print("Saved summary to scratch/gate1_results.json")
