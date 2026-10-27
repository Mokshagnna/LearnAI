"""
LearnAI: Code-Aware Quiz Rendering - Automated Test Suite

Verifies:
1. Plain-text questions render as plain text without any code containers.
2. Fenced Python snippets render in code containers with language label, preserved indentation, and syntax highlighting.
3. SQL queries render with SQL language label and uppercase keyword highlighting.
4. Java, C/C++, and JavaScript snippets render with correct badges and syntax tokens.
5. Multiple code blocks interleaved with prose preserve exact sequence.
6. Inline code (`code`) renders as distinct inline elements without container blocks.
7. Indentation, blank lines, and whitespace are strictly preserved.
8. Unknown or missing languages safely fall back to neutral "Code" / monospace formatting.
9. Malformed code fences (unclosed fence, empty block) do not crash rendering.
10. Special characters and XSS attempts (<script>alert(1)</script>, img onerror) are strictly escaped and rendered as literal text.
11. Question options with code vs prose are formatted appropriately.
12. Answer review explanations render code blocks and inline code cleanly.
13. Copy-to-clipboard button and accessible aria-labels are properly rendered.
14. Keyboard accessibility: tabindex="0" on scrollable <pre> container.
15. Responsive layout and dark mode styles exist in style.css.
16. Question element in index.html is accessible with role="heading" and aria-level="2".
"""

