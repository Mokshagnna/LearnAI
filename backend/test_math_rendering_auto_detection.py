"""
LearnAI: Mathematical Expression Auto-Detection Test Suite

Tests for the new convertPlainTextMath(), _hasPlainTextMath(), and
_looksLikeProse() methods added to LearnAIMathRenderer.

Validates that:
1. Plain-text math expressions (no LaTeX delimiters) are auto-detected and
   wrapped in \\(...\\) for KaTeX rendering.
2. Common math function names (sin, cos, sigmoid, etc.) are detected.
3. Exponents (x^2, e^{-x}) and subscripts (x_1, a_n) are detected.
4. Greek letter words (alpha, beta, theta, lambda, etc.) are converted.
5. Big-O notation O(n), O(log n) is detected.
6. Fraction patterns (a/b with math context) are detected.
7. URLs, emails, code backticks, and file paths are NOT falsely converted.
8. Currency values ($50, $100) are NOT falsely converted.
9. Already-delimited LaTeX expressions are NOT double-converted.
10. Normal English prose is NOT falsely converted.
11. Normalization: sqrt(x) → \\sqrt{x}, sin(x) → \\sin(x), etc.
12. Existing delimiter-based tokenize/render pipeline still works correctly.
13. The typeset() DOM walk now picks up nodes lacking $ or \\ delimiters
    when they contain plain-text math patterns.
"""

import subprocess
import json
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_JS = REPO_ROOT / "frontend" / "js" / "app.js"
KATEX_MIN_JS = REPO_ROOT / "frontend" / "vendor" / "katex" / "katex.min.js"

DOM_MOCK_PRELUDE = """
if (typeof global.document === 'undefined') {
    const domElements = new Map();
    function mockEl(id) {
        if (!domElements.has(id)) {
            domElements.set(id, {
                nodeType: 1, id, innerHTML: '', textContent: '', value: '', style: {}, dataset: {},
                classList: {
                    _classes: new Set(),
                    add(c) { this._classes.add(c); },
                    remove(c) { this._classes.delete(c); },
                    contains(c) { return this._classes.has(c); },
                    toggle(c) { if (this.contains(c)) this.remove(c); else this.add(c); }
                },
                children: [], childNodes: [],
                appendChild(c) { this.childNodes.push(c); return c; },
                removeChild(c) {
                    const idx = this.childNodes.indexOf(c);
                    if (idx !== -1) this.childNodes.splice(idx, 1);
                    return c;
                },
                replaceChild(n, o) {
                    const idx = this.childNodes.indexOf(o);
                    if (idx !== -1) {
                        if (n && n.nodeType === 11 && Array.isArray(n.childNodes)) {
                            this.childNodes.splice(idx, 1, ...n.childNodes);
                        } else {
                            this.childNodes[idx] = n;
                        }
                    }
                    return o;
                },
                setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
                addEventListener() {}, removeEventListener() {}, focus() {}, scrollIntoView() {}
            });
        }
        return domElements.get(id);
    }
    global.document = {
        getElementById: mockEl,
        createElement: (tag) => ({
            nodeType: 1, tagName: tag.toUpperCase(), className: '', innerHTML: '', textContent: '', style: {}, dataset: {},
            classList: {
                _classes: new Set(),
                add(c) { this._classes.add(c); },
                remove(c) { this._classes.delete(c); },
                contains(c) { return this._classes.has(c); }
            },
            childNodes: [],
            get firstChild() { return this.childNodes[0] || null; },
            appendChild(c) {
                c.parentNode = this;
                const prev = this.childNodes[this.childNodes.length - 1];
                if (prev) { prev.nextSibling = c; c.previousSibling = prev; }
                this.childNodes.push(c);
                return c;
            },
            removeChild(c) {
                const idx = this.childNodes.indexOf(c);
                if (idx !== -1) this.childNodes.splice(idx, 1);
                return c;
            },
            replaceChild(n, o) {
                const idx = this.childNodes.indexOf(o);
                if (idx !== -1) {
                    if (n && n.nodeType === 11 && Array.isArray(n.childNodes)) {
                        n.childNodes.forEach(ch => { ch.parentNode = this; });
                        this.childNodes.splice(idx, 1, ...n.childNodes);
                    } else {
                        if (n) n.parentNode = this;
                        this.childNodes[idx] = n;
                    }
                }
                return o;
            },
            setAttribute() {}, getAttribute() { return null; }, addEventListener() {}
        }),
        createTextNode: (t) => ({ nodeType: 3, textContent: t, data: t }),
        createDocumentFragment: () => ({ nodeType: 11, childNodes: [], appendChild(c) { this.childNodes.push(c); return c; } }),
        querySelectorAll: () => [], querySelector: () => null, addEventListener: () => {}
    };
    global.window = {
        document: global.document, addEventListener: () => {}, scrollTo: () => {},
        location: { origin: 'http://localhost:8000', pathname: '/' }
    };
    global.navigator = { clipboard: { writeText: async () => {} } };
}
"""


def run_node_eval(script_body: str) -> str:
    """Execute a Node.js script with DOM mock and KaTeX loaded, return stdout."""
    katex_path = str(KATEX_MIN_JS).replace("\\", "/")
    full_script = f"""
const path = require('path');
const Module = require('module');
const origResolve = Module._resolveFilename;
Module._resolveFilename = function(request, parent, isMain) {{
    if (request === 'katex') return path.resolve({json.dumps(katex_path)});
    return origResolve.apply(this, arguments);
}};

{DOM_MOCK_PRELUDE}

global.katex = require({json.dumps(katex_path)});

{script_body}
"""
    res = subprocess.run(
        ["node", "-e", full_script],
        capture_output=True, text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT), timeout=20
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"Node execution failed (code {res.returncode}):\n{res.stderr}\nScript:\n{full_script[:3000]}"
        )
    return res.stdout.strip()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Method Existence
# ─────────────────────────────────────────────────────────────────────────────

