"""
test_multiprocess_quota.py
==========================
Comprehensive Multiprocessing Concurrency & Integrity Audit for ProviderQuotaManager.
Tests cross-process coordination, race conditions, file locking, and lost updates.
"""

import os
import sys
import time
import json
import shutil
import tempfile
import multiprocessing as mp
from pathlib import Path

backend_dir = Path("backend").resolve()
sys.path.insert(0, str(backend_dir))

from services.quota_manager import ProviderQuotaManager, RateLimitInfo, RateLimitType


def worker_record_cooldown(state_file_path, provider, model, key, duration, start_event, result_queue):
    """Worker process that initializes a quota manager and records a cooldown."""
    try:
        # Create an independent quota manager pointing to the shared state file
        qm = ProviderQuotaManager(state_file_path=state_file_path)
        start_event.wait() # Synchronize start across processes
        
        info = RateLimitInfo(
            limit_type=RateLimitType.TPM,
            retry_after_seconds=duration,
            is_daily_exhaustion=False
        )
        applied = qm.record_rate_limit(provider, model, key, info)
        avail, remaining = qm.is_model_available(provider, model, key)
        result_queue.put({
            "pid": os.getpid(),
            "provider": provider,
            "applied": applied,
            "avail": avail,
            "remaining": remaining,
            "success": True
        })
    except Exception as e:
        result_queue.put({
            "pid": os.getpid(),
            "error": f"{type(e).__name__}: {e}",
            "success": False
        })


def worker_read_cooldown(state_file_path, provider, model, key, start_event, result_queue):
    """Worker process that reads availability without recording."""
    try:
        qm = ProviderQuotaManager(state_file_path=state_file_path)
        start_event.wait()
        avail, remaining = qm.is_model_available(provider, model, key)
        result_queue.put({
            "pid": os.getpid(),
            "provider": provider,
            "avail": avail,
            "remaining": remaining,
            "success": True
        })
    except Exception as e:
        result_queue.put({
            "pid": os.getpid(),
            "error": f"{type(e).__name__}: {e}",
            "success": False
        })


def worker_prewarmed_reader(state_file_path, provider, model, key, ready_ev, read_ev, res_q):
    qm = ProviderQuotaManager(state_file_path=state_file_path)
    ready_ev.set() # Signaled that process is loaded with initial state
    read_ev.wait() # Wait until external process modifies disk
    avail, rem = qm.is_model_available(provider, model, key)
    res_q.put({"avail": avail, "remaining": rem})


