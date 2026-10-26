"""
test_frontend_api_routing.py
============================
Regression tests verifying:
1. Frontend JavaScript API Base URL resolution (getApiBaseUrl and apiUrl) across:
   - Local frontend development servers (e.g. http://localhost:5500, http://127.0.0.1:5500)
   - Local direct FastAPI serving (http://127.0.0.1:8000)
   - Production deployments (e.g. Render / custom domains)
   - Custom overrides (window.LEARNAI_API_BASE_URL / localStorage)
2. FastAPI CORS configuration allowing cross-origin requests and preflight OPTIONS
   from local frontend servers (including GET, POST, OPTIONS, and DELETE methods).
"""

import json
import subprocess
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_JS_PATH = REPO_ROOT / "frontend" / "js" / "app.js"


class TestFrontendApiUrlResolution:
    """Verifies frontend/js/app.js URL resolution logic via Node.js."""

    @classmethod
    def setup_class(cls):
        assert APP_JS_PATH.is_file(), f"Missing app.js at {APP_JS_PATH}"

    def _eval_app_js(self, js_expression: str) -> str:
        escaped_path = str(APP_JS_PATH).replace("\\", "/")
        node_script = f"""
        const fs = require('fs');
        const vm = require('vm');
        const code = fs.readFileSync('{escaped_path}', 'utf8');
        const start = code.indexOf('function getApiBaseUrl');
        const end = code.indexOf('// ── Application State');
        if (start === -1 || end === -1) {{
            throw new Error('API routing functions not found in app.js');
        }}
        const helperCode = code.slice(start, end);
        const ctx = {{}};
        vm.createContext(ctx);
        vm.runInContext(helperCode, ctx);

        const res = (function() {{
            const {{ getApiBaseUrl, apiUrl }} = ctx;
            return ({js_expression});
        }})();
        console.log(JSON.stringify(res));
        """
        proc = subprocess.run(
            ["node", "-e", node_script],
            capture_output=True,
            text=True,
            check=True
        )
        return json.loads(proc.stdout.strip())

    def test_local_dev_server_port_5500_resolves_to_fastapi_8000(self):
        """When frontend is served on localhost:5500, API base resolves to http://127.0.0.1:8000."""
        expr = "getApiBaseUrl({ hostname: 'localhost', port: '5500', protocol: 'http:' })"
        assert self._eval_app_js(expr) == "http://127.0.0.1:8000"

    def test_local_dev_server_127_0_0_1_port_5500_resolves_to_fastapi_8000(self):
        """When frontend is served on 127.0.0.1:5500, API base resolves to http://127.0.0.1:8000."""
        expr = "getApiBaseUrl({ hostname: '127.0.0.1', port: '5500', protocol: 'http:' })"
        assert self._eval_app_js(expr) == "http://127.0.0.1:8000"

    def test_local_dev_server_other_port_3000_resolves_to_fastapi_8000(self):
        """When frontend is served on other dev server port (e.g. 3000), API base resolves to http://127.0.0.1:8000."""
        expr = "getApiBaseUrl({ hostname: 'localhost', port: '3000', protocol: 'http:' })"
        assert self._eval_app_js(expr) == "http://127.0.0.1:8000"

    def test_fastapi_direct_serving_port_8000_uses_relative_path(self):
        """When frontend is directly served by FastAPI on port 8000, relative paths are used."""
        expr_localhost = "getApiBaseUrl({ hostname: 'localhost', port: '8000', protocol: 'http:' })"
        expr_ip = "getApiBaseUrl({ hostname: '127.0.0.1', port: '8000', protocol: 'http:' })"
        assert self._eval_app_js(expr_localhost) == ""
        assert self._eval_app_js(expr_ip) == ""

    def test_production_render_domain_uses_relative_path(self):
        """When frontend is on production domain (e.g. Render), relative paths are preserved."""
        expr_render = "getApiBaseUrl({ hostname: 'learnai.onrender.com', port: '', protocol: 'https:' })"
        expr_custom = "getApiBaseUrl({ hostname: 'learnai.custom-domain.org', port: '443', protocol: 'https:' })"
        assert self._eval_app_js(expr_render) == ""
        assert self._eval_app_js(expr_custom) == ""

    def test_file_protocol_resolves_to_fastapi_8000(self):
        """When opened directly as a file:// URL, API base routes to http://127.0.0.1:8000."""
        expr = "getApiBaseUrl({ protocol: 'file:', hostname: '', port: '' })"
        assert self._eval_app_js(expr) == "http://127.0.0.1:8000"

    def test_explicit_window_override_takes_precedence(self):
        """Explicit window.LEARNAI_API_BASE_URL overrides all auto-detection."""
        expr = """
        getApiBaseUrl(
            { hostname: 'localhost', port: '5500', protocol: 'http:' },
            { LEARNAI_API_BASE_URL: 'https://staging-api.learnai.com/' }
        )
        """
        assert self._eval_app_js(expr) == "https://staging-api.learnai.com"

    def test_api_url_construction(self):
        """apiUrl correctly constructs full or relative paths for all endpoints."""
        cases = [
            ("apiUrl('/api/auth/session', 'http://127.0.0.1:8000')", "http://127.0.0.1:8000/api/auth/session"),
            ("apiUrl('/api/courses', 'http://127.0.0.1:8000')", "http://127.0.0.1:8000/api/courses"),
            ("apiUrl('/api/notes/extract-topics', 'http://127.0.0.1:8000')", "http://127.0.0.1:8000/api/notes/extract-topics"),
            ("apiUrl('/api/study-materials', 'http://127.0.0.1:8000')", "http://127.0.0.1:8000/api/study-materials"),
            ("apiUrl('/api/courses', '')", "/api/courses"),
            ("apiUrl('api/courses', '')", "/api/courses"),
            ("apiUrl('http://already-absolute.com/api', 'http://127.0.0.1:8000')", "http://already-absolute.com/api"),
        ]
        for expr, expected in cases:
            assert self._eval_app_js(expr) == expected