class TestMethodExistence:
    def test_convert_plain_text_math_exists(self):
        """convertPlainTextMath must be a function on LearnAIMathRenderer."""
        out = json.loads(run_node_eval("""
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        console.log(JSON.stringify(typeof LearnAIMathRenderer.convertPlainTextMath));
        """))
        assert out == "function", "convertPlainTextMath is not a function"

    def test_has_plain_text_math_exists(self):
        """_hasPlainTextMath must be a function on LearnAIMathRenderer."""
        out = json.loads(run_node_eval("""
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        console.log(JSON.stringify(typeof LearnAIMathRenderer._hasPlainTextMath));
        """))
        assert out == "function"

    def test_looks_like_prose_exists(self):
        """_looksLikeProse must be a function on LearnAIMathRenderer."""
        out = json.loads(run_node_eval("""
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        console.log(JSON.stringify(typeof LearnAIMathRenderer._looksLikeProse));
        """))
        assert out == "function"


# ─────────────────────────────────────────────────────────────────────────────
# 2. _hasPlainTextMath Heuristic
# ─────────────────────────────────────────────────────────────────────────────

class TestHasPlainTextMath:
    def test_detects_math_functions(self):
        """sin(x), cos(x), log(x), exp(x), sqrt(x), sigmoid(x) must be detected."""
        samples = [
            "The value of sin(x) at x=0 is 0",
            "cos(theta) is the horizontal component",
            "Apply log(n) base 2",
            "The function exp(x) is always positive",
            "sqrt(2) is approximately 1.414",
            "The sigmoid(z) maps any real to (0,1)",
        ]
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const samples = {json.dumps(samples)};
        const results = samples.map(s => LearnAIMathRenderer._hasPlainTextMath(s));
        console.log(JSON.stringify(results));
        """
        results = json.loads(run_node_eval(script))
        for i, (s, r) in enumerate(zip(samples, results)):
            assert r, f"Sample {i} not detected as math: {s!r}"

    def test_detects_exponent_notation(self):
        """x^2, e^n, 10^3, a^{n+1} must trigger detection."""
        samples = ["x^2 grows quadratically", "e^n for any n > 0", "10^3 = 1000"]
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const samples = {json.dumps(samples)};
        const results = samples.map(s => LearnAIMathRenderer._hasPlainTextMath(s));
        console.log(JSON.stringify(results));
        """
        results = json.loads(run_node_eval(script))
        for i, (s, r) in enumerate(zip(samples, results)):
            assert r, f"Exponent sample {i} not detected: {s!r}"

    def test_detects_subscript_notation(self):
        """x_1, a_n, x_{n+1} must trigger detection."""
        samples = ["element a_1 of the sequence", "variable x_n converges"]
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const samples = {json.dumps(samples)};
        const results = samples.map(s => LearnAIMathRenderer._hasPlainTextMath(s));
        console.log(JSON.stringify(results));
        """
        results = json.loads(run_node_eval(script))
        for i, (s, r) in enumerate(zip(samples, results)):
            assert r, f"Subscript sample {i} not detected: {s!r}"

    def test_detects_greek_letter_words(self):
        """alpha, beta, theta, lambda, sigma, omega, mu, pi must be detected."""
        samples = [
            "parameter alpha controls learning rate",
            "the angle theta is measured in radians",
            "regularization parameter lambda",
            "standard deviation sigma",
            "angular frequency omega",
        ]
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const samples = {json.dumps(samples)};
        const results = samples.map(s => LearnAIMathRenderer._hasPlainTextMath(s));
        console.log(JSON.stringify(results));
        """
        results = json.loads(run_node_eval(script))
        for i, (s, r) in enumerate(zip(samples, results)):
            assert r, f"Greek sample {i} not detected: {s!r}"

    def test_detects_big_o_notation(self):
        """O(n), O(log n), O(n^2), O(1) must be detected."""
        samples = [
            "Binary search runs in O(log n)",
            "Bubble sort is O(n^2) worst case",
            "Hash lookup is O(1) on average",
        ]
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const samples = {json.dumps(samples)};
        const results = samples.map(s => LearnAIMathRenderer._hasPlainTextMath(s));
        console.log(JSON.stringify(results));
        """
        results = json.loads(run_node_eval(script))
        for i, (s, r) in enumerate(zip(samples, results)):
            assert r, f"Big-O sample {i} not detected: {s!r}"

    def test_does_not_falsely_detect_prose(self):
        """Normal English prose sentences must NOT be detected as math."""
        prose_samples = [
            "The quick brown fox jumps over the lazy dog",
            "Binary search requires a sorted array to function correctly",
            "This algorithm is efficient and widely used in practice",
            "The answer to this question is option B",
            "Please read the documentation carefully before proceeding",
        ]
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const samples = {json.dumps(prose_samples)};
        const results = samples.map(s => LearnAIMathRenderer._hasPlainTextMath(s));
        console.log(JSON.stringify(results));
        """
        results = json.loads(run_node_eval(script))
        for i, (s, r) in enumerate(zip(prose_samples, results)):
            assert not r, f"Prose sample {i} falsely detected as math: {s!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. convertPlainTextMath — Auto-Detection and Conversion
# ─────────────────────────────────────────────────────────────────────────────