import subprocess
import json
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "frontend" / "index.html"
STYLE_CSS = REPO_ROOT / "frontend" / "css" / "style.css"
APP_JS = REPO_ROOT / "frontend" / "js" / "app.js"


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
                    toggle(c, f) { if (f ?? !this._classes.has(c)) this._classes.add(c); else this._classes.delete(c); }
                },
                addEventListener: () => {},
                removeEventListener: () => {},
                querySelector: (sel) => mockEl(sel),
                querySelectorAll: () => [],
                appendChild: () => {},
                removeChild: () => {},
                closest: () => null,
                scrollIntoView: () => {},
                focus: () => {}
            });
        }
        return domElements.get(id);
    }
    global.document = {
        getElementById: (id) => mockEl(id),
        querySelector: (sel) => mockEl(sel),
        querySelectorAll: () => [],
        addEventListener: () => {}
    };
    global.window = global;
    global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
    global.sessionStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
}
"""


def run_node_eval(script: str) -> str:
    """Execute a Node.js snippet and return stdout."""
    full_script = DOM_MOCK_PRELUDE + "\n" + script
    res = subprocess.run(
        ["node", "-e", full_script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8"
    )
    if res.returncode != 0:
        raise RuntimeError(f"Node execution failed (code {res.returncode}):\n{res.stderr}\nScript:\n{script}")
    return res.stdout.strip()


# ─────────────────────────────────────────────────────────────────────────────
# 1. HTML & CSS Static Structural Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCodeAwareHTMLAndCSS:
    def test_question_container_in_index_html(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert 'id="question"' in html
        assert 'class="question-heading"' in html
        assert 'role="heading"' in html
        assert 'aria-level="2"' in html

    def test_css_code_container_and_header_classes(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        assert ".code-block-container" in css
        assert ".code-block-header" in css
        assert ".code-lang-badge" in css
        assert ".code-copy-btn" in css
        assert ".code-pre" in css
        assert ".code-content" in css
        assert ".inline-code" in css
        assert ".question-prose" in css

    def test_css_syntax_token_classes(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        assert ".token-keyword" in css
        assert ".token-string" in css
        assert ".token-comment" in css
        assert ".token-number" in css
        assert ".token-builtin" in css
        assert ".token-operator" in css

    def test_css_responsive_and_dark_mode(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        assert "@media (max-width: 640px)" in css
        assert "@media (prefers-color-scheme: dark)" in css
        assert "overflow-x: auto" in css


# ─────────────────────────────────────────────────────────────────────────────
# 2. Plain Text vs Code Block Detection & Rendering Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCodeRendererDetection:
    def test_plain_text_question_remains_plain(self):
        node_code = """
        const { LearnAICodeRenderer } = require('./frontend/js/app.js');
        const q = "What is database normalization, and why is it useful?";
        const html = LearnAICodeRenderer.renderToHtml(q);
        const parsed = LearnAICodeRenderer.parseContent(q);
        
        console.log(JSON.stringify({
            hasContainer: html.includes("code-block-container"),
            hasPre: html.includes("<pre"),
            segmentCount: parsed.length,
            firstType: parsed[0].type,
            html: html
        }));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["hasContainer"] is False
        assert output["hasPre"] is False
        assert output["segmentCount"] == 1
        assert output["firstType"] == "text"
        assert "database normalization" in output["html"]

    def test_technical_prose_not_falsely_converted_to_code(self):
        """Technical sentences without code syntax must remain plain text."""
        examples = [
            "What is the time complexity of binary search?",
            "Explain the difference between a primary key and a foreign key.",
            "Which SQL command is used to retrieve records?",
            "Define First Normal Form (1NF) in relational databases."
        ]
        for ex in examples:
            node_code = f"""
            const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
            const html = LearnAICodeRenderer.renderToHtml({json.dumps(ex)});
            console.log(JSON.stringify({{
                hasContainer: html.includes("code-block-container"),
                hasPre: html.includes("<pre")
            }}));
            """
            output = json.loads(run_node_eval(node_code))
            assert output["hasContainer"] is False, f"Falsely converted to code: {ex}"
            assert output["hasPre"] is False

    def test_fenced_python_snippet_rendered(self):
        question = (
            "What is the output of this code?\n"
            "```python\n"
            "numbers = [2, 4, 6]\n"
            "result = 0\n"
            "for n in numbers:\n"
            "    result += n\n"
            "print(result)\n"
            "```\n"
            "Choose the correct option:"
        )
        node_code = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const html = LearnAICodeRenderer.renderToHtml({json.dumps(question)});
        console.log(JSON.stringify({{
            hasContainer: html.includes("code-block-container"),
            hasPythonBadge: html.includes("Python"),
            hasKeyword: html.includes("token-keyword"),
            hasBuiltin: html.includes("token-builtin"),
            hasCopyBtn: html.includes("code-copy-btn"),
            hasTabindex: html.includes('tabindex="0"'),
            html: html
        }}));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["hasContainer"] is True
        assert output["hasPythonBadge"] is True
        assert output["hasKeyword"] is True
        assert output["hasBuiltin"] is True
        assert output["hasCopyBtn"] is True
        assert output["hasTabindex"] is True
        assert "result" in output["html"] and "token-operator" in output["html"]

    def test_sql_query_rendered_with_sql_badge_and_tokens(self):
        question = (
            "Which records are returned by this query?\n"
            "```sql\n"
            "SELECT name, score\n"
            "FROM students\n"
            "WHERE score >= 80\n"
            "ORDER BY score DESC;\n"
            "```"
        )
        node_code = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const html = LearnAICodeRenderer.renderToHtml({json.dumps(question)});
        console.log(JSON.stringify({{
            hasContainer: html.includes("code-block-container"),
            hasSqlBadge: html.includes("SQL"),
            hasKeyword: html.includes("token-keyword"),
            html: html
        }}));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["hasContainer"] is True
        assert output["hasSqlBadge"] is True
        assert output["hasKeyword"] is True
        assert "SELECT" in output["html"]
        assert "students" in output["html"]

    def test_unfenced_sql_query_heuristic_detection(self):
        """Unfenced multiline SQL queries should be detected by conservative heuristic."""
        question = (
            "Consider the following database query:\n"
            "SELECT id, title, price\n"
            "FROM books\n"
            "WHERE price < 20\n"
            "ORDER BY price ASC;\n"
            "What does this query return?"
        )
        node_code = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const html = LearnAICodeRenderer.renderToHtml({json.dumps(question)});
        console.log(JSON.stringify({{
            hasContainer: html.includes("code-block-container"),
            hasSqlBadge: html.includes("SQL"),
            hasLeading: html.includes("Consider the following database query:"),
            hasTrailing: html.includes("What does this query return?"),
            html: html
        }}));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["hasContainer"] is True
        assert output["hasSqlBadge"] is True
        assert output["hasLeading"] is True
        assert output["hasTrailing"] is True

    def test_java_snippet_rendering(self):
        question = (
            "What will this Java program print?\n"
            "```java\n"
            "public class Main {\n"
            "    public static void main(String[] args) {\n"
            "        System.out.println(\"Hello LearnAI\");\n"
            "    }\n"
            "}\n"
            "```"
        )
        node_code = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const html = LearnAICodeRenderer.renderToHtml({json.dumps(question)});
        console.log(JSON.stringify({{
            hasContainer: html.includes("code-block-container"),
            hasJavaBadge: html.includes("Java"),
            hasKeyword: html.includes("token-keyword"),
            hasString: html.includes("token-string"),
            html: html
        }}));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["hasContainer"] is True
        assert output["hasJavaBadge"] is True
        assert output["hasKeyword"] is True
        assert output["hasString"] is True
        assert "Hello LearnAI" in output["html"]

    def test_cpp_snippet_rendering(self):
        question = (
            "Consider this C++ function:\n"
            "```cpp\n"
            "#include <iostream>\n"
            "int main() {\n"
            "    std::cout << \"Output: \" << 42 << std::endl;\n"
            "    return 0;\n"
            "}\n"
            "```"
        )
        node_code = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const html = LearnAICodeRenderer.renderToHtml({json.dumps(question)});
        console.log(JSON.stringify({{
            hasContainer: html.includes("code-block-container"),
            hasCppBadge: html.includes("C++"),
            hasDirective: html.includes("token-directive"),
            html: html
        }}));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["hasContainer"] is True
        assert output["hasCppBadge"] is True
        assert output["hasDirective"] is True

    def test_javascript_snippet_rendering(self):
        question = (
            "What does the following JavaScript code output?\n"
            "```javascript\n"
            "const sum = (a, b) => a + b;\n"
            "console.log(sum(10, 20));\n"
            "```"
        )
        node_code = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const html = LearnAICodeRenderer.renderToHtml({json.dumps(question)});
        console.log(JSON.stringify({{
            hasContainer: html.includes("code-block-container"),
            hasJsBadge: html.includes("JavaScript"),
            hasKeyword: html.includes("token-keyword"),
            html: html
        }}));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["hasContainer"] is True
        assert output["hasJsBadge"] is True
        assert output["hasKeyword"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 3. Interleaving, Formatting & Inline Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCodeInterleavingAndFormatting:
    def test_multiple_code_blocks_interleaved_with_prose(self):
        question = (
            "Compare Function A:\n"
            "```python\n"
            "def fn_a():\n"
            "    return 1\n"
            "```\n"
            "With Function B:\n"
            "```python\n"
            "def fn_b():\n"
            "    return 2\n"
            "```\n"
            "Which function returns the larger integer?"
        )
        node_code = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const parsed = LearnAICodeRenderer.parseContent({json.dumps(question)});
        const html = LearnAICodeRenderer.renderToHtml({json.dumps(question)});
        console.log(JSON.stringify({{
            segmentCount: parsed.length,
            types: parsed.map(s => s.type),
            containersCount: (html.match(/code-block-container/g) || []).length,
            firstSnippet: parsed[1].code,
            secondSnippet: parsed[3].code
        }}));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["segmentCount"] == 5
        assert output["types"] == ["text", "code", "text", "code", "text"]
        assert output["containersCount"] == 2
        assert "fn_a" in output["firstSnippet"]
        assert "fn_b" in output["secondSnippet"]

    def test_inline_code_rendering(self):
        text = "What is the effect of the `SELECT DISTINCT` keyword when used on `employee_id`?"
        node_code = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const html = LearnAICodeRenderer.renderToHtml({json.dumps(text)});
        console.log(JSON.stringify({{
            hasInlineCode: html.includes('class="inline-code"'),
            inlineCount: (html.match(/class="inline-code"/g) || []).length,
            hasContainer: html.includes("code-block-container")
        }}));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["hasInlineCode"] is True
        assert output["inlineCount"] == 2
        assert output["hasContainer"] is False

    def test_indentation_and_blank_lines_preserved(self):
        code = (
            "```python\n"
            "class Tree:\n"
            "    def __init__(self, val):\n"
            "        self.val = val\n"
            "\n"
            "        self.left = None\n"
            "        self.right = None\n"
            "```"
        )
        node_code = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const parsed = LearnAICodeRenderer.parseContent({json.dumps(code)});
        console.log(JSON.stringify({{
            hasFourSpaces: parsed[0].code.includes("    def"),
            hasEightSpaces: parsed[0].code.includes("        self.val"),
            hasBlankLine: parsed[0].code.includes("\\n\\n")
        }}));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["hasFourSpaces"] is True
        assert output["hasEightSpaces"] is True
        assert output["hasBlankLine"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 4. Security, XSS & Malformed Edge Case Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSecurityAndEdgeCases:
    def test_xss_script_tags_rendered_as_safe_text(self):
        question = "<script>alert('XSS-ATTACK');</script>"
        node_code = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const html = LearnAICodeRenderer.renderToHtml({json.dumps(question)});
        console.log(JSON.stringify({{
            hasRawScriptTag: html.includes("<script>"),
            hasEscapedScript: html.includes("&lt;script&gt;"),
            html: html
        }}));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["hasRawScriptTag"] is False
        assert output["hasEscapedScript"] is True

    def test_xss_in_code_block_rendered_safely(self):
        question = (
            "Inspect this HTML snippet:\n"
            "```html\n"
            "<script>alert('PWNED')</script>\n"
            "<img src=x onerror=alert(1)>\n"
            "```"
        )
        node_code = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const html = LearnAICodeRenderer.renderToHtml({json.dumps(question)});
        console.log(JSON.stringify({{
            hasRawScript: html.includes("<script>"),
            hasRawImg: html.includes("<img src=x"),
            hasEscapedScript: html.includes("&lt;script&gt;"),
            hasEscapedImg: html.includes("&lt;img src=x"),
            html: html
        }}));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["hasRawScript"] is False
        assert output["hasRawImg"] is False
        assert output["hasEscapedScript"] is True
        assert output["hasEscapedImg"] is True

    def test_unclosed_markdown_fence_handled_gracefully(self):
        """Unclosed fence at EOF must be consumed safely without throwing."""
        question = "What is the result?\n```python\nfor i in range(5):\n    print(i)"
        node_code = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const html = LearnAICodeRenderer.renderToHtml({json.dumps(question)});
        console.log(JSON.stringify({{
            hasContainer: html.includes("code-block-container"),
            hasPre: html.includes("<pre"),
            hasCode: html.includes("print") && html.includes("range")
        }}));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["hasContainer"] is True
        assert output["hasPre"] is True
        assert output["hasCode"] is True

    def test_unknown_language_fallback(self):
        question = "```obscurelang\nVAR x := 100;\n```"
        node_code = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const html = LearnAICodeRenderer.renderToHtml({json.dumps(question)});
        console.log(JSON.stringify({{
            hasContainer: html.includes("code-block-container"),
            hasBadge: html.includes("OBSCURELANG"),
            hasCode: html.includes("VAR x := 100;")
        }}));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["hasContainer"] is True
        assert output["hasBadge"] is True
        assert output["hasCode"] is True

    def test_empty_and_null_inputs(self):
        node_code = """
        const { LearnAICodeRenderer } = require('./frontend/js/app.js');
        console.log(JSON.stringify({
            nullRes: LearnAICodeRenderer.renderToHtml(null),
            undefinedRes: LearnAICodeRenderer.renderToHtml(undefined),
            emptyRes: LearnAICodeRenderer.renderToHtml(""),
            whitespaceRes: LearnAICodeRenderer.renderToHtml("   \\n  ")
        }));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["nullRes"] == ""
        assert output["undefinedRes"] == ""
        assert output["emptyRes"] == ""


# ─────────────────────────────────────────────────────────────────────────────
# 5. Options & Explanation Review Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestOptionsAndExplanationRendering:
    def test_option_with_inline_code(self):
        opt = "`SELECT COUNT(*)`"
        node_code = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const html = LearnAICodeRenderer.renderOptionToHtml({json.dumps(opt)});
        console.log(JSON.stringify({{
            hasInlineCode: html.includes('class="inline-code"'),
            content: html
        }}));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["hasInlineCode"] is True
        assert "SELECT COUNT(*)" in output["content"]

    def test_option_with_plain_text(self):
        opt = "Both A and B are correct"
        node_code = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const html = LearnAICodeRenderer.renderOptionToHtml({json.dumps(opt)});
        console.log(JSON.stringify({{
            hasInlineCode: html.includes('class="inline-code"'),
            html: html
        }}));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["hasInlineCode"] is False
        assert output["html"] == "Both A and B are correct"

    def test_explanation_with_code_block(self):
        explanation = (
            "The query must use a GROUP BY clause to aggregate:\n"
            "```sql\n"
            "SELECT dept_id, COUNT(*)\n"
            "FROM employees\n"
            "GROUP BY dept_id;\n"
            "```"
        )
        node_code = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const html = LearnAICodeRenderer.renderToHtml({json.dumps(explanation)});
        console.log(JSON.stringify({{
            hasContainer: html.includes("code-block-container"),
            hasSqlBadge: html.includes("SQL"),
            hasExplanationProse: html.includes("GROUP BY clause to aggregate:")
        }}));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["hasContainer"] is True
        assert output["hasSqlBadge"] is True
        assert output["hasExplanationProse"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 6. Accessibility & Copy Control Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAccessibilityAndCopyControl:
    def test_copy_button_and_pre_tabindex_rendered(self):
        question = "```python\nprint(123)\n```"
        node_code = f"""
        const {{ LearnAICodeRenderer }} = require('./frontend/js/app.js');
        const html = LearnAICodeRenderer.renderToHtml({json.dumps(question)});
        console.log(JSON.stringify({{
            hasCopyBtn: html.includes('class="code-copy-btn"'),
            hasAriaLabel: html.includes('aria-label="Copy Python code to clipboard"'),
            hasTabindex: html.includes('tabindex="0"'),
            hasRoleRegion: html.includes('role="region"')
        }}));
        """
        output = json.loads(run_node_eval(node_code))
        assert output["hasCopyBtn"] is True
        assert output["hasAriaLabel"] is True
        assert output["hasTabindex"] is True
        assert output["hasRoleRegion"] is True