class TestBackendCorsConfiguration:
    """Verifies FastAPI CORS allows cross-origin requests from local dev servers."""

    def test_cors_preflight_for_get_courses(self):
        client = TestClient(app)
        res = client.options("/api/courses", headers={
            "Origin": "http://localhost:5500",
            "Access-Control-Request-Method": "GET",
        })
        assert res.status_code == 200
        assert "GET" in res.headers.get("access-control-allow-methods", "")

    def test_cors_preflight_for_post_auth_session(self):
        client = TestClient(app)
        res = client.options("/api/auth/session", headers={
            "Origin": "http://localhost:5500",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        })
        assert res.status_code == 200
        assert "POST" in res.headers.get("access-control-allow-methods", "")

    def test_cors_preflight_for_post_notes_upload(self):
        client = TestClient(app)
        res = client.options("/api/notes/extract-topics", headers={
            "Origin": "http://localhost:5500",
            "Access-Control-Request-Method": "POST",
        })
        assert res.status_code == 200
        assert "POST" in res.headers.get("access-control-allow-methods", "")

    def test_cors_preflight_for_delete_study_material(self):
        """Cross-origin DELETE preflight must succeed for study-material removal."""
        client = TestClient(app)
        res = client.options("/api/study-materials/mat_123", headers={
            "Origin": "http://localhost:5500",
            "Access-Control-Request-Method": "DELETE",
        })
        assert res.status_code == 200
        assert "DELETE" in res.headers.get("access-control-allow-methods", "")

    def test_cross_origin_get_courses_returns_allow_origin(self):
        client = TestClient(app)
        res = client.get("/api/courses", headers={"Origin": "http://localhost:5500"})
        assert res.status_code == 200
        assert res.headers.get("access-control-allow-origin") in ("*", "http://localhost:5500")
        assert "courses" in res.json()

    def test_cross_origin_post_auth_session_returns_allow_origin(self):
        client = TestClient(app)
        res = client.post("/api/auth/session", headers={"Origin": "http://localhost:5500"})
        assert res.status_code == 200
        assert res.headers.get("access-control-allow-origin") in ("*", "http://localhost:5500")
        assert "token" in res.json()
        assert "student_id" in res.json()