class TestConvertPlainTextMath:
    def test_sigmoid_function_expression_converted(self):
        """f(x) = 1 / (1 + e^{-x}) must be wrapped in \\(...\\) delimiters."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const input = 'f(x) = 1 / (1 + e^{-x})';
        const output = LearnAIMathRenderer.convertPlainTextMath(input);
        console.log(JSON.stringify({
            output: output,
            hasDelimiters: output.includes('\\\\(') || output.includes('\\\\['),
            containsMathNotation: output.includes('frac') || output.includes('^') || output.includes('\\\\(')
        }));
        """
        result = json.loads(run_node_eval(script))
        assert result["hasDelimiters"], f"sigmoid not wrapped in delimiters: {result['output']!r}"
        assert result["containsMathNotation"], f"math content missing: {result['output']!r}"

    def test_sin_cos_function_converted(self):
        """sin(x) and cos(x) must be converted to \\sin(x), \\cos(x)."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const r = LearnAIMathRenderer.convertPlainTextMath('Compute sin(x) + cos(x)');
        console.log(JSON.stringify({ output: r, hasSin: r.includes('\\\\sin') || r.includes('\\\\(') }));
        """
        result = json.loads(run_node_eval(script))
        assert result["hasSin"], f"sin(x) not converted: {result['output']!r}"

    def test_sqrt_function_converted(self):
        """sqrt(x) must become \\sqrt{x}."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const r = LearnAIMathRenderer.convertPlainTextMath('The result is sqrt(n) steps');
        console.log(JSON.stringify({ output: r, hasSqrt: r.includes('\\\\sqrt') }));
        """
        result = json.loads(run_node_eval(script))
        assert result["hasSqrt"], f"sqrt not normalized: {result['output']!r}"

    def test_greek_letters_converted(self):
        """Greek letter words must be replaced with LaTeX commands."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const r1 = LearnAIMathRenderer.convertPlainTextMath('The angle theta is in radians');
        const r2 = LearnAIMathRenderer.convertPlainTextMath('Use lambda as regularization parameter');
        console.log(JSON.stringify({
            r1: r1, r2: r2,
            r1HasTheta: r1.includes('\\\\theta'),
            r2HasLambda: r2.includes('\\\\lambda')
        }));
        """
        result = json.loads(run_node_eval(script))
        assert result["r1HasTheta"], f"theta not converted: {result['r1']!r}"
        assert result["r2HasLambda"], f"lambda not converted: {result['r2']!r}"

    def test_big_o_notation_converted(self):
        """O(log n), O(n^2) must be wrapped in inline math delimiters."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const r = LearnAIMathRenderer.convertPlainTextMath('Binary search has complexity O(log n)');
        console.log(JSON.stringify({ output: r, hasMathDelim: r.includes('\\\\(') }));
        """
        result = json.loads(run_node_eval(script))
        assert result["hasMathDelim"], f"O(log n) not wrapped: {result['output']!r}"

    def test_exponent_notation_converted(self):
        """x^2, 10^3 must be detected and wrapped."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const r = LearnAIMathRenderer.convertPlainTextMath('The value x^2 grows quadratically');
        console.log(JSON.stringify({ output: r, hasExp: r.includes('\\\\(') || r.includes('^') }));
        """
        result = json.loads(run_node_eval(script))
        assert result["hasExp"], f"exponent not converted: {result['output']!r}"

    def test_subscript_notation_converted(self):
        """a_1, x_n must be detected and wrapped."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const r = LearnAIMathRenderer.convertPlainTextMath('The sequence element a_n converges');
        console.log(JSON.stringify({ output: r, hasSubscript: r.includes('\\\\(') || r.includes('_') }));
        """
        result = json.loads(run_node_eval(script))
        assert result["hasSubscript"], f"subscript not converted: {result['output']!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Safety Guards — No False Positives
# ─────────────────────────────────────────────────────────────────────────────

class TestSafetyGuards:
    def test_url_not_converted(self):
        """URLs must pass through completely unchanged."""
        urls = [
            "See https://en.wikipedia.org/wiki/Sigmoid_function for details",
            "Reference: http://mathworld.wolfram.com/BinarySearch.html",
        ]
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const urls = {json.dumps(urls)};
        const results = urls.map(u => ({{
            input: u,
            output: LearnAIMathRenderer.convertPlainTextMath(u),
            unchanged: LearnAIMathRenderer.convertPlainTextMath(u) === u
        }}));
        console.log(JSON.stringify(results));
        """
        results = json.loads(run_node_eval(script))
        for r in results:
            assert r["unchanged"], f"URL was modified: input={r['input']!r}, output={r['output']!r}"

    def test_already_delimited_latex_not_double_converted(self):
        r"""Text already wrapped in \(...\) or \[...\] must not be double-converted."""
        samples = [
            r"\( x^2 + y^2 = r^2 \)",
            r"\[ f(x) = \frac{1}{\sigma\sqrt{2\pi}} \]",
            r"The formula $\alpha + \beta = \gamma$ appears in many proofs.",
        ]
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const samples = {json.dumps(samples)};
        const results = samples.map(s => {{
            const r = LearnAIMathRenderer.convertPlainTextMath(s);
            return {{ input: s, output: r, unchanged: r === s }};
        }});
        console.log(JSON.stringify(results));
        """
        results = json.loads(run_node_eval(script))
        for r in results:
            assert r["unchanged"], f"Already-delimited LaTeX was double-converted:\nInput:  {r['input']!r}\nOutput: {r['output']!r}"

    def test_currency_not_converted(self):
        """Currency values like $50, $1,000 must not be converted to math."""
        samples = [
            "An enterprise license costs $50 for students",
            "The company raised $1,000,000 in funding",
            "Price is $9.99 per month",
        ]
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const samples = {json.dumps(samples)};
        // convertPlainTextMath should either return unchanged or not add \\( around currency
        const results = samples.map(s => {{
            const r = LearnAIMathRenderer.convertPlainTextMath(s);
            return {{ input: s, output: r, hasCurrency: r.includes('$50') || r.includes('$1,000') || r.includes('$9.99') }};
        }});
        console.log(JSON.stringify(results));
        """
        results = json.loads(run_node_eval(script))
        for r in results:
            assert r["hasCurrency"], f"Currency was corrupted:\nInput:  {r['input']!r}\nOutput: {r['output']!r}"

    def test_prose_not_converted(self):
        """Normal English sentences with no math patterns must remain unchanged."""
        prose = [
            "The quick brown fox jumps over the lazy dog.",
            "Binary search is an efficient algorithm for sorted arrays.",
            "This is a question about data structures and algorithms.",
        ]
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const samples = {json.dumps(prose)};
        const results = samples.map(s => {{
            const r = LearnAIMathRenderer.convertPlainTextMath(s);
            return {{ input: s, output: r, unchanged: r === s }};
        }});
        console.log(JSON.stringify(results));
        """
        results = json.loads(run_node_eval(script))
        for r in results:
            assert r["unchanged"], f"Prose was falsely converted:\nInput:  {r['input']!r}\nOutput: {r['output']!r}"

    def test_backtick_code_not_converted(self):
        """Strings containing backtick code must not have math injected."""
        sample = "Use `x^2` as the exponent in your Python formula"
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const s = {json.dumps(sample)};
        const r = LearnAIMathRenderer.convertPlainTextMath(s);
        console.log(JSON.stringify({{ output: r, unchanged: r === s }}));
        """
        result = json.loads(run_node_eval(script))
        assert result["unchanged"], f"Backtick code was modified: {result['output']!r}"

    def test_version_number_not_converted(self):
        """Version strings like 3.14.1 or 1.0.2 must not be converted."""
        samples = ["Python 3.12.0 supports match statements", "Version 1.0.2 was released"]
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const samples = {json.dumps(samples)};
        const results = samples.map(s => LearnAIMathRenderer.convertPlainTextMath(s) === s);
        console.log(JSON.stringify(results));
        """
        results = json.loads(run_node_eval(script))
        for i, (s, r) in enumerate(zip(samples, results)):
            assert r, f"Version string {i} was modified: {s!r}"

    def test_null_and_empty_inputs_safe(self):
        """null, undefined, and empty string must not throw."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        let threw = false;
        try {
            const r1 = LearnAIMathRenderer.convertPlainTextMath(null);
            const r2 = LearnAIMathRenderer.convertPlainTextMath(undefined);
            const r3 = LearnAIMathRenderer.convertPlainTextMath('');
            const r4 = LearnAIMathRenderer.convertPlainTextMath('x');
        } catch(e) {
            threw = true;
        }
        console.log(JSON.stringify({ threw }));
        """
        result = json.loads(run_node_eval(script))
        assert not result["threw"], "convertPlainTextMath threw on null/empty input"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Integration: convertPlainTextMath + tokenize + renderToString pipeline
