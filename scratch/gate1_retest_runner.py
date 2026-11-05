"""
gate1_retest_runner.py
======================
Controlled Retest for Failed Providers (Groq, NVIDIA, Gemini).
Excludes Cloudflare Workers AI (already verified in Gate 1).
Maximum external inference requests: exactly 3 (1 Groq, 1 NVIDIA, 1 Gemini).
Zero retries, zero fallback, zero second model attempts, zero replacement loops.
Zero credential exposure.
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
from services.providers.base import extract_json_from_text

# Gate 0 Pre-execution assertions
assert not is_production_mode(), "FATAL: Production mode active!"
assert get_db_path().name == "staging_gate1.db", "FATAL: Not staging database!"
assert not os.getenv("DATABASE_URL"), "FATAL: DATABASE_URL leak!"
assert not os.getenv("MYSQL_DATABASE_URL"), "FATAL: MYSQL_DATABASE_URL leak!"
assert not os.getenv("RENDER"), "FATAL: RENDER environment active!"

groq_key = os.getenv("GROQ_API_KEY", "")
nvidia_key = os.getenv("NVIDIA_API_KEY", "")
gemini_key = os.getenv("GEMINI_API_KEY", "")

assert groq_key, "FATAL: GROQ_API_KEY missing!"
assert nvidia_key, "FATAL: NVIDIA_API_KEY missing!"
assert gemini_key, "FATAL: GEMINI_API_KEY missing!"

TEST_PROMPT = (
    "Generate a 5-question multiple-choice quiz on 'Inverse of a Matrix' for an undergraduate Linear Algebra course. "
    "Output valid JSON conforming strictly to: "
    '{"questions": [{"question": "...", "options": ["A) ...", "B) ...", "C) ...", "D) ..."], "difficulty": "easy", "concept": "Invertibility"}]} '
    "Do not include markdown code fences or conversational text."
)

results = {}
total_external_calls = 0

print("=" * 60)
print("[GATE 1 RETEST] Controlled 3-Provider Retest Initializing...")
print(f"Staging DB: {get_db_path()}")
print(f"Production Guard: {not is_production_mode()}")
print("=" * 60)

# -------------------------------------------------------------
# 1. Groq Retest (openai/gpt-oss-20b) - Exactly 1 request
# -------------------------------------------------------------
print("\n[RETEST 1/3] Testing Groq (openai/gpt-oss-20b)...")
groq_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
avail_before, _ = quota_manager.is_model_available("groq", groq_model, groq_key)

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
    "error": None,
    "response_preview": None
}

try:
    from groq import Groq
    groq_client = Groq(api_key=groq_key, timeout=15.0)

    total_external_calls += 1
    groq_record["external_calls"] = 1
    assert total_external_calls <= 3, "Exceeded total 3 call limit!"

    t0 = time.perf_counter()
    resp = groq_client.chat.completions.create(
        model=groq_model,
        messages=[
            {"role": "system", "content": "You are a quiz generator. Output valid JSON."},
            {"role": "user", "content": TEST_PROMPT}
        ],
        temperature=0.3,
        max_tokens=2500,
        response_format={"type": "json_object"}
    )
    latency = time.perf_counter() - t0
    groq_record["latency_sec"] = round(latency, 3)
    groq_record["http_status"] = 200

    content = resp.choices[0].message.content or ""
    groq_record["response_preview"] = content[:150].replace("\n", " ")
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
    groq_record["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    groq_record["http_status"] = getattr(getattr(e, "response", None), "status_code", getattr(e, "status_code", 500))
    print(f"  -> FAILED: {type(e).__name__} (status={groq_record['http_status']}): {str(e)[:150]}")

avail_after, _ = quota_manager.is_model_available("groq", groq_model, groq_key)
groq_record["quota_after"] = avail_after
results["groq"] = groq_record


# -------------------------------------------------------------
# 2. NVIDIA NIM Retest (meta/llama-3.1-8b-instruct) - Exactly 1 request
# -------------------------------------------------------------
print("\n[RETEST 2/3] Testing NVIDIA NIM (meta/llama-3.1-8b-instruct)...")
nvidia_model = os.getenv("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct")
avail_before, _ = quota_manager.is_model_available("nvidia", nvidia_model, nvidia_key)

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
    "error": None,
    "response_preview": None
}

try:
    from openai import OpenAI
    nvidia_client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=nvidia_key,
        timeout=16.0
    )

    total_external_calls += 1
    nvidia_record["external_calls"] = 1
    assert total_external_calls <= 3, "Exceeded total 3 call limit!"

    t0 = time.perf_counter()
    resp = nvidia_client.chat.completions.create(
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
    nvidia_record["response_preview"] = content[:150].replace("\n", " ")
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
    nvidia_record["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    nvidia_record["http_status"] = getattr(getattr(e, "response", None), "status_code", getattr(e, "status_code", 500))
    print(f"  -> FAILED: {type(e).__name__} (status={nvidia_record['http_status']}): {str(e)[:150]}")

avail_after, _ = quota_manager.is_model_available("nvidia", nvidia_model, nvidia_key)
nvidia_record["quota_after"] = avail_after
results["nvidia"] = nvidia_record


# -------------------------------------------------------------
# 3. Google Gemini Retest (gemini-3.8-flash) - Exactly 1 request
# -------------------------------------------------------------
print("\n[RETEST 3/3] Testing Google Gemini (gemini-3.8-flash)...")
gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
avail_before, _ = quota_manager.is_model_available("gemini", gemini_model, gemini_key)

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
    "error": None,
    "response_preview": None
}

try:
    from google import genai
    from google.genai import types
    gemini_client = genai.Client(api_key=gemini_key)

    total_external_calls += 1
    gemini_record["external_calls"] = 1
    assert total_external_calls <= 3, "Exceeded total 3 call limit!"

    t0 = time.perf_counter()
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        http_options=types.HttpOptions(timeout=20000)
    )
    resp = gemini_client.models.generate_content(
        model=gemini_model,
        contents=TEST_PROMPT,
        config=config
    )
    latency = time.perf_counter() - t0
    gemini_record["latency_sec"] = round(latency, 3)
    gemini_record["http_status"] = 200

    content = resp.text or ""
    gemini_record["response_preview"] = content[:150].replace("\n", " ")
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
    gemini_record["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    gemini_record["http_status"] = getattr(getattr(e, "response", None), "status_code", getattr(e, "code", 500))
    print(f"  -> FAILED: {type(e).__name__} (status={gemini_record['http_status']}): {str(e)[:150]}")

avail_after, _ = quota_manager.is_model_available("gemini", gemini_model, gemini_key)
gemini_record["quota_after"] = avail_after
results["gemini"] = gemini_record


# -------------------------------------------------------------
# Summary and Persistence
# -------------------------------------------------------------
print("\n" + "=" * 60)
print(f"[RETEST COMPLETE] Total External Inference Requests: {total_external_calls}/3")
print("=" * 60)
for p, rec in results.items():
    status_str = f"HTTP {rec['http_status']}" if rec['http_status'] else "N/A"
    res_str = "PASS" if rec['success'] else "FAIL"
    print(f" - {p.upper():<10} | {rec['model']:<30} | {status_str:<10} | {rec['latency_sec']:>6.2f}s | {res_str}")

out_data = {
    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    "total_external_calls": total_external_calls,
    "results": results
}
out_path = Path("scratch/gate1_retest_results.json")
out_path.write_text(json.dumps(out_data, indent=2), encoding="utf-8")
print(f"\nSaved results to {out_path}")
