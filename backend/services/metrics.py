"""
metrics.py
==========
Thread-safe, in-memory AI request and performance metrics tracker for LearnAI.

Tracks:
- Total quiz requests (successes, failures, rate-limited)
- Per-provider statistics: attempts, successes, failures, fallbacks, average response latency
- Total submissions and evaluation counts

Never logs or stores:
- API keys or tokens
- Raw student prompts or answers
"""
import time
import threading
from typing import Dict, Any


class MetricsCollector:
    def __init__(self):
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._total_quiz_requests = 0
        self._successful_quiz_requests = 0
        self._failed_quiz_requests = 0
        self._rate_limited_quiz_requests = 0
        self._total_submissions = 0

        # provider_name -> {attempts, successes, failures, total_duration, avg_latency}
        self._provider_stats: Dict[str, Dict[str, Any]] = {
            "groq": {"attempts": 0, "successes": 0, "failures": 0, "total_duration": 0.0},
            "nvidia": {"attempts": 0, "successes": 0, "failures": 0, "total_duration": 0.0},
            "gemini": {"attempts": 0, "successes": 0, "failures": 0, "total_duration": 0.0},
        }

    def record_quiz_request(self):
        with self._lock:
            self._total_quiz_requests += 1

    def record_quiz_success(self, provider: str, duration: float):
        with self._lock:
            self._successful_quiz_requests += 1
            p = provider.lower()
            if p not in self._provider_stats:
                self._provider_stats[p] = {"attempts": 0, "successes": 0, "failures": 0, "total_duration": 0.0}
            self._provider_stats[p]["attempts"] += 1
            self._provider_stats[p]["successes"] += 1
            self._provider_stats[p]["total_duration"] += duration

    def record_quiz_failure(self, provider: str, duration: float):
        with self._lock:
            self._failed_quiz_requests += 1
            p = provider.lower()
            if p not in self._provider_stats:
                self._provider_stats[p] = {"attempts": 0, "successes": 0, "failures": 0, "total_duration": 0.0}
            self._provider_stats[p]["attempts"] += 1
            self._provider_stats[p]["failures"] += 1
            self._provider_stats[p]["total_duration"] += duration

    def record_rate_limit_exceeded(self):
        with self._lock:
            self._rate_limited_quiz_requests += 1

    def record_submission(self):
        with self._lock:
            self._total_submissions += 1

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            uptime_seconds = int(time.time() - self._start_time)
            stats_copy = {}
            for p, data in self._provider_stats.items():
                attempts = data["attempts"]
                avg_latency = (
                    round(data["total_duration"] / attempts, 2)
                    if attempts > 0 else 0.0
                )
                stats_copy[p] = {
                    "attempts": attempts,
                    "successes": data["successes"],
                    "failures": data["failures"],
                    "avg_latency_seconds": avg_latency
                }

            return {
                "uptime_seconds": uptime_seconds,
                "quiz_requests": {
                    "total": self._total_quiz_requests,
                    "successful": self._successful_quiz_requests,
                    "failed": self._failed_quiz_requests,
                    "rate_limited": self._rate_limited_quiz_requests
                },
                "total_submissions": self._total_submissions,
                "providers": stats_copy
            }

    def reset(self):
        with self._lock:
            self._total_quiz_requests = 0
            self._successful_quiz_requests = 0
            self._failed_quiz_requests = 0
            self._rate_limited_quiz_requests = 0
            self._total_submissions = 0
            for data in self._provider_stats.values():
                data["attempts"] = 0
                data["successes"] = 0
                data["failures"] = 0
                data["total_duration"] = 0.0


metrics = MetricsCollector()