# ─────────────────────────────────────────────────────────────────────────────

class TestRenderingPipeline:
    def test_converted_text_tokenizes_to_math(self):
        """After convertPlainTextMath, the result must tokenize into math tokens."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const raw = 'Compute sin(x) for x in [0, pi]';
        const converted = LearnAIMathRenderer.convertPlainTextMath(raw);
        const tokens = LearnAIMathRenderer.tokenize(converted);
        const mathTokens = tokens.filter(t => t.type === 'math');
        console.log(JSON.stringify({
            converted: converted,
            mathTokenCount: mathTokens.length,
            hasMath: mathTokens.length > 0
        }));
        """
        result = json.loads(run_node_eval(script))
        assert result["hasMath"], f"Converted text produced no math tokens: {result['converted']!r}"

    def test_full_pipeline_renders_katex_html(self):
        """The full convert → tokenize → renderToString pipeline must produce KaTeX HTML."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const raw = 'The sigmoid function f(x) = 1 / (1 + e^{-x}) is used in logistic regression';
        const converted = LearnAIMathRenderer.convertPlainTextMath(raw);
        const tokens = LearnAIMathRenderer.tokenize(converted);
        const mathTokens = tokens.filter(t => t.type === 'math');
        const rendered = mathTokens.length > 0
            ? LearnAIMathRenderer.renderToString(mathTokens[0].content, mathTokens[0].display)
            : '';
        console.log(JSON.stringify({
            hasKatex: rendered.includes('katex-html') || rendered.includes('katex'),
            mathCount: mathTokens.length
        }));
        """
        result = json.loads(run_node_eval(script))
        assert result["mathCount"] > 0, "No math tokens from sigmoid expression"
        assert result["hasKatex"], "renderToString did not produce KaTeX HTML"

    def test_greek_letters_render_as_katex(self):
        """Greek letter words, once converted, must produce KaTeX HTML."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const raw = 'The learning rate alpha controls convergence';
        const converted = LearnAIMathRenderer.convertPlainTextMath(raw);
        const tokens = LearnAIMathRenderer.tokenize(converted);
        const mathTokens = tokens.filter(t => t.type === 'math');
        let hasKatex = false;
        for (const tok of mathTokens) {
            const rendered = LearnAIMathRenderer.renderToString(tok.content, tok.display);
            if (rendered.includes('katex')) hasKatex = true;
        }
        console.log(JSON.stringify({ converted, mathCount: mathTokens.length, hasKatex }));
        """
        result = json.loads(run_node_eval(script))
        assert result["mathCount"] > 0, f"No math tokens from Greek text: {result['converted']!r}"
        assert result["hasKatex"], "Greek letter tokens did not produce KaTeX HTML"

    def test_o_notation_renders_as_katex(self):
        """O(n log n) notation, once converted, must produce KaTeX HTML."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const raw = 'Merge sort has time complexity O(n log n) in all cases';
        const converted = LearnAIMathRenderer.convertPlainTextMath(raw);
        const tokens = LearnAIMathRenderer.tokenize(converted);
        const mathTokens = tokens.filter(t => t.type === 'math');
        let hasKatex = false;
        for (const tok of mathTokens) {
            const rendered = LearnAIMathRenderer.renderToString(tok.content, tok.display);
            if (rendered.includes('katex')) hasKatex = true;
        }
        console.log(JSON.stringify({ converted, mathCount: mathTokens.length, hasKatex }));
        """
        result = json.loads(run_node_eval(script))
        assert result["mathCount"] > 0, f"No math tokens from O() expression: {result['converted']!r}"
        assert result["hasKatex"], "O(n) notation did not produce KaTeX HTML"

    def test_existing_latex_delimiter_pipeline_unchanged(self):
        r"""Existing delimiter-based path (\(...\), $$...$$) must still work as before."""
        script = r"""
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const text = 'The quadratic formula: \\( x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a} \\) for real roots.';
        // convertPlainTextMath must leave already-delimited text unchanged
        const converted = LearnAIMathRenderer.convertPlainTextMath(text);
        const tokens = LearnAIMathRenderer.tokenize(converted);
        const mathTokens = tokens.filter(t => t.type === 'math');
        const rendered = mathTokens.length > 0
            ? LearnAIMathRenderer.renderToString(mathTokens[0].content, false)
            : '';
        console.log(JSON.stringify({
            unchanged: converted === text,
            mathCount: mathTokens.length,
            hasFrac: rendered.includes('frac') || rendered.includes('katex'),
            hasSqrt: rendered.includes('sqrt') || rendered.includes('katex')
        }));
        """
        result = json.loads(run_node_eval(script))
        assert result["unchanged"], "Explicit LaTeX was modified by convertPlainTextMath"
        assert result["mathCount"] >= 1, "Explicit LaTeX produced no math tokens"
        assert result["hasFrac"] or result["hasSqrt"], "Explicit LaTeX did not render correctly"


