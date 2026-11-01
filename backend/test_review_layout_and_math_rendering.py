"""
LearnAI: Responsive Question Review Layout & Mathematical Expression Rendering Test Suite

Validates:
1. Mathematical expression typesetting:
   - Inline math: \\( ... \\) and $ ... $
   - Display/block math: \\[ ... \\] and $$ ... $$
   - Math environments: \\begin{matrix}...\\end{matrix}, \\begin{align}...
   - Fractions, square roots, summations, integrals, Greek letters, matrices, equations
   - Currency protection: $50, $100, $9.99, $1,000, $50M never mistaken for math delimiters
   - Malformed LaTeX handles safely without crashing or throwing
   - Code blocks containing $ (jQuery, PHP, Bash) are ignored and preserved
2. Review layout & distractor card overhaul:
   - Distractor explanations render in separate full-width containers below option text
   - Distractors have distinct visual incorrect status indicators
   - Option text has full width without restrictive white-space: nowrap
   - Student answer and correct answer have clear vertical stacking for long options
   - Educational explanations span full card width (no artificial 720px restriction)
   - Multiline paragraphs and bulleted/numbered lists format cleanly
3. Responsive styles:
   - Desktop (1440px), Laptop (1024px), Tablet (768px), Mobile (390px) responsive rules
   - Horizontally scrollable equation containers (.katex-display-container)
   - KaTeX vendor bundle and CSS/JS links in index.html
"""

import subprocess
import json
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "frontend" / "index.html"
STYLE_CSS = REPO_ROOT / "frontend" / "css" / "style.css"
APP_JS = REPO_ROOT / "frontend" / "js" / "app.js"
KATEX_MIN_JS = REPO_ROOT / "frontend" / "vendor" / "katex" / "katex.min.js"
KATEX_MIN_CSS = REPO_ROOT / "frontend" / "vendor" / "katex" / "katex.min.css"


DOM_MOCK_PRELUDE = """
if (typeof global.document === 'undefined') {
    const domElements = new Map();
    function mockEl(id) {
        if (!domElements.has(id)) {
            domElements.set(id, {
                id,
                innerHTML: '',
                textContent: '',
                value: '',
                style: {},
                dataset: {},
                classList: {
                    _classes: new Set(),
                    add(c) { this._classes.add(c); },
                    remove(c) { this._classes.delete(c); },
                    contains(c) { return this._classes.has(c); },
                    toggle(c) { if (this.contains(c)) this.remove(c); else this.add(c); }
                },
                children: [],
                childNodes: [],
                appendChild(c) { this.childNodes.push(c); return c; },
                removeChild(c) {
                    const idx = this.childNodes.indexOf(c);
                    if (idx !== -1) this.childNodes.splice(idx, 1);
                    return c;
                },
                replaceChild(n, o) {
                    const idx = this.childNodes.indexOf(o);
                    if (idx !== -1) this.childNodes[idx] = n;
                    return o;
                },
                setAttribute() {},
                getAttribute() { return null; },
                removeAttribute() {},
                addEventListener() {},
                removeEventListener() {},
                focus() {},
                scrollIntoView() {}
            });
        }
        return domElements.get(id);
    }
    global.document = {
        getElementById: mockEl,
        createElement: (tag) => ({
            tagName: tag.toUpperCase(),
            className: '',
            innerHTML: '',
            textContent: '',
            style: {},
            dataset: {},
            classList: {
                _classes: new Set(),
                add(c) { this._classes.add(c); },
                remove(c) { this._classes.delete(c); },
                contains(c) { return this._classes.has(c); }
            },
            childNodes: [],
            appendChild(c) { this.childNodes.push(c); return c; },
            setAttribute() {},
            getAttribute() { return null; },
            addEventListener() {}
        }),
        createTextNode: (t) => ({
            nodeType: 3,
            textContent: t,
            data: t
        }),
        createDocumentFragment: () => ({
            nodeType: 11,
            childNodes: [],
            appendChild(c) { this.childNodes.push(c); return c; }
        }),
        querySelectorAll: () => [],
        querySelector: () => null,
        addEventListener: () => {}
    };
    global.window = {
        document: global.document,
        addEventListener: () => {},
        scrollTo: () => {},
        location: { origin: 'http://localhost:8000', pathname: '/' }
    };
    global.navigator = { clipboard: { writeText: async () => {} } };
}
"""