def run_audit():
    print("=" * 70)
    print("PHASE 6: MULTIPROCESSING QUOTA MANAGER AUDIT")
    print("=" * 70)
    
    test_dir = Path(tempfile.mkdtemp(prefix="learnai_mp_audit_"))
    shared_state = test_dir / "provider_cooldowns_mp.json"
    
    results = {}
    
    # ---------------------------------------------------------
    # TEST 1: Two processes recording distinct provider cooldowns concurrently
    # ---------------------------------------------------------
    print("\n[TEST 1] Concurrent Cooldown Recording (2 distinct processes)...")
    start_event = mp.Event()
    q = mp.Queue()
    
    p1 = mp.Process(target=worker_record_cooldown, args=(str(shared_state), "groq", "model-1", "key1", 30.0, start_event, q))
    p2 = mp.Process(target=worker_record_cooldown, args=(str(shared_state), "nvidia", "model-2", "key2", 45.0, start_event, q))
    
    p1.start()
    p2.start()
    time.sleep(0.1) # Let processes initialize
    start_event.set() # Trigger both simultaneously
    
    p1.join(timeout=5.0)
    p2.join(timeout=5.0)
    
    res1 = q.get() if not q.empty() else None
    res2 = q.get() if not q.empty() else None
    
    # Inspect what ended up on disk in shared_state
    persisted_state = {}
    if shared_state.exists():
        try:
            persisted_state = json.loads(shared_state.read_text(encoding="utf-8"))
        except Exception as e:
            persisted_state = f"CORRUPTED: {e}"
            
    print(f"  Process 1 (Groq): {res1}")
    print(f"  Process 2 (NVIDIA): {res2}")
    print(f"  State file keys on disk: {list(persisted_state.keys()) if isinstance(persisted_state, dict) else persisted_state}")
    
    # Check if both keys survived on disk
    both_survived = isinstance(persisted_state, dict) and len(persisted_state) == 2
    results["test1_concurrent_writes"] = {
        "pass": both_survived,
        "keys_on_disk": len(persisted_state) if isinstance(persisted_state, dict) else 0,
        "note": "PASS if both processes' cooldowns were merged on disk; FAIL if one overwrote the other (lost update)."
    }
    print(f"  -> Outcome: {'PASS' if both_survived else 'FAIL (Lost Update Detected: one process overwrote state)'}")

    # ---------------------------------------------------------
    # TEST 2: Process cross-read (Process B reading cooldown set by Process A)
    # ---------------------------------------------------------
    print("\n[TEST 2] Cross-Process Cooldown Visibility...")
    # Initialize a clean state file with Groq on cooldown
    initial_qm = ProviderQuotaManager(state_file_path=str(shared_state))
    initial_qm.reset_all()
    initial_qm.record_rate_limit(
        "groq", "model-1", "key1",
        RateLimitInfo(limit_type=RateLimitType.TPM, retry_after_seconds=20.0)
    )
    
    # Process B was spawned *after* state file was written, so it loads on init
    q2 = mp.Queue()
    ev2 = mp.Event()
    p_reader = mp.Process(target=worker_read_cooldown, args=(str(shared_state), "groq", "model-1", "key1", ev2, q2))
    p_reader.start()
    ev2.set()
    p_reader.join(timeout=5.0)
    read_res = q2.get() if not q2.empty() else {}
    
    visible_on_startup = (read_res.get("avail") is False and read_res.get("remaining", 0) > 0)
    print(f"  Reader Process Result: {read_res}")
    print(f"  -> Cooldown visible to newly spawned process: {visible_on_startup}")
    results["test2_process_startup_recovery"] = visible_on_startup

    # ---------------------------------------------------------
    # TEST 3: Mid-flight cross-process visibility (pre-existing process)
    # ---------------------------------------------------------
    print("\n[TEST 3] Mid-flight Cross-Process Visibility (Pre-warmed process)...")
    ready_ev = mp.Event()
    read_ev = mp.Event()
    q3 = mp.Queue()
    p_prewarmed = mp.Process(target=worker_prewarmed_reader, args=(str(shared_state), "groq", "model-live", "key-live", ready_ev, read_ev, q3))
    p_prewarmed.start()
    ready_ev.wait(timeout=3.0)
    
    # Now Parent process records a cooldown for "model-live"
    initial_qm.record_rate_limit(
        "groq", "model-live", "key-live",
        RateLimitInfo(limit_type=RateLimitType.TPM, retry_after_seconds=25.0)
    )
    # Now signal prewarmed process to check availability
    read_ev.set()
    p_prewarmed.join(timeout=5.0)
    midflight_res = q3.get() if not q3.empty() else {}
    midflight_detected = (midflight_res.get("avail") is False)
    print(f"  Prewarmed Process result: {midflight_res}")
    print(f"  -> Midflight cooldown detected by sibling process: {midflight_detected}")
    results["test3_midflight_cross_process_visibility"] = midflight_detected
    if not midflight_detected:
        print("     [FINDING]: In-memory caching in prewarmed worker does NOT reload disk on read.")
        print("     [DEPLOYMENT CONSTRAINT]: Single-worker only, or requires shared memory/Redis.")

    # ---------------------------------------------------------
    # TEST 4: Corrupted file recovery
    # ---------------------------------------------------------
    print("\n[TEST 4] Corrupted State File Recovery...")
    shared_state.write_text("{This is corrupted json ::::", encoding="utf-8")
    qm_corrupt = ProviderQuotaManager(state_file_path=str(shared_state))
    # Should not raise exception, should treat all as available
    avail_corrupt, _ = qm_corrupt.is_model_available("groq", "model-1", "key1")
    print(f"  is_model_available on corrupted file: {avail_corrupt} (No exception raised)")
    results["test4_corrupted_file_recovery"] = avail_corrupt

    # ---------------------------------------------------------
    # TEST 5: Missing file recovery
    # ---------------------------------------------------------
    print("\n[TEST 5] Missing State File Recovery...")
    if shared_state.exists():
        shared_state.unlink()
    qm_missing = ProviderQuotaManager(state_file_path=str(shared_state))
    avail_missing, _ = qm_missing.is_model_available("groq", "model-1", "key1")
    print(f"  is_model_available on missing file: {avail_missing}")
    results["test5_missing_file_recovery"] = avail_missing

    # Cleanup temp directory
    try:
        shutil.rmtree(test_dir)
    except Exception:
        pass

    print("\n" + "=" * 70)
    print("PHASE 6 AUDIT COMPLETE")
    print("=" * 70)
    out_file = Path("scratch/multiprocess_quota_audit_results.json")
    out_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Audit results written to {out_file}")


if __name__ == "__main__":
    mp.freeze_support()
    run_audit()