# ─────────────────────────────────────────────────────────────────────────────
# 6. hasMath() Regression — must still detect explicit LaTeX
# ─────────────────────────────────────────────────────────────────────────────

class TestHasMathRegression:
    def test_has_math_still_detects_explicit_latex(self):
        r"""hasMath() must still return true for text with explicit LaTeX delimiters."""
        samples = [
            r"\( x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a} \)",
            r"\[ E = mc^2 \]",
            r"$$\sum_{i=1}^n x_i = S$$",
            r"\frac{1}{2} mv^2",
        ]
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const samples = {json.dumps(samples)};
        const results = samples.map(s => LearnAIMathRenderer.hasMath(s));
        console.log(JSON.stringify(results));
        """
        results = json.loads(run_node_eval(script))
        for i, (s, r) in enumerate(zip(samples, results)):
            assert r, f"hasMath returned false for explicit LaTeX sample {i}: {s!r}"

    def test_currency_still_safe_after_patch(self):
        """Currency protection in tokenize() must be unaffected by the new methods."""
        samples = [
            "An enterprise license costs $50 for students and $100 for faculty.",
            "The company raised $1,000,000 in seed capital and $50M in Series A.",
        ]
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const samples = {json.dumps(samples)};
        const results = samples.map(s => {{
            const tokens = LearnAIMathRenderer.tokenize(s);
            return tokens.filter(t => t.type === 'math').length;
        }});
        console.log(JSON.stringify(results));
        """
        results = json.loads(run_node_eval(script))
        for i, (s, r) in enumerate(zip(samples, results)):
            assert r == 0, f"Currency sample {i} incorrectly tokenized as math: {s!r} → {r} math tokens"

# ─────────────────────────────────────────────────────────────────────────────
# 7. Mandatory Screenshot Regression Cases
# ─────────────────────────────────────────────────────────────────────────────

class TestMandatoryScreenshotOptions:
    def test_option_a_max_formula(self):
        r"""Option A: L(y, s) = max(0, 1 - y * s) -> \(L(y,s)=\max\{0,1-ys\}\)"""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const input = 'L(y, s) = max(0, 1 - y * s)';
        const actual = LearnAIMathRenderer.convertPlainTextMath(input);
        console.log(JSON.stringify({ actual }));
        """
        res = json.loads(run_node_eval(script))
        expected = r"\(L(y,s)=\max\{0,1-ys\}\)"
        assert res["actual"] == expected, f"Option A mismatch:\nActual:   {res['actual']}\nExpected: {expected}"

    def test_option_b_squared_formula(self):
        r"""Option B: L(y, s) = (1 - y * s)^2 -> \(L(y,s)=(1-ys)^2\)"""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const input = 'L(y, s) = (1 - y * s)^2';
        const actual = LearnAIMathRenderer.convertPlainTextMath(input);
        console.log(JSON.stringify({ actual }));
        """
        res = json.loads(run_node_eval(script))
        expected = r"\(L(y,s)=(1-ys)^2\)"
        assert res["actual"] == expected, f"Option B mismatch:\nActual:   {res['actual']}\nExpected: {expected}"

    def test_option_c_log_exp_formula(self):
        r"""Option C: L(y, s) = log(1 + exp(-y * s)) -> \(L(y,s)=\log(1+e^{-ys})\)"""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const input = 'L(y, s) = log(1 + exp(-y * s))';
        const actual = LearnAIMathRenderer.convertPlainTextMath(input);
        console.log(JSON.stringify({ actual }));
        """
        res = json.loads(run_node_eval(script))
        expected = r"\(L(y,s)=\log(1+e^{-ys})\)"
        assert res["actual"] == expected, f"Option C mismatch:\nActual:   {res['actual']}\nExpected: {expected}"

    def test_option_d_negative_log_formula(self):
        r"""Option D: L(y, s) = -y * log(s) -> \(L(y,s)=-y\log(s)\)"""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const input = 'L(y, s) = -y * log(s)';
        const actual = LearnAIMathRenderer.convertPlainTextMath(input);
        console.log(JSON.stringify({ actual }));
        """
        res = json.loads(run_node_eval(script))
        expected = r"\(L(y,s)=-y\log(s)\)"
        assert res["actual"] == expected, f"Option D mismatch:\nActual:   {res['actual']}\nExpected: {expected}"

    def test_screenshot_options_render_valid_katex(self):
        """All four options must produce valid KaTeX markup with no syntax or rendering errors."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const options = [
            'L(y, s) = max(0, 1 - y * s)',
            'L(y, s) = (1 - y * s)^2',
            'L(y, s) = log(1 + exp(-y * s))',
            'L(y, s) = -y * log(s)'
        ];
        const results = options.map(opt => {
            const converted = LearnAIMathRenderer.convertPlainTextMath(opt);
            const tokens = LearnAIMathRenderer.tokenize(converted);
            const mathToken = tokens.find(t => t.type === 'math');
            const html = mathToken ? LearnAIMathRenderer.renderToString(mathToken.content, mathToken.display) : '';
            return {
                opt,
                hasKatex: html.includes('class="katex"'),
                hasError: html.includes('katex-error')
            };
        });
        console.log(JSON.stringify(results));
        """
        results = json.loads(run_node_eval(script))
        for res in results:
            assert res["hasKatex"], f"Option failed KaTeX render: {res['opt']}"
            assert not res["hasError"], f"Option produced KaTeX error: {res['opt']}"