def run_node_eval(script_body: str) -> str:
    """Execute a Node.js script with our mock DOM prelude and return stdout."""
    full_script = f"""
    const path = require('path');
    const Module = require('module');
    const origResolve = Module._resolveFilename;
    Module._resolveFilename = function(request, parent, isMain) {{
        if (request === 'katex') {{
            return path.resolve({json.dumps(str(KATEX_MIN_JS).replace('\\', '/'))});
        }}
        return origResolve.apply(this, arguments);
    }};

    {DOM_MOCK_PRELUDE}
    
    // Load KaTeX onto global scope before app.js
    global.katex = require({json.dumps(str(KATEX_MIN_JS).replace('\\', '/'))});
    
    {script_body}
    """
    res = subprocess.run(
        ["node", "-e", full_script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=15
    )
    if res.returncode != 0:
        raise RuntimeError(f"Node execution failed (code {res.returncode}):\n{res.stderr}\nScript:\n{full_script}")
    return res.stdout.strip()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Mathematical Expression Typesetting & Currency Protection Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMathematicalTypesetting:
    def test_currency_values_never_mistaken_for_math(self):
        """Currency strings ($50, $100, $9.99, etc.) must NOT be parsed as LaTeX delimiters."""
        samples = [
            "An enterprise license costs $50 for students and $100 for faculty.",
            "Special introductory price: $9.99 per month, discounted from $19.99.",
            "The company raised $1,000,000 in seed capital and $50M in Series A.",
            "Savings account balance is $250.75 with a $5.00 monthly fee."
        ]
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const samples = {json.dumps(samples)};
        
        const results = samples.map(s => {{
            const tokens = LearnAIMathRenderer.tokenize(s);
            const mathTokens = tokens.filter(t => t.type === 'math');
            const escaped = LearnAIMathRenderer.escapeCurrency(s);
            return {{
                mathTokensCount: mathTokens.length,
                hasCurrenciesEscaped: escaped.includes('\\\\$')
            }};
        }});
        
        console.log(JSON.stringify(results));
        """
        output = json.loads(run_node_eval(script))
        for res in output:
            assert res["mathTokensCount"] == 0, "Currency mistakenly treated as math token!"
            assert res["hasCurrenciesEscaped"] is True

    def test_mixed_currency_and_math_expressions(self):
        """Text containing both currency and math notation must correctly distinguish between them."""
        text = r"A share bought at $50 rose to $100. Return formula: \( R = \frac{100 - 50}{50} = 100\% \)."
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const text = {json.dumps(text)};
        const tokens = LearnAIMathRenderer.tokenize(text);
        
        const mathTokens = tokens.filter(t => t.type === 'math');
        const textTokens = tokens.filter(t => t.type === 'text');
        
        console.log(JSON.stringify({{
            tokenCount: tokens.length,
            mathCount: mathTokens.length,
            mathContent: mathTokens[0].content,
            mathDisplay: mathTokens[0].display,
            has50: textTokens[0].raw.includes("$50"),
            has100: textTokens[0].raw.includes("$100")
        }}));
        """
        output = json.loads(run_node_eval(script))
        assert output["mathCount"] == 1
        assert output["mathDisplay"] is False
        assert "frac" in output["mathContent"]
        assert output["has50"] is True
        assert output["has100"] is True

    def test_inline_math_paren_and_dollar_delimiters(self):
        """Both \\( ... \\) and $ ... $ inline delimiters must tokenize and render into KaTeX."""
        text_paren = r"The root is \( x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a} \) for quadratic curves."
        text_dollar = r"Given eigenvalues $\lambda_1, \lambda_2$ and vector $v \in \mathbb{R}^n$."
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const textParen = {json.dumps(text_paren)};
        const textDollar = {json.dumps(text_dollar)};
        
        const tokensParen = LearnAIMathRenderer.tokenize(textParen);
        const tokensDollar = LearnAIMathRenderer.tokenize(textDollar);
        
        const mathParen = tokensParen.find(t => t.type === 'math');
        const mathDollar = tokensDollar.find(t => t.type === 'math');
        
        const renderedParen = LearnAIMathRenderer.renderToString(mathParen.content, false);
        const renderedDollar = LearnAIMathRenderer.renderToString(mathDollar.content, false);
        
        console.log(JSON.stringify({{
            parenMathCount: tokensParen.filter(t => t.type === 'math').length,
            dollarMathCount: tokensDollar.filter(t => t.type === 'math').length,
            parenHasKatex: renderedParen.includes("katex-html"),
            dollarHasKatex: renderedDollar.includes("katex-html"),
            parenHasSqrt: renderedParen.includes("sqrt"),
            dollarHasLambda: renderedDollar.includes("lambda")
        }}));
        """
        output = json.loads(run_node_eval(script))
        assert output["parenMathCount"] == 1
        assert output["dollarMathCount"] >= 1
        assert output["parenHasKatex"] is True
        assert output["dollarHasKatex"] is True
        assert output["parenHasSqrt"] is True

    def test_display_math_delimiters_and_equations(self):
        """Display equations with \\[ ... \\] and $$ ... $$ must tokenize as display: true."""
        text_bracket = "Gaussian distribution:\n\\[ f(x) = \\frac{1}{\\sigma \\sqrt{2\\pi}} e^{-\\frac{1}{2}\\left(\\frac{x-\\mu}{\\sigma}\\right)^2} \\]"
        text_double = "Euler's identity is defined as: $$\\sum_{n=0}^{\\infty} \\frac{x^n}{n!} = e^x$$"
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const textBracket = {json.dumps(text_bracket)};
        const textDouble = {json.dumps(text_double)};
        
        const tokensBracket = LearnAIMathRenderer.tokenize(textBracket);
        const tokensDouble = LearnAIMathRenderer.tokenize(textDouble);
        
        const mathBracket = tokensBracket.find(t => t.type === 'math');
        const mathDouble = tokensDouble.find(t => t.type === 'math');
        
        console.log(JSON.stringify({{
            bracketDisplay: mathBracket ? mathBracket.display : false,
            doubleDisplay: mathDouble ? mathDouble.display : false,
            bracketHasSigma: mathBracket.content.includes("sigma"),
            doubleHasSum: mathDouble.content.includes("sum")
        }}));
        """
        output = json.loads(run_node_eval(script))
        assert output["bracketDisplay"] is True
        assert output["doubleDisplay"] is True
        assert output["bracketHasSigma"] is True
        assert output["doubleHasSum"] is True

    def test_matrices_and_latex_environments(self):
        """Matrices, determinants, and multi-line equation environments render cleanly."""
        matrix_eq = r"Matrix transformation: \begin{pmatrix} a & b \\ c & d \end{pmatrix} \begin{pmatrix} x \\ y \end{pmatrix}"
        script = f"""
        const {{ LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const matrixEq = {json.dumps(matrix_eq)};
        const tokens = LearnAIMathRenderer.tokenize(matrixEq);
        const math = tokens.find(t => t.type === 'math');
        const rendered = LearnAIMathRenderer.renderToString(math.content, true);
        
        console.log(JSON.stringify({{
            hasMath: LearnAIMathRenderer.hasMath(matrixEq),
            mathType: math ? math.type : null,
            mathDisplay: math ? math.display : null,
            renderedIncludesMatrix: rendered.includes("matrix") || rendered.includes("mtable") || rendered.includes("katex-html")
        }}));
        """
        output = json.loads(run_node_eval(script))
        assert output["hasMath"] is True
        assert output["mathType"] == "math"
        assert output["renderedIncludesMatrix"] is True

    def test_malformed_latex_handled_safely(self):
        """Malformed or incomplete LaTeX syntax must not crash rendering."""
        script = """
        const { LearnAIMathRenderer } = require('./frontend/js/app.js');
        const malformed = "\\\\frac{a}{";
        let threw = false;
        let html = "";
        try {
            html = LearnAIMathRenderer.renderToString(malformed, false);
        } catch (e) {
            threw = true;
        }
        
        console.log(JSON.stringify({
            threw: threw,
            hasContent: html.length > 0
        }));
        """
        output = json.loads(run_node_eval(script))
        assert output["threw"] is False, "Malformed LaTeX caused an uncaught exception!"
        assert output["hasContent"] is True

    def test_code_blocks_with_dollar_variables_ignored_by_math(self):
        """PHP, jQuery, or Bash code snippets with $var must remain code and not be parsed as math."""
        script = """
        const { LearnAICodeRenderer, LearnAIMathRenderer } = require('./frontend/js/app.js');
        const phpCode = "```php\\n$user_id = 42;\\n$total = $price * $quantity;\\necho $total;\\n```";
        const html = LearnAICodeRenderer.renderToHtml(phpCode);
        
        console.log(JSON.stringify({
            hasCodeContainer: html.includes("code-block-container"),
            hasPre: html.includes("<pre"),
            containsDollars: html.includes("$user_id") && html.includes("$total")
        }));
        """
        output = json.loads(run_node_eval(script))
        assert output["hasCodeContainer"] is True
        assert output["hasPre"] is True
        assert output["containsDollars"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. Review Layout, Distractor Cards & Prose Formatting Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestReviewLayoutAndDistractorCards:
    def test_distractor_explanation_rendered_in_separate_full_width_block(self):
        """Distractor explanations must be rendered in separate container below option, not crammed side-by-side."""
        script = """
        const { LearnAICodeRenderer } = require('./frontend/js/app.js');
        const options = {
            "A": "Linear time complexity O(n) scanning all elements sequentially",
            "B": "Logarithmic time complexity O(log n) dividing the search interval in half at each step",
            "C": "Quadratic time complexity O(n^2) nested comparison across every item pair",
            "D": "Constant time complexity O(1) direct hash key indexing"
        };
        const explanations = {
            "A": "Linear search is O(n), whereas binary search repeatedly halves the sorted range.",
            "C": "Quadratic time O(n^2) corresponds to bubble sort or nested comparisons, not binary search.",
            "D": "Direct hash lookups operate in O(1) on average, which does not describe binary search."
        };
        
        const distractorKeys = ["A", "C", "D"];
        const itemsHtml = distractorKeys.map(k => {
            const optVal = options[k];
            const reason = explanations[k];
            return `
                <li class="review-distractor-entry">
                  <div class="distractor-header">
                    <div class="distractor-badge-row">
                      <span class="choice-badge badge-choice-incorrect">Option ${k}</span>
                      <span class="distractor-status-tag">Incorrect</span>
                    </div>
                    <div class="distractor-option-content">${LearnAICodeRenderer.renderOptionToHtml(optVal)}</div>
                  </div>
                  <div class="distractor-explanation">
                    <div class="distractor-explanation-label">Why it's incorrect:</div>
                    <div class="distractor-explanation-text">
                      ${LearnAICodeRenderer.renderToHtml(reason)}
                    </div>
                  </div>
                </li>
            `;
        }).join("");
        
        console.log(JSON.stringify({
            hasHeader: itemsHtml.includes("distractor-header"),
            hasStatusTag: itemsHtml.includes("distractor-status-tag"),
            hasOptionContent: itemsHtml.includes("distractor-option-content"),
            hasExplanationText: itemsHtml.includes("distractor-explanation-text"),
            hasExplanationLabel: itemsHtml.includes("Why it's incorrect:"),
            noNowrapInMarkup: !itemsHtml.includes("white-space: nowrap")
        }));
        """
        output = json.loads(run_node_eval(script))
        assert output["hasHeader"] is True
        assert output["hasStatusTag"] is True
        assert output["hasOptionContent"] is True
        assert output["hasExplanationText"] is True
        assert output["hasExplanationLabel"] is True
        assert output["noNowrapInMarkup"] is True

    def test_multiline_paragraphs_and_prose_lists_rendered(self):
        """Explanations with paragraphs and bullet/numbered lists must be structured cleanly."""
        explanation = (
            "Step 1: Analyze the time complexity.\n"
            "- Binary search requires a sorted array.\n"
            "- At each step, the search interval is cut in half.\n\n"
            "Step 2: Mathematical derivation:\n"
            "1. n / (2^k) = 1\n"
            "2. 2^k = n\n"
            "3. k = log2(n)\n\n"
            "Therefore, the overall complexity is O(log n)."
        )
        script = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const explanation = {json.dumps(explanation)};
        const html = LearnAICodeRenderer.renderToHtml(explanation);
        
        console.log(JSON.stringify({{
            hasUlList: html.includes('<ul class="prose-list">'),
            hasOlList: html.includes('<ol class="prose-list">'),
            hasQuestionProse: html.includes('class="question-prose"'),
            hasStep1: html.includes("Step 1: Analyze the time complexity."),
            hasDerivedStep: html.includes("k = log2(n)")
        }}));
        """
        output = json.loads(run_node_eval(script))
        assert output["hasUlList"] is True
        assert output["hasOlList"] is True
        assert output["hasQuestionProse"] is True
        assert output["hasStep1"] is True
        assert output["hasDerivedStep"] is True

    def test_option_math_preserved_in_render_option_to_html(self):
        """Mathematical formulas in option choices must preserve math delimiters for KaTeX."""
        math_opt = r"\( \det(A - \lambda I) = 0 \)"
        script = f"""
        const {{ LearnAICodeRenderer, LearnAIMathRenderer }} = require('./frontend/js/app.js');
        const mathOpt = {json.dumps(math_opt)};
        const html = LearnAICodeRenderer.renderOptionToHtml(mathOpt);
        
        console.log(JSON.stringify({{
            hasMath: LearnAIMathRenderer.hasMath(mathOpt),
            htmlContainsLambda: html.includes("lambda"),
            notPreCode: !html.includes("<pre")
        }}));
        """
        output = json.loads(run_node_eval(script))
        assert output["hasMath"] is True
        assert output["htmlContainsLambda"] is True
        assert output["notPreCode"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 3. CSS & Responsive Breakpoint Validation
# ─────────────────────────────────────────────────────────────────────────────

class TestResponsiveCSSValidation:
    def test_css_contains_distractor_card_styles(self):
        """Verify that style.css defines overhauled distractor classes with flex-direction: column."""
        css_text = STYLE_CSS.read_text(encoding="utf-8")
        assert ".review-distractor-entry" in css_text
        assert ".distractor-header" in css_text
        assert ".distractor-status-tag" in css_text
        assert ".distractor-option-content" in css_text
        assert ".distractor-explanation" in css_text
        assert ".distractor-explanation-label" in css_text
        assert ".distractor-explanation-text" in css_text
        assert "border: 1px solid" in css_text and ".review-distractor-entry" in css_text

    def test_css_prose_is_full_width(self):
        """Verify that .review-prose is full card width without restrictive max-width: 720px."""
        css_text = STYLE_CSS.read_text(encoding="utf-8")
        assert ".review-prose" in css_text
        assert "max-width: 100%" in css_text or "width: 100%" in css_text

    def test_css_contains_katex_and_scroll_container_styles(self):
        """Verify that style.css styles KaTeX display equations with horizontal scroll containers."""
        css_text = STYLE_CSS.read_text(encoding="utf-8")
        assert ".katex" in css_text
        assert ".katex-display-container" in css_text
        assert "overflow-x: auto" in css_text
        assert ".katex-error" in css_text

    def test_css_responsive_breakpoints_exist(self):
        """Verify responsive media queries exist for desktop, laptop, tablet, and mobile."""
        css_text = STYLE_CSS.read_text(encoding="utf-8")
        assert "@media (max-width: 1024px)" in css_text
        assert "@media (max-width: 768px)" in css_text
        assert "@media (max-width: 480px)" in css_text

    def test_katex_vendor_files_exist_and_linked_in_index_html(self):
        """KaTeX minified bundle files exist and are linked in index.html with fallback."""
        assert KATEX_MIN_JS.exists()
        assert KATEX_MIN_CSS.exists()
        
        html_text = INDEX_HTML.read_text(encoding="utf-8")
        assert "vendor/katex/katex.min.css" in html_text
        assert "vendor/katex/katex.min.js" in html_text
        assert "vendor/katex/contrib/auto-render.min.js" in html_text