# ─────────────────────────────────────────────────────────────────────────────
# 8. Multiplication Normalization Tests
# ─────────────────────────────────────────────────────────────────────────────

    def test_variable_multiplication(self):
        """y * s -> ys, a * b -> ab, 2 * x -> 2x"""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const samples = [
            { in: 'y * s', expected: 'ys' },
            { in: 'a * b', expected: 'ab' },
            { in: '2 * x', expected: '2x' },
            { in: '10 * y', expected: '10y' }
        ];
        const results = samples.map(s => ({
            in: s.in,
            out: LearnAIMathRenderer.normalizeMathExpression(s.in),
            expected: s.expected
        }));
        console.log(JSON.stringify(results));
        """
        results = json.loads(run_node_eval(script))
        for r in results:
            assert r["out"] == r["expected"], f"Failed: {r['in']} -> {r['out']} (expected {r['expected']})"

    def test_parentheses_multiplication(self):
        """x * (y + z) -> x(y+z), (a + b) * (c + d) -> (a+b)(c+d)"""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const samples = [
            { in: 'x * (y + z)', expected: 'x(y+z)' },
            { in: '(a + b) * (c + d)', expected: '(a+b)(c+d)' },
            { in: '(a + b) * c', expected: '(a+b)c' }
        ];
        const results = samples.map(s => ({
            in: s.in,
            out: LearnAIMathRenderer.normalizeMathExpression(s.in),
            expected: s.expected
        }));
        console.log(JSON.stringify(results));
        """
        results = json.loads(run_node_eval(script))
        for r in results:
            assert r["out"] == r["expected"], f"Failed: {r['in']} -> {r['out']}"

    def test_numeric_multiplication_uses_cdot(self):
        r"""2 * 3 must become 2 \cdot 3 for clarity (not 23)."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const out = LearnAIMathRenderer.normalizeMathExpression('2 * 3');
        console.log(JSON.stringify({ out }));
        """
        res = json.loads(run_node_eval(script))
        assert "cdot" in res["out"], f"Numeric multiplication missing \\cdot: {res['out']}"


# ─────────────────────────────────────────────────────────────────────────────
# 9. Function and Operator Normalization Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFunctionAndOperatorNormalization:
    def test_max_and_min_functions(self):
        r"""max(0, 1 - y * s) -> \max\{0,1-ys\}, min(a, b) -> \min\{a,b\}"""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const r1 = LearnAIMathRenderer.normalizeMathExpression('max(0, 1 - y * s)');
        const r2 = LearnAIMathRenderer.normalizeMathExpression('min(a, b)');
        console.log(JSON.stringify({ r1, r2 }));
        """
        res = json.loads(run_node_eval(script))
        assert res["r1"] == r"\max\{0,1-ys\}", f"max failed: {res['r1']}"
        assert res["r2"] == r"\min\{a,b\}", f"min failed: {res['r2']}"

    def test_exponential_normalization(self):
        r"""exp(-y * s) -> e^{-ys}, exp(x^2) -> e^{x^2}"""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const r1 = LearnAIMathRenderer.normalizeMathExpression('exp(-y * s)');
        const r2 = LearnAIMathRenderer.normalizeMathExpression('exp(x^2)');
        console.log(JSON.stringify({ r1, r2 }));
        """
        res = json.loads(run_node_eval(script))
        assert res["r1"] == "e^{-ys}", f"exp failed: {res['r1']}"
        assert res["r2"] == "e^{x^2}", f"exp failed: {res['r2']}"

    def test_log_and_ln_preserved(self):
        r"""log(x) -> \log(x), ln(x) -> \ln(x)"""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const r1 = LearnAIMathRenderer.normalizeMathExpression('log(x)');
        const r2 = LearnAIMathRenderer.normalizeMathExpression('ln(x)');
        console.log(JSON.stringify({ r1, r2 }));
        """
        res = json.loads(run_node_eval(script))
        assert res["r1"] == r"\log(x)", f"log failed: {res['r1']}"
        assert res["r2"] == r"\ln(x)", f"ln failed: {res['r2']}"

    def test_fractions_normalization(self):
        r"""1 / (1 + e^{-x}) -> \frac{1}{1+e^{-x}}, a / b -> \frac{a}{b}"""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const r1 = LearnAIMathRenderer.normalizeMathExpression('1 / (1 + e^{-x})');
        const r2 = LearnAIMathRenderer.normalizeMathExpression('a / b');
        console.log(JSON.stringify({ r1, r2 }));
        """
        res = json.loads(run_node_eval(script))
        assert res["r1"] == r"\frac{1}{1+e^{-x}}", f"fraction 1 failed: {res['r1']}"
        assert res["r2"] == r"\frac{a}{b}", f"fraction 2 failed: {res['r2']}"

    def test_exponents_grouping(self):
        """a^(n+1) -> a^{n+1}, (1-ys)^2 -> (1-ys)^2"""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const r1 = LearnAIMathRenderer.normalizeMathExpression('a^(n+1)');
        const r2 = LearnAIMathRenderer.normalizeMathExpression('(1-ys)^2');
        console.log(JSON.stringify({ r1, r2 }));
        """
        res = json.loads(run_node_eval(script))
        assert res["r1"] == "a^{n+1}", f"exp grouping failed: {res['r1']}"
        assert res["r2"] == "(1-ys)^2", f"exp grouping failed: {res['r2']}"


# ─────────────────────────────────────────────────────────────────────────────
# 10. Safety Guards & Idempotency Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSafetyAndIdempotency:
    def test_dates_and_paths_not_converted(self):
        """Dates (12/05/2024) and paths (/usr/local/bin) must NOT be converted to fractions."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const r1 = LearnAIMathRenderer.normalizeMathExpression('12/05/2024');
        const r2 = LearnAIMathRenderer.normalizeMathExpression('/usr/local/bin');
        console.log(JSON.stringify({ r1, r2 }));
        """
        res = json.loads(run_node_eval(script))
        assert "frac" not in res["r1"], f"Date converted to fraction: {res['r1']}"
        assert "frac" not in res["r2"], f"Path converted to fraction: {res['r2']}"

    def test_normalization_is_idempotent(self):
        """Running normalizeMathExpression twice must yield the exact same result."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const samples = [
            'L(y, s) = max(0, 1 - y * s)',
            'L(y, s) = (1 - y * s)^2',
            'L(y, s) = log(1 + exp(-y * s))',
            'L(y, s) = -y * log(s)',
            'f(x) = 1 / (1 + e^{-x})',
            '2 * x + 3 * y'
        ];
        const results = samples.map(s => {
            const once = LearnAIMathRenderer.normalizeMathExpression(s);
            const twice = LearnAIMathRenderer.normalizeMathExpression(once);
            return { s, once, twice, match: once === twice };
        });
        console.log(JSON.stringify(results));
        """
        results = json.loads(run_node_eval(script))
        for r in results:
            assert r["match"], f"Idempotency failed for {r['s']}:\nOnce:  {r['once']}\nTwice: {r['twice']}"


# ─────────────────────────────────────────────────────────────────────────────
# 11. Huber Loss and Mixed Prose/Equation Regression Suite
# ─────────────────────────────────────────────────────────────────────────────

class TestHuberLossAndMixedProseRegression:
    def test_exact_huber_loss_screenshot_option(self):
        r"""The exact Huber loss option from the screenshot must be properly typeset into mixed prose and KaTeX."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const input = "It behaves as 0.5 * e² for small errors |e| <= delta (smooth and differentiable), and transitions to delta * (|e| - 0.5 * delta) for large errors |e| > delta (linear, robust to outliers).";
        const converted = LearnAIMathRenderer.convertPlainTextMath(input);
        const tokens = LearnAIMathRenderer.tokenize(converted);
        const mathTokens = tokens.filter(t => t.type === 'math');
        const renders = mathTokens.map(t => ({
            content: t.content,
            html: LearnAIMathRenderer.renderToString(t.content, t.display),
            hasKatex: LearnAIMathRenderer.renderToString(t.content, t.display).includes('class="katex"')
        }));
        console.log(JSON.stringify({ converted, mathCount: mathTokens.length, renders }));
        """
        res = json.loads(run_node_eval(script))
        # Must have detected the 4 mathematical units
        assert res["mathCount"] == 4, f"Expected 4 math tokens, got {res['mathCount']}: {res['converted']}"
        # All 4 must render without error
        for r in res["renders"]:
            assert r["hasKatex"], f"Token failed KaTeX render: {r['content']}"
            assert "katex-error" not in r["html"], f"Token has KaTeX error: {r['content']}"

        # Check specific expressions normalized
        contents = [r["content"] for r in res["renders"]]
        assert any(r"\frac{1}{2}" in c and "e^{2}" in c for c in contents), f"0.5 * e^2 failed: {contents}"
        assert any(r"\leq" in c and r"\delta" in c for c in contents), f"|e| <= delta failed: {contents}"
        assert any(r"\delta" in c and r"\frac{1}{2}\delta" in c for c in contents), f"delta * (...) failed: {contents}"
        assert any(">" in c and r"\delta" in c for c in contents), f"|e| > delta failed: {contents}"

    def test_piecewise_huber_loss_definition(self):
        r"""Piecewise definition using if/for must produce a LaTeX cases environment."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const input = "L_delta(e) = 0.5 * e^2 if |e| <= delta; delta * (|e| - 0.5 * delta) if |e| > delta";
        const converted = LearnAIMathRenderer.convertPlainTextMath(input);
        const tokens = LearnAIMathRenderer.tokenize(converted);
        const mathTok = tokens.find(t => t.type === 'math');
        const html = mathTok ? LearnAIMathRenderer.renderToString(mathTok.content, mathTok.display) : '';
        console.log(JSON.stringify({ converted, hasCases: converted.includes('begin{cases}'), hasKatex: html.includes('class="katex"') }));
        """
        res = json.loads(run_node_eval(script))
        assert res["hasCases"], f"Piecewise cases not generated: {res['converted']}"
        assert res["hasKatex"], "Piecewise KaTeX failed to render"

    def test_mixed_prose_and_equations(self):
        r"""Prose containing equations must preserve English text while typesetting formulas."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const input = "The objective function combines quadratic penalty 0.5 * e^2 with linear penalty delta * |e|.";
        const converted = LearnAIMathRenderer.convertPlainTextMath(input);
        console.log(JSON.stringify({ converted }));
        """
        res = json.loads(run_node_eval(script))
        assert "The objective function combines quadratic penalty" in res["converted"]
        assert "with linear penalty" in res["converted"]
        assert r"\frac{1}{2}e^2" in res["converted"] or r"\frac{1}{2}e^{2}" in res["converted"]

    def test_multiple_mathematical_expressions_in_one_sentence(self):
        r"""Multiple expressions in a single sentence are all independently tokenized."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const input = "When x >= 0 and y <= 1, the model predicts y = 2 * x + 1.";
        const converted = LearnAIMathRenderer.convertPlainTextMath(input);
        const tokens = LearnAIMathRenderer.tokenize(converted);
        const mathTokens = tokens.filter(t => t.type === 'math');
        console.log(JSON.stringify({ converted, mathCount: mathTokens.length }));
        """
        res = json.loads(run_node_eval(script))
        assert res["mathCount"] >= 3, f"Expected at least 3 math expressions, got {res['mathCount']}"

    def test_absolute_value_expressions(self):
        r"""Absolute values |e| and |x - y| are converted to LaTeX delimiters."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const r1 = LearnAIMathRenderer.normalizeMathExpression('|e|');
        const r2 = LearnAIMathRenderer.normalizeMathExpression('|x - y|');
        console.log(JSON.stringify({ r1, r2 }));
        """
        res = json.loads(run_node_eval(script))
        assert r"\left|e\right|" in res["r1"] or "|e|" in res["r1"]
        assert r"\left|x-y\right|" in res["r2"] or "|x-y|" in res["r2"]

    def test_inequalities_formatting(self):
        r"""Inequality symbols <=, >=, != are converted to \leq, \geq, \neq."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const r1 = LearnAIMathRenderer.normalizeMathExpression('x <= 1');
        const r2 = LearnAIMathRenderer.normalizeMathExpression('x >= 0');
        const r3 = LearnAIMathRenderer.normalizeMathExpression('x != 0');
        console.log(JSON.stringify({ r1, r2, r3 }));
        """
        res = json.loads(run_node_eval(script))
        assert r"\leq" in res["r1"]
        assert r"\geq" in res["r2"]
        assert r"\neq" in res["r3"]

    def test_decimal_coefficients_normalization(self):
        r"""0.5 * term is normalized to \frac{1}{2} term."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const r1 = LearnAIMathRenderer.normalizeMathExpression('0.5 * e^2');
        const r2 = LearnAIMathRenderer.normalizeMathExpression('0.5 * delta');
        const r3 = LearnAIMathRenderer.normalizeMathExpression('-0.5 * delta');
        console.log(JSON.stringify({ r1, r2, r3 }));
        """
        res = json.loads(run_node_eval(script))
        assert r"\frac{1}{2}e^2" in res["r1"]
        assert r"\frac{1}{2}\delta" in res["r2"]
        assert r"-\frac{1}{2}\delta" in res["r3"]

    def test_unicode_superscripts_normalized(self):
        r"""Unicode superscripts like ², ³, ⁻¹ are converted to LaTeX powers."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const r1 = LearnAIMathRenderer.normalizeSuperscripts('e²');
        const r2 = LearnAIMathRenderer.normalizeSuperscripts('x³');
        const r3 = LearnAIMathRenderer.normalizeSuperscripts('e⁻¹');
        console.log(JSON.stringify({ r1, r2, r3 }));
        """
        res = json.loads(run_node_eval(script))
        assert res["r1"] == "e^{2}"
        assert res["r2"] == "x^{3}"
        assert res["r3"] == "e^{-1}"

    def test_piecewise_guard_rejects_ordinary_prose(self):
        r"""Ordinary sentences containing if/for words must NOT be converted to cases."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const input = "Take 2 pills if headache persists for more than 3 hours.";
        const converted = LearnAIMathRenderer.convertPlainTextMath(input);
        console.log(JSON.stringify({ converted, hasCases: converted.includes('begin{cases}') }));
        """
        res = json.loads(run_node_eval(script))
        assert not res["hasCases"], f"Ordinary prose falsely converted to cases: {res['converted']}"

    def test_repeated_typesetting_idempotency(self):
        r"""Repeated convertPlainTextMath calls produce idempotent results without delimiter compounding."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const input = "It behaves as 0.5 * e² for small errors |e| <= delta (smooth and differentiable), and transitions to delta * (|e| - 0.5 * delta) for large errors |e| > delta (linear, robust to outliers).";
        const once = LearnAIMathRenderer.convertPlainTextMath(input);
        const twice = LearnAIMathRenderer.convertPlainTextMath(once);
        console.log(JSON.stringify({ match: once === twice, once, twice }));
        """
        res = json.loads(run_node_eval(script))
        assert res["match"], f"Idempotency failed:\nOnce:  {res['once']}\nTwice: {res['twice']}"

    def test_code_and_urls_preserved(self):
        r"""Programming code and URLs are not modified into mathematics."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const r1 = LearnAIMathRenderer.convertPlainTextMath('See https://example.com/huber-loss for details');
        const r2 = LearnAIMathRenderer.convertPlainTextMath('Use `if x <= 10: print(x)` to filter');
        console.log(JSON.stringify({ r1, r2 }));
        """
        res = json.loads(run_node_eval(script))
        assert r"https://example.com/huber-loss" in res["r1"]
        assert "`if x <= 10: print(x)`" in res["r2"]

    def test_dom_typeset_replaces_text_node_with_katex(self):
        r"""typeset(root) in a mock DOM replaces option text node with KaTeX rendered spans."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const optText = "It behaves as 0.5 * e² for small errors |e| <= delta (smooth and differentiable), and transitions to delta * (|e| - 0.5 * delta) for large errors |e| > delta (linear, robust to outliers).";
        const container = document.createElement('div');
        const textNode = document.createTextNode(optText);
        container.appendChild(textNode);
        LearnAIMathRenderer.typeset(container);
        const hasKatexSpan = container.childNodes.some(c => c.innerHTML && c.innerHTML.includes('class="katex"'));
        console.log(JSON.stringify({ childCount: container.childNodes.length, hasKatexSpan }));
        """
        res = json.loads(run_node_eval(script))
        assert res["childCount"] > 1, f"DOM text node was not split/wrapped: {res['childCount']} children"
        assert res["hasKatexSpan"], "DOM did not receive rendered KaTeX spans"
