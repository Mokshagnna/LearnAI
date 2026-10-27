const $ = id => document.getElementById(id);

// ── API Routing & Base URL Resolution ────────────────────────────────────────
function getApiBaseUrl(loc = (typeof window !== "undefined" ? window.location : null), win = (typeof window !== "undefined" ? window : null)) {
  if (win && win.LEARNAI_API_BASE_URL && typeof win.LEARNAI_API_BASE_URL === "string") {
    return win.LEARNAI_API_BASE_URL.replace(/\/+$/, "");
  }
  if (win && win.localStorage) {
    try {
      const stored = win.localStorage.getItem("learnai_api_base_url");
      if (stored && typeof stored === "string") {
        return stored.replace(/\/+$/, "");
      }
    } catch (_) {}
  }

  if (loc) {
    if (loc.protocol === "file:") {
      return "http://127.0.0.1:8000";
    }
    const hostname = (loc.hostname || "").toLowerCase();
    const port = String(loc.port || "");
    const isLocalhost = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1" || hostname.endsWith(".local");

    // When running on a separate local frontend development server (e.g. port 5500, 3000)
    // that is distinct from the FastAPI server port (8000), route API requests to FastAPI at http://127.0.0.1:8000.
    if (isLocalhost && port !== "8000" && port !== "") {
      return "http://127.0.0.1:8000";
    }
  }

  // In production (e.g. Render) or when served directly by FastAPI on port 8000,
  // use same-origin relative paths (empty base prefix).
  return "";
}

const API_BASE_URL = getApiBaseUrl();

function apiUrl(endpoint, base = API_BASE_URL) {
  if (!endpoint || typeof endpoint !== "string") return endpoint || "";
  if (endpoint.startsWith("http://") || endpoint.startsWith("https://")) {
    return endpoint;
  }
  const cleanEndpoint = endpoint.startsWith("/") ? endpoint : `/${endpoint}`;
  return base ? `${base}${cleanEndpoint}` : cleanEndpoint;
}


// ── Application State ────────────────────────────────────────────────────────
let curriculum = [];
let currentQuiz = null;
let currentIndex = 0;
let answers = [];
let activeCourseCode = "";
let activeTopicName = "";
let activeTopicId = "";
let activeNotesSessionId = null;
let isGenerating = false;
let activeAbortController = null;
let studentId = (typeof localStorage !== "undefined" && localStorage.getItem) ? (localStorage.getItem("learnai_student_id") || null) : null;
let authToken = (typeof localStorage !== "undefined" && localStorage.getItem) ? (localStorage.getItem("learnai_auth_token") || null) : null;
let authSessionPromise = null;

// ── Student Authentication & Session Management ──────────────────────────────
function isTokenExpired(token, bufferSeconds = 30) {
  if (!token || typeof token !== "string") return true;
  const parts = token.trim().split(".");
  if (parts.length !== 3) return true;
  const expTs = parseInt(parts[1], 10);
  if (isNaN(expTs) || expTs <= 0) return true;
  const nowTs = Math.floor(Date.now() / 1000);
  return nowTs >= (expTs - bufferSeconds);
}

async function ensureAuthSession(forceRefresh = false) {
  if (!studentId && typeof localStorage !== "undefined" && localStorage.getItem) {
    studentId = localStorage.getItem("learnai_student_id") || null;
  }
  if (!authToken && typeof localStorage !== "undefined" && localStorage.getItem) {
    authToken = localStorage.getItem("learnai_auth_token") || null;
  }

  // If we already have a valid unexpired token and are not forcing renewal, return it
  if (!forceRefresh && authToken && studentId && !isTokenExpired(authToken)) {
    return { token: authToken, studentId: studentId };
  }

  // Deduplicate concurrent session requests
  if (authSessionPromise) {
    return authSessionPromise;
  }

  authSessionPromise = (async () => {
    try {
      const headers = { "Content-Type": "application/json" };
      if (authToken) {
        headers["Authorization"] = `Bearer ${authToken}`;
      }
      if (typeof fetch === "function") {
        const res = await fetch(apiUrl("/api/auth/session"), {
          method: "POST",
          headers: headers
        });
        if (res.ok) {
          const data = await res.json();
          authToken = data.token;
          studentId = data.student_id;
          if (typeof localStorage !== "undefined" && localStorage.setItem) {
            localStorage.setItem("learnai_auth_token", authToken);
            localStorage.setItem("learnai_student_id", studentId);
          }
          return { token: authToken, studentId: studentId };
        } else {
          console.warn("[AUTH] /api/auth/session rejected with status", res.status);
          if (typeof localStorage !== "undefined" && localStorage.removeItem) {
            localStorage.removeItem("learnai_auth_token");
          }
          authToken = null;
        }
      }
    } catch (err) {
      console.warn("Failed to initialize server auth session:", err);
    } finally {
      authSessionPromise = null;
    }
    return { token: authToken, studentId: studentId };
  })();

  return authSessionPromise;
}

function getStudentId() {
  return studentId;
}

function getAuthToken() {
  if (isTokenExpired(authToken)) {
    return null;
  }
  return authToken;
}

async function authenticatedFetch(url, options = {}) {
  const resolvedUrl = apiUrl(url);
  await ensureAuthSession();
  options.headers = options.headers || {};
  if (authToken && !isTokenExpired(authToken)) {
    options.headers["Authorization"] = `Bearer ${authToken}`;
  }
  let res = await fetch(resolvedUrl, options);
  if (res.status === 401) {
    console.warn("[AUTH] 401 Unauthorized received. Renewing session and retrying fetch once...");
    await ensureAuthSession(true);
    if (authToken) {
      options.headers["Authorization"] = `Bearer ${authToken}`;
      res = await fetch(resolvedUrl, options);
    }
  }
  return res;
}

// ── HTML Safety & Escaping Helper ───────────────────────────────────────────
function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
// ── Code-Aware Quiz Rendering Engine ─────────────────────────────────────────
const LearnAICodeRenderer = {
  TOKEN_PATTERNS: {
    python: [
      { type: "comment", regex: /#[^\n]*/g },
      { type: "string", regex: /\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|r?f?b?\"(?:\\.|[^\"\\\n])*\"|r?f?b?'(?:\\.|[^'\\\n])*'/g },
      { type: "number", regex: /\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b|\b0[xX][0-9a-fA-F]+\b/g },
      { type: "keyword", regex: /\b(?:def|class|return|if|elif|else|for|while|in|not|and|or|is|import|from|as|try|except|finally|with|lambda|yield|raise|pass|break|continue|async|await|assert|global|nonlocal)\b/g },
      { type: "boolean", regex: /\b(?:True|False|None)\b/g },
      { type: "builtin", regex: /\b(?:print|len|range|enumerate|zip|sum|min|max|abs|all|any|str|int|float|bool|list|dict|set|tuple|open|type|isinstance|sorted|input)\b/g },
      { type: "operator", regex: /==|!=|<=|>=|\+=|-=|\*=|[\+\-\*\/%=\<\>]/g }
    ],
    sql: [
      { type: "comment", regex: /--[^\n]*|\/\*[\s\S]*?\*\//g },
      { type: "string", regex: /'(?:''|[^'])*'|\"(?:\\.|[^\"\\\n])*\"/g },
      { type: "number", regex: /\b\d+(?:\.\d+)?\b/g },
      { type: "keyword", regex: /\b(?:SELECT|FROM|WHERE|INSERT|INTO|VALUES|UPDATE|SET|DELETE|JOIN|INNER|LEFT|RIGHT|FULL|OUTER|CROSS|ON|GROUP|BY|HAVING|ORDER|ASC|DESC|LIMIT|OFFSET|CREATE|TABLE|ALTER|DROP|INDEX|PRIMARY|KEY|FOREIGN|REFERENCES|CHECK|UNIQUE|DEFAULT|NOT|NULL|AND|OR|IN|IS|BETWEEN|LIKE|EXISTS|UNION|ALL|AS|DISTINCT|VIEW|TRIGGER|CASE|WHEN|THEN|ELSE|END|WITH)\b/gi },
      { type: "builtin", regex: /\b(?:COUNT|SUM|AVG|MIN|MAX|ROUND|COALESCE|NOW|CURRENT_TIMESTAMP|UPPER|LOWER|TRIM|SUBSTRING|CONCAT|CAST)\b/gi }
    ],
    javascript: [
      { type: "comment", regex: /\/\/[^\n]*|\/\*[\s\S]*?\*\//g },
      { type: "string", regex: /\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`[\s\S]*?`/g },
      { type: "number", regex: /\b\d+(?:\.\d+)?\b|\b0[xX][0-9a-fA-F]+\b/g },
      { type: "keyword", regex: /\b(?:function|const|let|var|return|if|else|for|while|do|switch|case|break|continue|default|import|export|from|class|extends|new|this|super|async|await|try|catch|finally|throw|typeof|instanceof|in|of|void|delete)\b/g },
      { type: "boolean", regex: /\b(?:true|false|null|undefined|NaN)\b/g },
      { type: "builtin", regex: /\b(?:console|Math|JSON|Promise|Array|Object|String|Number|Boolean|setTimeout|setInterval)\b/g }
    ],
    java: [
      { type: "comment", regex: /\/\/[^\n]*|\/\*[\s\S]*?\*\//g },
      { type: "string", regex: /\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'/g },
      { type: "number", regex: /\b\d+(?:\.\d+)?[fFdDlL]?\b|\b0[xX][0-9a-fA-F]+\b/g },
      { type: "keyword", regex: /\b(?:public|private|protected|class|interface|enum|extends|implements|static|final|abstract|void|int|double|float|long|short|byte|char|boolean|String|return|if|else|for|while|do|switch|case|break|continue|default|new|this|super|try|catch|finally|throw|throws|import|package|instanceof)\b/g },
      { type: "boolean", regex: /\b(?:true|false|null)\b/g },
      { type: "builtin", regex: /\b(?:System|out|println|print|Math|Arrays|Collections|Override)\b/g }
    ],
    cpp: [
      { type: "comment", regex: /\/\/[^\n]*|\/\*[\s\S]*?\*\//g },
      { type: "directive", regex: /#[a-zA-Z_]+[^\n]*/g },
      { type: "string", regex: /\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'/g },
      { type: "number", regex: /\b\d+(?:\.\d+)?[fFlLuU]?\b|\b0[xX][0-9a-fA-F]+\b/g },
      { type: "keyword", regex: /\b(?:int|float|double|char|void|long|short|unsigned|signed|bool|struct|union|enum|typedef|auto|const|static|extern|return|if|else|for|while|do|switch|case|break|continue|default|goto|sizeof|nullptr|class|public|private|protected|virtual|override|namespace|using|template|typename|new|delete|try|catch|throw|inline)\b/g },
      { type: "boolean", regex: /\b(?:true|false|NULL)\b/g },
      { type: "builtin", regex: /\b(?:std|cout|cin|endl|string|vector|printf|scanf|malloc|free)\b/g }
    ]
  },

  normalizeLanguage(lang) {
    if (!lang) return "";
    const l = String(lang).trim().toLowerCase();
    const map = {
      py: "python", python: "python", python3: "python",
      sql: "sql", mysql: "sql", postgresql: "sql", sqlite: "sql", tsql: "sql", plsql: "sql",
      js: "javascript", javascript: "javascript", node: "javascript", jsx: "javascript",
      ts: "typescript", typescript: "typescript", tsx: "typescript",
      java: "java",
      c: "c",
      cpp: "cpp", "c++": "cpp", cc: "cpp", cxx: "cpp", h: "cpp", hpp: "cpp",
      html: "html", htm: "html", xml: "html",
      css: "css", scss: "css", sass: "css",
      json: "json",
      sh: "bash", bash: "bash", shell: "bash", zsh: "bash",
      pseudo: "pseudocode", pseudocode: "pseudocode", algorithm: "pseudocode"
    };
    return map[l] || l;
  },

  getLanguageLabel(lang) {
    const norm = this.normalizeLanguage(lang);
    const labels = {
      python: "Python",
      sql: "SQL",
      java: "Java",
      c: "C",
      cpp: "C++",
      javascript: "JavaScript",
      typescript: "TypeScript",
      html: "HTML",
      css: "CSS",
      json: "JSON",
      bash: "Bash",
      pseudocode: "Pseudocode"
    };
    return labels[norm] || (lang ? String(lang).toUpperCase() : "Code");
  },

  detectLanguage(code) {
    if (!code || typeof code !== "string") return "";
    const clean = code.trim();

    if (/^\s*(?:SELECT\b[\s\S]*?\bFROM\b|INSERT\s+INTO\b|UPDATE\s+\w+\s+SET\b|DELETE\s+FROM\b|CREATE\s+TABLE\b|ALTER\s+TABLE\b)/i.test(clean)) {
      return "sql";
    }
    if (/#include\s*<[^>]+>|\bstd::cout\b|\bstd::cin\b|\bint\s+main\s*\(/.test(clean)) {
      return "cpp";
    }
    if (/\bpublic\s+(?:class|interface|static\s+void\s+main)\b|\bSystem\.(?:out|err)\.println\b/.test(clean)) {
      return "java";
    }
    if (/\bdef\s+[a-zA-Z_]\w*\s*\(|\bclass\s+[a-zA-Z_]\w*[:\(]|^\s*(?:import\s+[a-zA-Z_]|from\s+[a-zA-Z_]+\s+import)\b|:\s*\n\s{2,}/m.test(clean)) {
      return "python";
    }
    if (/\b(?:console\.log|const\s+\w+\s*=|let\s+\w+\s*=|function\s*\w*\s*\(|=>\s*\{)/.test(clean)) {
      return "javascript";
    }
    if (/^[\{\[][\s\S]*?[\}\]]$/.test(clean) && /\"[^\"]+\"\s*:/.test(clean)) {
      return "json";
    }
    if (/<!DOCTYPE\s+html|<(?:html|head|body|div|span|p|table|script)\b/i.test(clean)) {
      return "html";
    }
    return "";
  },

  highlight(code, language) {
    if (!code) return "";
    const norm = this.normalizeLanguage(language);
    const langKey = (norm === "c" ? "cpp" : (norm === "typescript" ? "javascript" : norm));
    const patterns = this.TOKEN_PATTERNS[langKey];
    if (!patterns) return escapeHtml(code);

    let tokens = [];
    patterns.forEach(({ type, regex }) => {
      regex.lastIndex = 0;
      let match;
      while ((match = regex.exec(code)) !== null) {
        tokens.push({
          type,
          start: match.index,
          end: match.index + match[0].length,
          text: match[0]
        });
      }
    });

    tokens.sort((a, b) => a.start - b.start || (b.end - b.start) - (a.end - a.start));

    let nonOverlapping = [];
    let lastEnd = 0;
    for (const t of tokens) {
      if (t.start >= lastEnd) {
        nonOverlapping.push(t);
        lastEnd = t.end;
      }
    }

    let out = "";
    let cursor = 0;
    for (const t of nonOverlapping) {
      if (t.start > cursor) {
        out += escapeHtml(code.slice(cursor, t.start));
      }
      out += `<span class="code-token token-${t.type}">${escapeHtml(t.text)}</span>`;
      cursor = t.end;
    }
    if (cursor < code.length) {
      out += escapeHtml(code.slice(cursor));
    }
    return out;
  },

  renderInline(text) {
    if (text === null || text === undefined) return "";
    const s = String(text);
    const parts = [];
    let lastIndex = 0;
    const inlineRegex = /`([^`\n]+)`/g;
    let match;
    while ((match = inlineRegex.exec(s)) !== null) {
      if (match.index > lastIndex) {
        parts.push(escapeHtml(s.slice(lastIndex, match.index)));
      }
      parts.push(`<code class="inline-code">${escapeHtml(match[1])}</code>`);
      lastIndex = match.index + match[0].length;
    }
    if (lastIndex < s.length) {
      parts.push(escapeHtml(s.slice(lastIndex)));
    }
    return parts.join("");
  },

  parseContent(rawContent, options = {}) {
    if (!rawContent) return [];

    if (typeof rawContent === "object" && Array.isArray(rawContent.question_segments)) {
      return rawContent.question_segments;
    }
    if (typeof rawContent === "object" && rawContent.code) {
      return [
        { type: "text", content: rawContent.question_text || rawContent.question || "" },
        { type: "code", code: rawContent.code, language: rawContent.language || options.language || "" }
      ];
    }

    const text = String(rawContent).trim();
    if (!text) return [];

    const fenceRegex = /```([a-zA-Z0-9_+#.-]*)\s*\n?([\s\S]*?)(?:```|$)/g;
    let hasFences = false;
    let segments = [];
    let lastIdx = 0;
    let match;

    while ((match = fenceRegex.exec(text)) !== null) {
      const rawLang = match[1] ? match[1].trim() : "";
      const rawSnippet = match[2];

      hasFences = true;
      if (match.index > lastIdx) {
        const prose = text.slice(lastIdx, match.index);
        if (prose.trim()) {
          segments.push({ type: "text", content: prose.trim() });
        }
      }

      const detected = rawLang || this.detectLanguage(rawSnippet) || options.language || "";
      segments.push({
        type: "code",
        code: rawSnippet.replace(/\n+$/, ""),
        language: detected
      });

      lastIdx = match.index + match[0].length;
      if (match[0].indexOf("```", 3) === -1) {
        break;
      }
    }

    if (hasFences) {
      if (lastIdx < text.length) {
        const trailing = text.slice(lastIdx);
        if (trailing.trim()) {
          segments.push({ type: "text", content: trailing.trim() });
        }
      }
      return segments;
    }

    const heuristic = this._detectUnformattedCode(text, options);
    if (heuristic) {
      return heuristic;
    }

    return [{ type: "text", content: text }];
  },

  _detectUnformattedCode(text, options = {}) {
    const lines = text.split("\n");
    if (lines.length < 3) return null;

    const sqlStartIdx = lines.findIndex(l => /^\s*SELECT\b/i.test(l) || /^\s*CREATE\s+TABLE\b/i.test(l) || /^\s*INSERT\s+INTO\b/i.test(l));
    if (sqlStartIdx !== -1) {
      let sqlEndIdx = sqlStartIdx;
      for (let i = sqlStartIdx; i < lines.length; i++) {
        const line = lines[i];
        if (/(?:FROM|WHERE|JOIN|GROUP|ORDER|HAVING|VALUES|LIMIT|;\s*$)/i.test(line) || /^\s{2,}/.test(line)) {
          sqlEndIdx = i;
          if (/;\s*$/.test(line)) break;
        } else if (i > sqlStartIdx && !line.trim()) {
          continue;
        } else if (i > sqlStartIdx) {
          break;
        }
      }

      if (sqlEndIdx > sqlStartIdx || /;\s*$/.test(lines[sqlStartIdx])) {
        const leading = lines.slice(0, sqlStartIdx).join("\n").trim();
        const code = lines.slice(sqlStartIdx, sqlEndIdx + 1).join("\n");
        const trailing = lines.slice(sqlEndIdx + 1).join("\n").trim();

        const segments = [];
        if (leading) segments.push({ type: "text", content: leading });
        segments.push({ type: "code", code: code.trim(), language: "sql" });
        if (trailing) segments.push({ type: "text", content: trailing });
        return segments;
      }
    }

    const codeStartIdx = lines.findIndex(l =>
      /^\s*(?:def\s+\w+\s*\(|class\s+\w+|for\s+\w+\s+in\s+|if\s+.+:|#include\s*<|public\s+class\s+|int\s+main\s*\()/.test(l)
    );

    if (codeStartIdx !== -1) {
      let codeEndIdx = codeStartIdx;
      for (let i = codeStartIdx + 1; i < lines.length; i++) {
        const line = lines[i];
        if (/^\s{2,}|\t/.test(line) || /^\s*(?:return\b|print\(|cout\s*<<|System\.out|\}|else:|elif\b)/.test(line) || !line.trim()) {
          codeEndIdx = i;
        } else {
          break;
        }
      }

      if (codeEndIdx > codeStartIdx) {
        const leading = lines.slice(0, codeStartIdx).join("\n").trim();
        const code = lines.slice(codeStartIdx, codeEndIdx + 1).join("\n");
        const trailing = lines.slice(codeEndIdx + 1).join("\n").trim();

        const lang = this.detectLanguage(code) || options.language || "python";
        const segments = [];
        if (leading) segments.push({ type: "text", content: leading });
        segments.push({ type: "code", code: code.trim(), language: lang });
        if (trailing) segments.push({ type: "text", content: trailing });
        return segments;
      }
    }

    return null;
  },

  renderToHtml(rawContent, options = {}) {
    const segments = this.parseContent(rawContent, options);
    if (!segments || segments.length === 0) return "";

    return segments.map(seg => {
      if (seg.type === "code") {
        const lang = seg.language || options.language || "";
        const label = this.getLanguageLabel(lang);
        const highlighted = this.highlight(seg.code, lang);

        return (
          `<div class="code-block-container" role="region" aria-label="${escapeHtml(label)} code snippet">` +
            `<div class="code-block-header">` +
              `<span class="code-lang-badge"><span class="code-lang-icon" aria-hidden="true">&lt;/&gt;</span> ${escapeHtml(label)}</span>` +
              `<button type="button" class="code-copy-btn" aria-label="Copy ${escapeHtml(label)} code to clipboard" title="Copy code">` +
                `<svg class="copy-icon" aria-hidden="true" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">` +
                  `<rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>` +
                  `<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>` +
                `</svg>` +
                `<span class="copy-btn-text">Copy</span>` +
              `</button>` +
            `</div>` +
            `<pre class="code-pre" tabindex="0" aria-label="${escapeHtml(label)} code, horizontally scrollable"><code class="code-content language-${escapeHtml(this.normalizeLanguage(lang))}">${highlighted}</code></pre>` +
          `</div>`
        );
      } else {
        const prose = this.renderInline(seg.content || "");
        return `<div class="question-prose">${prose}</div>`;
      }
    }).join("");
  },

  renderToElement(rawContent, targetEl, options = {}) {
    if (!targetEl) return;
    const segments = this.parseContent(rawContent, options);

    if (segments.length === 1 && segments[0].type === "text" && !/`[^`\n]+`/.test(segments[0].content)) {
      targetEl.textContent = segments[0].content;
      return;
    }

    targetEl.innerHTML = this.renderToHtml(rawContent, options);
  },

  renderOptionToHtml(optionValue) {
    if (optionValue === null || optionValue === undefined) return "";
    const s = String(optionValue);
    if (/`[^`\n]+`/.test(s)) {
      return this.renderInline(s);
    }
    if (/^\s*(?:SELECT\b|UPDATE\b|DELETE\b|INSERT\b|def\b|class\b|System\.out|console\.|return\b|#include|int\b|float\b|char\b)/i.test(s) || /;\s*$/.test(s) || /^[a-zA-Z_]\w*\s*=\s*.+$/.test(s)) {
      return `<code class="inline-code">${escapeHtml(s)}</code>`;
    }
    return escapeHtml(s);
  },

  renderOptionToElement(optionValue, targetEl) {
    if (!targetEl) return;
    const html = this.renderOptionToHtml(optionValue);
    if (html.indexOf("<") !== -1) {
      targetEl.innerHTML = html;
    } else {
      targetEl.textContent = optionValue;
    }
  }
};


// ── Mutually Exclusive Screen Management ────────────────────────────────────
// Ensures EXACTLY ONE screen is visible at any time.
const ALL_SCREENS = ["setup", "loadingSection", "quizSection", "resultSection", "dashboardSection", "notesUploadSection"];

let lastActiveScreen = "setup";

function showScreen(screenId, pushHistory = true) {
  if (screenId === "materialsSection") {
    screenId = "notesUploadSection";
  }

  ALL_SCREENS.forEach(id => {
    const el = $(id);
    if (el) {
      if (id === screenId) {
        el.classList.remove("hidden");
      } else {
        el.classList.add("hidden");
      }
    }
  });

  if (screenId !== "loadingSection") {
    lastActiveScreen = screenId;
  }

  const isDashboard = screenId === "dashboardSection";
  const isNotes = screenId === "notesUploadSection";
  $("navDashboardBtn")?.classList.toggle("active", isDashboard);
  $("navNotesBtn")?.classList.toggle("active", isNotes);
  $("navMaterialsBtn")?.classList.toggle("active", isNotes);
  $("navPracticeBtn")?.classList.toggle("active", !isDashboard && !isNotes);

  if (isNotes && typeof loadStudyMaterials === "function") {
    const expandId = lastPracticedStudyMaterialId || expandedMaterialId;
    loadStudyMaterials({ expandMaterialId: expandId });
  }

  // Safe programmatic focus on screen heading for screen readers & keyboard navigation
  const activeEl = $(screenId);
  if (activeEl) {
    const heading = activeEl.querySelector("h1, h2");
    if (heading) {
      heading.setAttribute("tabindex", "-1");
      heading.focus({ preventScroll: true });
    }
  }

  // Push history state if supported (excluding transient loading state)
  if (pushHistory && screenId !== "loadingSection" && typeof window !== "undefined" && window.history && typeof window.history.pushState === "function") {
    if (!window.history.state || window.history.state.screen !== screenId) {
      try {
        window.history.pushState({ screen: screenId }, "");
      } catch (e) {
        // Ignore history errors if sandbox restricts pushState
      }
    }
  }
}

// ── Session State Persistence across Browser Refresh ─────────────────────────
function saveSessionState() {
  if (currentQuiz && currentQuiz.questions) {
    sessionStorage.setItem("learnai_active_quiz", JSON.stringify({
      currentQuiz,
      currentIndex,
      answers,
      activeCourseCode,
      activeTopicName,
      activeTopicId,
      activeNotesSessionId,
      activeStudyMaterialId
    }));
  }
}

function clearSessionState() {
  sessionStorage.removeItem("learnai_active_quiz");
  activeNotesSessionId = null;
}

function restoreSessionState() {
  try {
    const raw = sessionStorage.getItem("learnai_active_quiz");
    if (!raw) return false;
    const data = JSON.parse(raw);
    if (data && data.currentQuiz && Array.isArray(data.currentQuiz.questions)) {
      currentQuiz = data.currentQuiz;
      currentIndex = data.currentIndex || 0;
      answers = data.answers || Array(currentQuiz.questions.length).fill("");
      activeCourseCode = data.activeCourseCode || currentQuiz.course_code;
      activeTopicName = data.activeTopicName || currentQuiz.topic;
      activeTopicId = data.activeTopicId || currentQuiz.topic_id || "";
      activeNotesSessionId = data.activeNotesSessionId || null;
      activeStudyMaterialId = data.activeStudyMaterialId || currentQuiz.study_material_id || null;

      // Restore select dropdowns
      if ($("course") && activeCourseCode) {
        $("course").value = activeCourseCode;
        $("course").dispatchEvent(new Event("change"));
        setTimeout(() => {
          if ($("topic") && activeTopicName) {
            $("topic").value = activeTopicName;
          }
        }, 50);
      }

      // Restore quiz header cleanly
      renderQuizHeader(currentQuiz);

      renderQuestion();
      showScreen("quizSection");
      return true;
    }
  } catch (err) {
    clearSessionState();
  }
  return false;
}

// ── Load Curriculum & Course Select ──────────────────────────────────────────
async function loadCourses() {
  const courseSelect = $("course");
  try {
    const res = await fetch(apiUrl("/api/courses"));
    if (!res.ok) {
      throw new Error(`Failed to fetch curriculum: HTTP ${res.status}`);
    }
    const data = await res.json();
    curriculum = data.courses || [];

    if (!curriculum.length) {
      $("setupMsg").innerHTML = '<span class="error-msg">No courses available in curriculum.</span>';
      return;
    }

    courseSelect.innerHTML = '<option value="">Select a course</option>';
    curriculum.forEach(course => {
      const option = document.createElement("option");
      option.value = course.code;
      option.textContent = `${course.code} - ${course.name}`;
      courseSelect.appendChild(option);
    });
    $("setupMsg").innerHTML = "";
    populateDashboardCourseFilter();
  } catch (e) {
    console.error("Failed to load courses:", e);
    if (courseSelect) {
      courseSelect.innerHTML = '<option value="">Error loading courses</option>';
    }
    $("setupMsg").innerHTML = '<span class="error-msg">Unable to load courses. Please check if the server is running.</span>';
  }
}

$("course").addEventListener("change", () => {
  const course = curriculum.find(c => c.code === $("course").value);
  const topicSelect = $("topic");
  topicSelect.innerHTML = "";

  if (!course) {
    topicSelect.disabled = true;
    topicSelect.innerHTML = '<option value="">Select a course first</option>';
    topicSelect.value = "";
    return;
  }

  const first = document.createElement("option");
  first.value = "";
  first.textContent = "Select a topic";
  topicSelect.appendChild(first);

  const seen = new Set();
  (course.topics || []).forEach(topic => {
    if (!topic || !topic.name) return;
    const name = String(topic.name).trim();
    if (!name || seen.has(name.toLowerCase())) return;
    seen.add(name.toLowerCase());

    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    option.dataset.topicId = topic.id || "";
    option.dataset.topicName = name;
    option.dataset.parentTopic = topic.parent_topic || topic.parent_section || topic.source_topic_name || "";
    option.dataset.module = (topic.module !== undefined && topic.module !== null) ? topic.module : "";
    option.dataset.unit = (topic.unit !== undefined && topic.unit !== null) ? topic.unit : "";
    topicSelect.appendChild(option);
  });

  topicSelect.disabled = false;
  topicSelect.value = "";
});

// ── Navigation Buttons ───────────────────────────────────────────────────────
$("generateBtn").addEventListener("click", () => {
  const courseCode = $("course").value;
  const topicSelect = $("topic");
  const topic = topicSelect.value;
  const selectedOption = topicSelect.selectedOptions ? topicSelect.selectedOptions[0] : null;
  const topicId = selectedOption?.dataset?.topicId || "";

  if (!courseCode || !topic) {
    $("setupMsg").innerHTML = '<span class="error-msg">Please select both course and topic.</span>';
    return;
  }
  $("setupMsg").innerHTML = "";

  startQuizGeneration(courseCode, topic, false, topicId);
});

$("prevBtn").addEventListener("click", () => {
  if (currentIndex > 0) {
    currentIndex--;
    renderQuestion();
  }
});

$("nextBtn").addEventListener("click", async () => {
  if (!answers[currentIndex]) {
    const alertEl = $("quizAlert");
    if (alertEl) {
      alertEl.textContent = "Please choose an answer to proceed.";
      const optEl = $("options");
      if (optEl) {
        optEl.classList.remove("shake");
        void optEl.offsetWidth; // force DOM reflow
        optEl.classList.add("shake");
      }
    } else {
      alert("Please choose an answer.");
    }
    return;
  }

  if (currentIndex < currentQuiz.questions.length - 1) {
    currentIndex++;
    renderQuestion();
  } else {
    await submitQuiz();
  }
});

// ── Retake Flow: Clean State Transition (Results → Loading → New Quiz) ──────
$("retakeBtn").addEventListener("click", async () => {
  if (isGenerating) return;

  const retakeBtn = $("retakeBtn");
  // Prevent double-click duplicate attempts
  if (retakeBtn.dataset.processing === "true") return;
  retakeBtn.dataset.processing = "true";
  retakeBtn.disabled = true;
  retakeBtn.textContent = "Initiating retake...";

  // Use active course/topic
  const courseCode = activeCourseCode || (currentQuiz ? currentQuiz.course_code : $("course").value);
  const topic = activeTopicName || (currentQuiz ? currentQuiz.topic : $("topic").value);
  const topicId = activeTopicId || (currentQuiz ? currentQuiz.topic_id : null);

  if (!courseCode || !topic) {
    alert("Unable to detect course and topic for retake. Please choose from setup.");
    goToHome();
    retakeBtn.disabled = false;
    retakeBtn.textContent = "Retake with New AI Questions";
    retakeBtn.dataset.processing = "false";
    return;
  }

  // 1. Completely wipe quiz and result state
  currentQuiz = null;
  currentIndex = 0;
  answers = [];

  // 2. Clear all result view DOM elements so old marks can NEVER leak
  clearResultDisplay();

  // 3. Clear question view DOM elements
  if ($("options")) $("options").innerHTML = "";
  if ($("question")) $("question").textContent = "";
  if ($("concept")) $("concept").textContent = "";
  if ($("progressBar")) $("progressBar").style.width = "0%";
  if ($("progressPercent")) $("progressPercent").textContent = "0% completed";

  // 4. Launch fresh quiz generation for retake
  await startQuizGeneration(courseCode, topic, true, topicId, activeNotesSessionId, activeStudyMaterialId);

  retakeBtn.disabled = false;
  retakeBtn.textContent = "Retake with New AI Questions";
  retakeBtn.dataset.processing = "false";
});

// ── Explicit Home Navigation & Loading Screen Exit ──────────────────────────
function goToHome() {
  if (isGenerating) return;
  clearSessionState();
  showScreen("setup");
  $("setupMsg").textContent = "";
}

function returnFromLoading() {
  if (activeAbortController) {
    activeAbortController.abort();
    activeAbortController = null;
  }
  isGenerating = false;
  const generateBtn = $("generateBtn");
  const retakeBtn = $("retakeBtn");
  if (generateBtn) generateBtn.disabled = false;
  if (retakeBtn) {
    retakeBtn.disabled = false;
    retakeBtn.textContent = "Retake with New AI Questions";
    retakeBtn.dataset.processing = "false";
  }
  if (lastActiveScreen === "dashboardSection") {
    openDashboard();
  } else if (lastActiveScreen === "notesUploadSection") {
    showScreen("notesUploadSection");
  } else if (lastActiveScreen === "materialsSection") {
    openStudyMaterials();
  } else {
    goToHome();
  }
}

$("newTopicBtn").addEventListener("click", goToHome);
$("brandLogo")?.addEventListener("click", goToHome);
$("loadingBackBtn")?.addEventListener("click", returnFromLoading);
$("loadingCancelBtn")?.addEventListener("click", returnFromLoading);

// ── Clear Results Display ───────────────────────────────────────────────────
function clearResultDisplay() {
  if ($("scoreText")) $("scoreText").textContent = "";
  if ($("statusText")) $("statusText").textContent = "";
  if ($("percentageText")) $("percentageText").textContent = "";
  if ($("difficultyPerformance")) $("difficultyPerformance").innerHTML = "";
  if ($("conceptPerformance")) $("conceptPerformance").innerHTML = "";
  if ($("strongConcepts")) $("strongConcepts").innerHTML = "";
  if ($("weakConcepts")) $("weakConcepts").innerHTML = "";
  if ($("analysisSummary")) $("analysisSummary").textContent = "";
  if ($("analysisRevision")) $("analysisRevision").textContent = "";
  if ($("analysisRecommendation")) $("analysisRecommendation").textContent = "";
  if ($("review")) $("review").innerHTML = "";
  if ($("attemptHistorySection")) $("attemptHistorySection").classList.add("hidden");
  const histList = $("attemptHistoryList");
  if (histList) histList.innerHTML = "";
}

// ── Core Quiz Generation (First Attempt & Retake) ────────────────────────────
async function startQuizGeneration(courseCode, topic, isRetake = false, topicId = null, notesSessionId = null, studyMaterialId = null) {
  if (isGenerating) return;
  isGenerating = true;

  if (notesSessionId) {
    activeNotesSessionId = notesSessionId;
  } else if (!isRetake) {
    activeNotesSessionId = null;
  }
  if (studyMaterialId) {
    activeStudyMaterialId = studyMaterialId;
  } else if (!isRetake) {
    activeStudyMaterialId = null;
  }

  const generateBtn = $("generateBtn");
  const retakeBtn = $("retakeBtn");
  if (generateBtn) generateBtn.disabled = true;
  if (retakeBtn) retakeBtn.disabled = true;

  activeCourseCode = courseCode;
  activeTopicName = topic;
  activeTopicId = topicId || "";

  let timeoutId = null;
  const spinner = document.querySelector("#loadingSection .spinner");

  try {
    // Clear any previous setup message
    if ($("setupMsg")) $("setupMsg").innerHTML = "";

    // Sync select dropdowns
    if ($("course")) $("course").value = courseCode;
    if ($("topic") && $("topic").value !== topic) {
      $("course").dispatchEvent(new Event("change"));
      await new Promise(r => setTimeout(r, 40));
      if ($("topic")) $("topic").value = topic;
    }

    // ── Setup Loading Screen (Calm, Minimal, Academic) ──
    if (spinner) spinner.style.display = "block";
    if ($("loadingErrorBox")) $("loadingErrorBox").classList.add("hidden");

    // Populate course and topic label (small, secondary)
    const courseObj = curriculum.find(c => c.code === courseCode);
    const courseLabel = courseObj ? `${courseObj.code} - ${courseObj.name}` : courseCode;
    const notesTag = (activeNotesSessionId || activeStudyMaterialId) ? " [Notes-Grounded]" : "";
    if ($("loadingTopicLabel")) {
      $("loadingTopicLabel").textContent = `${courseLabel} • ${topic}${notesTag}`;
    }

    showScreen("loadingSection");

    activeAbortController = new AbortController();
    const timeoutSec = 50;
    timeoutId = setTimeout(() => activeAbortController?.abort(), timeoutSec * 1000);

    const quizPayload = {
      course_code: courseCode,
      topic: topic,
      topic_id: activeTopicId || undefined,
      student_id: getStudentId() || undefined,
      retake: isRetake
    };
    if (activeNotesSessionId) {
      quizPayload.notes_session_id = activeNotesSessionId;
    }
    if (activeStudyMaterialId) {
      quizPayload.study_material_id = activeStudyMaterialId;
    }

    const res = await authenticatedFetch("/api/quiz/generate", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(quizPayload),
      signal: activeAbortController.signal
    });
    if (timeoutId) clearTimeout(timeoutId);

    const data = await res.json();

    if (!res.ok) {
      const errMsg = (data && data.detail)
        ? (typeof data.detail === "object" ? data.detail.message : data.detail)
        : "AI service is temporarily busy. Please try again.";
      throw new Error(errMsg);
    }

    if (data.auth_token) {
      authToken = data.auth_token;
      localStorage.setItem("learnai_auth_token", authToken);
    }
    if (data.student_id) {
      studentId = data.student_id;
      localStorage.setItem("learnai_student_id", studentId);
    }

    // ── Validate questions before displaying them ──
    if (!data || !Array.isArray(data.questions) || data.questions.length === 0) {
      throw new Error("Received empty or malformed quiz from AI service. Please click Retry.");
    }

    const invalidQ = data.questions.find((q, idx) => {
      if (!q || typeof q.question !== "string" || !q.question.trim()) return true;
      if (!q.options || typeof q.options !== "object") return true;
      const opts = q.options;
      const required = ["A", "B", "C", "D"];
      const hasAllFour = required.every(k => typeof opts[k] === "string" && opts[k].trim().length > 0);
      if (!hasAllFour) return true;
      if (q.correct_answer !== undefined && !required.includes(q.correct_answer)) return true;
      return false;
    });

    if (invalidQ) {
      throw new Error("One or more questions contained incomplete or blank answer options. Please click Retry.");
    }

    // ── Success: Initialize Clean Quiz State ──
    currentQuiz = data;
    if (data.topic_id) activeTopicId = data.topic_id;
    if (data.study_material_id) activeStudyMaterialId = data.study_material_id;
    if (data.notes_session_id) activeNotesSessionId = data.notes_session_id;
    currentIndex = 0;
    answers = Array(data.questions.length).fill("");

    // Render directly to Question 1 with no intermediate flashes
    renderQuestion();
    showScreen("quizSection");
    saveSessionState();

  } catch (e) {
    if (timeoutId) clearTimeout(timeoutId);

    // Stay on loading section with clean, human-readable error with Retry & Cancel
    if (spinner) spinner.style.display = "none";

    let friendlyMsg = "AI service is temporarily busy. Please try again shortly.";
    if (e.name === "AbortError") {
      friendlyMsg = "Quiz generation was cancelled or timed out. Please try again.";
    } else if (e.message && typeof e.message === "string" && e.message.trim().length > 0 && !e.message.startsWith("{")) {
      friendlyMsg = e.message;
    }

    if ($("loadingErrorMsg")) $("loadingErrorMsg").textContent = friendlyMsg;
    if ($("loadingErrorBox")) $("loadingErrorBox").classList.remove("hidden");

    // Configure retry button
    const retryBtn = $("loadingRetryBtn");
    if (retryBtn) {
      retryBtn.onclick = () => {
        startQuizGeneration(courseCode, topic, isRetake, topicId, activeNotesSessionId, activeStudyMaterialId);
      };
    }

  } finally {
    isGenerating = false;
    if (generateBtn) generateBtn.disabled = false;
    if (retakeBtn) retakeBtn.disabled = false;
  }
}

// ── Dedicated Quiz Header Renderer (Clean, Calm, Academic) ──────────────────
function renderQuizHeader(quizData) {
  if (!quizData) return;

  const courseObj = curriculum.find(c => c.code === quizData.course_code);
  const courseName = courseObj ? `${courseObj.code} - ${courseObj.name}` : (quizData.course_code || quizData.subject || "");

  if ($("quizCourse")) {
    const modTag = (quizData.module !== undefined && quizData.unit !== undefined)
      ? ` • Module ${quizData.module}, Unit ${quizData.unit}`
      : "";
    $("quizCourse").textContent = `${courseName}${modTag}`;
  }
  if ($("quizTitle")) {
    $("quizTitle").textContent = quizData.topic;
  }
  if ($("quizAttemptNumber")) {
    const attempt = quizData.attempt_number || 1;
    const diff = (quizData.recommended_difficulty || "mixed").toUpperCase();
    $("quizAttemptNumber").textContent = attempt > 1 ? `Attempt #${attempt} • ${diff}` : `Attempt #1 • ${diff}`;
  }
}

// ── Render Question ─────────────────────────────────────────────────────────
function renderQuestion() {
  if (!currentQuiz || !currentQuiz.questions || !currentQuiz.questions[currentIndex]) {
    return;
  }

  const q = currentQuiz.questions[currentIndex];
  const totalQuestions = currentQuiz.questions.length;
  const progressPercent = Math.round(((currentIndex + 1) / totalQuestions) * 100);

  // Sync header and progress
  renderQuizHeader(currentQuiz);
  if ($("progressText")) $("progressText").textContent = `Question ${currentIndex + 1} of ${totalQuestions}`;
  if ($("difficultyBadge")) $("difficultyBadge").textContent = q.difficulty || "Standard";
  if ($("concept")) $("concept").textContent = q.concept ? `Concept: ${q.concept}` : "";
  if ($("question")) {
    LearnAICodeRenderer.renderToElement(q.question, $("question"), {
      structuredSegments: q.question_segments,
      code: q.code,
      language: q.language
    });
  }
  if ($("progressBar")) {
    $("progressBar").style.width = `${progressPercent}%`;
    const progressEl = $("progressBar").parentElement;
    if (progressEl) progressEl.setAttribute("aria-valuenow", progressPercent);
  }
  if ($("progressPercent")) $("progressPercent").textContent = `${progressPercent}% completed`;

  if ($("quizAlert")) $("quizAlert").textContent = "";

  const options = $("options");
  if (!options) return;
  options.innerHTML = "";

  Object.entries(q.options || {}).forEach(([key, value]) => {
    const isSelected = answers[currentIndex] === key;
    const div = document.createElement("div");
    div.className = "option" + (isSelected ? " selected" : "");
    div.setAttribute("role", "radio");
    div.setAttribute("aria-checked", isSelected ? "true" : "false");
    div.setAttribute("tabindex", "0");

    const radioCircle = document.createElement("span");
    radioCircle.className = "radio-circle";
    radioCircle.setAttribute("aria-hidden", "true");
    const radioDot = document.createElement("span");
    radioDot.className = "radio-dot";
    radioCircle.appendChild(radioDot);

    const letterSpan = document.createElement("span");
    letterSpan.className = "option-letter";
    letterSpan.textContent = `${key}.`;

    const textSpan = document.createElement("span");
    textSpan.className = "option-text";
    LearnAICodeRenderer.renderOptionToElement(value, textSpan);

    div.appendChild(radioCircle);
    div.appendChild(letterSpan);
    div.appendChild(textSpan);

    const selectOption = () => {
      answers[currentIndex] = key;
      if ($("quizAlert")) $("quizAlert").textContent = "";
      saveSessionState();
      renderQuestion();
    };

    div.addEventListener("click", selectOption);
    div.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        selectOption();
      }
    });

    options.appendChild(div);
  });

  if ($("prevBtn")) $("prevBtn").style.visibility = currentIndex === 0 ? "hidden" : "visible";
  if ($("nextBtn")) {
    const isLast = currentIndex === totalQuestions - 1;
    $("nextBtn").textContent = isLast ? "Submit Quiz" : "Next";
    $("nextBtn").setAttribute("aria-label",
      isLast ? "Submit quiz" : "Next question");
  }
}

// ── Code Snippet Copy-to-Clipboard Handler ──────────────────────────────────
if (typeof document !== "undefined" && document.addEventListener) {
  document.addEventListener("click", (e) => {
    const copyBtn = e.target.closest ? e.target.closest(".code-copy-btn") : null;
    if (!copyBtn) return;

    const container = copyBtn.closest(".code-block-container");
    if (!container) return;
    const codeEl = container.querySelector(".code-content");
    const codeText = codeEl ? codeEl.textContent : (copyBtn.getAttribute("data-raw-code") || "");
    if (!codeText) return;

    const btnText = copyBtn.querySelector(".copy-btn-text");
    const setCopied = () => {
      copyBtn.classList.add("copied");
      if (btnText) btnText.textContent = "Copied!";
      copyBtn.setAttribute("aria-label", "Copied to clipboard");
      setTimeout(() => {
        copyBtn.classList.remove("copied");
        if (btnText) btnText.textContent = "Copy";
        copyBtn.setAttribute("aria-label", "Copy code to clipboard");
      }, 2000);
    };

    if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(codeText).then(setCopied).catch(fallback);
    } else {
      fallback();
    }

    function fallback() {
      try {
        const ta = document.createElement("textarea");
        ta.value = codeText;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        ta.style.top = "-9999px";
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        const ok = document.execCommand("copy");
        document.body.removeChild(ta);
        if (ok) setCopied();
      } catch (err) {
        console.warn("Could not copy code snippet:", err);
      }
    }
  });
}

// ── Keyboard Navigation (1-4, A-D, Left/Right Arrows) ───────────────────────
document.addEventListener("keydown", (e) => {
  if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || e.target.tagName === "TEXTAREA")) return;
  const quizSection = $("quizSection");
  if (!quizSection || quizSection.classList.contains("hidden")) return;
  if (!currentQuiz || !currentQuiz.questions || !currentQuiz.questions[currentIndex]) return;

  const key = e.key.toUpperCase();
  const keyMap = { "1": "A", "2": "B", "3": "C", "4": "D", "A": "A", "B": "B", "C": "C", "D": "D" };
  if (keyMap[key]) {
    const optKey = keyMap[key];
    const q = currentQuiz.questions[currentIndex];
    if (q.options && q.options[optKey]) {
      e.preventDefault();
      answers[currentIndex] = optKey;
      if ($("quizAlert")) $("quizAlert").textContent = "";
      saveSessionState();
      renderQuestion();
    }
  } else if (e.key === "ArrowLeft" && currentIndex > 0) {
    e.preventDefault();
    currentIndex--;
    renderQuestion();
  } else if (e.key === "ArrowRight") {
    e.preventDefault();
    $("nextBtn").click();
  }
});

// ── Submit Quiz ─────────────────────────────────────────────────────────────
async function submitQuiz() {
  const nextBtn = $("nextBtn");
  nextBtn.disabled = true;
  nextBtn.textContent = "Analyzing answers with AI...";

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 45000);

  try {
    const res = await authenticatedFetch("/api/quiz/submit", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        quiz_id: currentQuiz.quiz_id,
        answers: answers,
        student_id: getStudentId(),
        auth_token: getAuthToken()
      }),
      signal: controller.signal
    });
    clearTimeout(timeoutId);

    const data = await res.json();

    if (!res.ok) {
      if (res.status === 404 || (res.status === 400 && typeof data.detail === "string" && data.detail.toLowerCase().includes("expired"))) {
        clearSessionState();
      }
      throw new Error(data.detail || "Failed to submit quiz.");
    }

    lastPracticedStudyMaterialId = activeStudyMaterialId || currentQuiz.study_material_id || null;
    clearSessionState();
    studyMaterialsCacheTime = 0;
    materialTopicsCache.clear();
    showResult(data);
  } catch (e) {
    clearTimeout(timeoutId);
    let msg = "Failed to submit quiz. Please try again.";
    if (e.name === "AbortError") {
      msg = "Analysis timed out. Please click Submit Quiz again.";
    } else if (e.message) {
      msg = e.message;
    }
    alert(msg);
  } finally {
    nextBtn.disabled = false;
    nextBtn.textContent = "Submit Quiz";
  }
}

// ── Show Results (Isolated to Result Screen) ────────────────────────────────
function showResult(data) {
  // Clear any existing review elements
  $("review").innerHTML = "";

  $("scoreText").textContent = `${data.score} / ${data.total}`;
  $("statusText").textContent = data.status;
  $("percentageText").textContent = `${data.percentage}% accuracy`;

  // Display difficulty-wise performance
  if (data.difficulty_accuracy) {
    let diffHtml = "";
    for (const [diff, stats] of Object.entries(data.difficulty_accuracy)) {
      const acc = stats.accuracy;
      let scoreClass = "mixed";
      if (acc >= 80) scoreClass = "correct";
      else if (acc < 50) scoreClass = "wrong";

      diffHtml += `
        <div class="difficulty-item">
          <span class="difficulty-label">${diff}</span>
          <span class="difficulty-score ${scoreClass}">${acc}%</span>
          <span class="difficulty-detail">(${stats.correct}/${stats.total})</span>
        </div>`;
    }
    $("difficultyPerformance").innerHTML = diffHtml || "<span class='muted'>No data</span>";
  }

  // Display concept-wise performance
  if (data.concept_accuracy) {
    let conceptHtml = "";
    for (const [concept, stats] of Object.entries(data.concept_accuracy)) {
      const acc = stats.accuracy;
      let scoreClass = "mixed";
      if (acc >= 80) scoreClass = "correct";
      else if (acc < 50) scoreClass = "wrong";

      conceptHtml += `
        <div class="concept-item">
          <span class="concept-label">${concept}</span>
          <span class="concept-score ${scoreClass}">${acc}%</span>
          <span class="concept-detail">(${stats.correct}/${stats.total})</span>
        </div>`;
    }
    $("conceptPerformance").innerHTML = conceptHtml || "<span class='muted'>No data</span>";
  }

  $("strongConcepts").innerHTML = (data.strong_concepts && data.strong_concepts.length)
    ? data.strong_concepts.map(x => `<span class="tag-chip">${x}</span>`).join("")
    : "<span class='muted'>No strong concept identified yet.</span>";

  $("weakConcepts").innerHTML = (data.weak_concepts && data.weak_concepts.length)
    ? data.weak_concepts.map(x => `<span class="tag-chip">${x}</span>`).join("")
    : "<span class='muted'>No major weak concept detected.</span>";

  const analysis = (typeof data.analysis === "object" && data.analysis !== null)
    ? data.analysis
    : {
        summary: typeof data.analysis === "string" ? data.analysis : "AI feedback is temporarily unavailable.",
        revision: "Review the question explanations below to revise your concepts.",
        recommendation: "Review your weak concepts and try another quiz once the service is available."
      };

  $("analysisSummary").textContent = analysis.summary || "";
  $("analysisRevision").textContent = analysis.revision || "";
  $("analysisRecommendation").textContent = analysis.recommendation || "";

  $("review").innerHTML =
    "<h3>Answer Review</h3>" +
    (data.details || []).map((d, i) => `
      <div class="review-item">
        <div class="review-question-title"><strong>Q${i + 1}.</strong></div>
        <div class="review-question-content">${LearnAICodeRenderer.renderToHtml(d.question)}</div>
        <div class="${d.is_correct ? "correct" : "wrong"}">
          ${d.is_correct ? "Correct" : "Incorrect"}
        </div>
        <div class="review-answer-line">Your answer: ${LearnAICodeRenderer.renderOptionToHtml(d.selected_answer || "Not answered")}</div>
        <div class="review-answer-line">Correct answer: ${LearnAICodeRenderer.renderOptionToHtml(d.correct_answer)}</div>
        <div class="muted review-explanation">${LearnAICodeRenderer.renderToHtml(d.explanation)}</div>
      </div>
    `).join("");

  // Load and display attempt history for this topic
  loadAttemptHistory();

  // Show ONLY resultSection
  showScreen("resultSection");
  window.scrollTo({top: 0, behavior: "smooth"});
}

// ── Fetch & Render Attempt History ──────────────────────────────────────────
async function loadAttemptHistory() {
  try {
    const sId = getStudentId();
    if (!sId) return;
    const res = await authenticatedFetch(`/api/student/${sId}/history?limit=10`);
    if (!res.ok) return;

    const data = await res.json();
    const attempts = data.attempts || [];

    // Filter attempts for this topic
    const topicAttempts = attempts.filter(a =>
      a.course_code === activeCourseCode && a.topic_name === activeTopicName
    );

    if (topicAttempts.length <= 1) {
      $("attemptHistorySection")?.classList.add("hidden");
      return;
    }

    let tableHtml = `
      <table class="history-table">
        <thead>
          <tr>
            <th>Attempt</th>
            <th>Date & Time</th>
            <th>Score</th>
            <th>Accuracy</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
    `;

    topicAttempts.forEach((att, idx) => {
      const attemptNum = topicAttempts.length - idx;
      const dateStr = new Date(att.created_at).toLocaleString([], {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
      });
      const isCurrent = idx === 0;
      const rowClass = isCurrent ? "history-row-current" : "";

      tableHtml += `
        <tr class="${rowClass}">
          <td>${isCurrent ? 'Current (#' + attemptNum + ')' : 'Attempt #' + attemptNum}</td>
          <td>${dateStr}</td>
          <td>${att.score} / ${att.total}</td>
          <td>${att.percentage}%</td>
          <td>${att.status}</td>
        </tr>
      `;
    });

    tableHtml += `</tbody></table>`;
    $("attemptHistoryList").innerHTML = tableHtml;
    $("attemptHistorySection").classList.remove("hidden");

  } catch (err) {
    // Non-critical, ignore if history fails to load
  }
}

// ── Student Progress Dashboard Logic ─────────────────────────────────────────

async function openDashboard() {
  if (isGenerating) return;
  showScreen("dashboardSection");
  window.scrollTo({ top: 0, behavior: "smooth" });

  populateDashboardCourseFilter();
  await loadDashboard();
}

function populateDashboardCourseFilter(data) {
  const filterSelect = $("dashboardCourseFilter");
  if (!filterSelect) return;

  const currentVal = filterSelect.value || "";
  const existingValues = new Set(Array.from(filterSelect.options || []).map(o => o.value));

  // Initialize with curriculum courses if only default 'All Courses' exists
  if (existingValues.size <= 1 && Array.isArray(curriculum) && curriculum.length > 0) {
    filterSelect.innerHTML = '<option value="">All Courses</option>';
    curriculum.forEach(course => {
      const opt = document.createElement("option");
      opt.value = course.code;
      opt.textContent = `${course.code} - ${course.name}`;
      filterSelect.appendChild(opt);
      existingValues.add(course.code);
    });
  }

  // Also include any courses present in student data (topic_progress or recent_trends)
  if (data) {
    const dataCourses = new Map();
    (data.topic_progress || []).forEach(tp => {
      if (tp.course_code && !existingValues.has(tp.course_code)) {
        dataCourses.set(tp.course_code, tp.course_code);
      }
    });
    (data.recent_trends || []).forEach(rt => {
      if (rt.course_code && !existingValues.has(rt.course_code)) {
        const label = rt.course_name ? `${rt.course_code} - ${rt.course_name}` : rt.course_code;
        dataCourses.set(rt.course_code, label);
      }
    });
    dataCourses.forEach((label, code) => {
      const opt = document.createElement("option");
      opt.value = code;
      opt.textContent = label;
      filterSelect.appendChild(opt);
      existingValues.add(code);
    });
  }

  if (currentVal && existingValues.has(currentVal)) {
    filterSelect.value = currentVal;
  }
}

let currentDashboardRequestId = 0;

function onDashboardCourseFilterChange() {
  const filterVal = $("dashboardCourseFilter")?.value || "";
  const graphCard = $("performanceGraphCard");
  const trendsWrap = $("trendChartWrap");
  const countTag = $("trendsCountTag");
  const subtitleEl = $("performanceGraphSubtitle");

  // Immediate UI update upon user action:
  // If switching to 'All Courses', immediately hide the graph card and clear stale chart content.
  // If switching to a specific course, immediately clear previous chart content and metadata so it cannot linger while loading.
  if (!filterVal) {
    if (graphCard) {
      graphCard.classList.add("hidden");
      graphCard.setAttribute("aria-hidden", "true");
    }
    if (trendsWrap) {
      trendsWrap.innerHTML = "";
    }
    if (countTag) {
      countTag.textContent = "";
    }
  } else {
    if (trendsWrap) {
      trendsWrap.innerHTML = "";
    }
    if (countTag) {
      countTag.textContent = "";
    }
    if (subtitleEl) {
      subtitleEl.textContent = `Historical score progression for ${filterVal}.`;
    }
  }

  loadDashboard();
}

async function loadDashboard() {
  const requestId = ++currentDashboardRequestId;
  const loadingEl = $("dashboardLoading");
  const errorEl = $("dashboardError");
  const emptyEl = $("dashboardEmptyState");
  const contentEl = $("dashboardContent");
  const graphCard = $("performanceGraphCard");
  const trendsWrap = $("trendChartWrap");
  const countTag = $("trendsCountTag");
  const subtitleEl = $("performanceGraphSubtitle");

  const courseFilter = $("dashboardCourseFilter")?.value || "";

  // Ensure graph is immediately hidden if 'All Courses' is selected, or clear stale chart if specific course
  if (!courseFilter) {
    if (graphCard) {
      graphCard.classList.add("hidden");
      graphCard.setAttribute("aria-hidden", "true");
    }
    if (trendsWrap) {
      trendsWrap.innerHTML = "";
    }
    if (countTag) {
      countTag.textContent = "";
    }
  } else {
    if (trendsWrap) {
      trendsWrap.innerHTML = "";
    }
    if (countTag) {
      countTag.textContent = "";
    }
    if (subtitleEl) {
      subtitleEl.textContent = `Historical score progression for ${courseFilter}.`;
    }
  }

  if (loadingEl) loadingEl.classList.remove("hidden");
  if (errorEl) errorEl.classList.add("hidden");
  if (emptyEl) emptyEl.classList.add("hidden");
  if (contentEl) contentEl.classList.add("hidden");

  try {
    await ensureAuthSession();
    const sId = getStudentId();
    if (!sId) {
      throw new Error("Unable to establish authenticated student session.");
    }
    let url = `/api/student/${encodeURIComponent(sId)}/dashboard?limit=20`;
    if (courseFilter) {
      url += `&course_code=${encodeURIComponent(courseFilter)}`;
    }

    const res = await authenticatedFetch(url);
    if (!res.ok) {
      throw new Error(`Failed to load dashboard: HTTP ${res.status}`);
    }

    const data = await res.json();
    // Guard against race conditions if rapid course changes occurred
    if (requestId !== currentDashboardRequestId) {
      return;
    }
    renderDashboard(data);
  } catch (err) {
    console.error("Dashboard load error:", err);
    // On error, guarantee stale graph data is removed and graph card is hidden
    if (graphCard) {
      graphCard.classList.add("hidden");
      graphCard.setAttribute("aria-hidden", "true");
    }
    if (trendsWrap) {
      trendsWrap.innerHTML = "";
    }
    const countTag = $("trendsCountTag");
    if (countTag) {
      countTag.textContent = "";
    }
    if (loadingEl) loadingEl.classList.add("hidden");
    if (errorEl) {
      errorEl.classList.remove("hidden");
      const msg = $("dashboardErrorMsg");
      if (msg) msg.textContent = "Unable to load dashboard data. Please try again.";
    }
  }
}

function renderDashboard(data) {
  const loadingEl = $("dashboardLoading");
  const emptyEl = $("dashboardEmptyState");
  const contentEl = $("dashboardContent");

  if (loadingEl) loadingEl.classList.add("hidden");

  // Ensure course filter options include all relevant courses
  populateDashboardCourseFilter(data);

  const overview = data.overview || {};
  const completed = overview.completed_quizzes || 0;
  const overallCompleted = (data.overall_completed_quizzes !== undefined)
    ? Number(data.overall_completed_quizzes)
    : null;

  // hasCompleted represents whether the student has completed any quizzes overall
  const hasCompleted = (data.has_completed_quizzes !== undefined)
    ? Boolean(data.has_completed_quizzes)
    : (overallCompleted !== null ? overallCompleted > 0 : completed > 0);

  const filterEl = $("dashboardCourseFilter");
  const selectedCourse = filterEl ? (filterEl.value || "").trim() : "";
  const courseFilterActive = Boolean(selectedCourse || (data.course_code || "").trim());

  // First-time student screen is shown ONLY when the student has never completed ANY quiz
  // and no course filter is currently active. When filtering to a specific course that has
  // 0 attempts, show the course dashboard with its dedicated empty state.
  if (!hasCompleted && !courseFilterActive) {
    if (contentEl) contentEl.classList.add("hidden");
    if (emptyEl) emptyEl.classList.remove("hidden");
    return;
  }

  if (emptyEl) emptyEl.classList.add("hidden");
  if (contentEl) contentEl.classList.remove("hidden");

  renderReturningDashboard(data);
}

function getCourseDisplayName(courseCode, data = null, fallbackName = "") {
  if (fallbackName && typeof fallbackName === "string" && fallbackName.trim() && fallbackName.trim().toLowerCase() !== (courseCode || "").trim().toLowerCase()) {
    return fallbackName.trim();
  }
  const cleanCode = (courseCode || "").trim().toLowerCase();
  if (!cleanCode) return "Course Practice";

  // 1. Check global curriculum array loaded by loadCourses()
  if (Array.isArray(curriculum) && curriculum.length > 0) {
    const found = curriculum.find(c => (c.code || "").trim().toLowerCase() === cleanCode);
    if (found && found.name && found.name.trim()) {
      return found.name.trim();
    }
  }

  // 2. Check data.recent_trends if available in dashboard data
  if (data && Array.isArray(data.recent_trends)) {
    const trendMatch = data.recent_trends.find(t => (t.course_code || "").trim().toLowerCase() === cleanCode);
    if (trendMatch && trendMatch.course_name && trendMatch.course_name.trim()) {
      return trendMatch.course_name.trim();
    }
  }

  // 3. Check dropdown options in #dashboardCourseFilter or #course
  const selectIds = ["dashboardCourseFilter", "course"];
  for (const id of selectIds) {
    const sel = document.getElementById(id);
    if (!sel || !sel.options) continue;
    for (const opt of sel.options) {
      if ((opt.value || "").trim().toLowerCase() === cleanCode) {
        const text = (opt.textContent || "").trim();
        if (text.includes(" - ")) {
          const parts = text.split(" - ");
          if (parts.length > 1 && parts.slice(1).join(" - ").trim()) {
            return parts.slice(1).join(" - ").trim();
          }
        } else if (text && text.toLowerCase() !== cleanCode) {
          return text;
        }
      }
    }
  }

  // 4. Fallback lookup map for common course codes
  const KNOWN_COURSES = {
    "22am201": "Artificial Intelligence",
    "22tp103": "Programming In C",
    "22cs104": "Python Programming",
    "22cs101": "Programming In C",
    "22am301": "Deep Learning",
    "22am204": "Machine Learning",
    "22cs201": "Database Management Systems",
    "22cs207": "Operating Systems",
    "22cs204": "Computer Networks"
  };
  if (KNOWN_COURSES[cleanCode]) {
    return KNOWN_COURSES[cleanCode];
  }

  // 5. Default fallback: Never display raw alphanumeric course code like '22AM201'
  return "Course Practice";
}

function renderReturningDashboard(data) {
  const overview = data.overview || {};

  // 1. Overview Metrics
  if ($("statCompletedQuizzes")) $("statCompletedQuizzes").textContent = overview.completed_quizzes || 0;
  if ($("statTotalQuestions")) $("statTotalQuestions").textContent = overview.total_questions || 0;
  if ($("statTotalCorrectSub")) $("statTotalCorrectSub").textContent = `${overview.total_correct || 0} correct`;
  if ($("statOverallAccuracy")) $("statOverallAccuracy").textContent = `${overview.overall_accuracy || 0}%`;
  if ($("statTopicsPracticed")) $("statTopicsPracticed").textContent = overview.topics_practiced || 0;
  if ($("statLastPracticed")) {
    if (overview.last_practiced_at) {
      const d = new Date(overview.last_practiced_at);
      $("statLastPracticed").textContent = d.toLocaleDateString([], { month: "short", day: "numeric" });
    } else {
      $("statLastPracticed").textContent = "Never";
    }
  }

  // 2. Recommended Review (Topics Needing Review)
  const reviewListEl = $("reviewTopicsList");
  const reviewTopics = data.review_topics || data.recommended_topics || [];

  if (reviewListEl) {
    if (!reviewTopics.length) {
      reviewListEl.innerHTML = `
        <div class="on-track-card">
          <div class="on-track-icon" aria-hidden="true">&#x2713;</div>
          <div>
            <strong>All Practiced Topics on Track</strong>
            <p class="muted">You have maintained solid performance across practiced topics. Choose a topic from Practice Quiz to begin a new session.</p>
          </div>
        </div>
      `;
    } else {
      reviewListEl.innerHTML = reviewTopics.map((r, idx) => {
        const isHigh = r.priority === "high";
        const cardId = `rec-card-${idx}`;

        const courseName = getCourseDisplayName(r.course_code, data, r.course_name);
        const topicName = (r.topic_name || "").trim();
        const attemptsCount = (typeof r.attempt_count === "number" && r.attempt_count > 0) ? r.attempt_count : 1;
        const attemptsText = attemptsCount > 1 ? `${attemptsCount} attempts` : "1 attempt";

        const latestScore = (r.latest_score !== undefined && r.latest_score !== null) ? r.latest_score : null;
        const latestTotal = (r.latest_total !== undefined && r.latest_total !== null) ? r.latest_total : null;
        const latestAcc = (r.latest_accuracy !== undefined && r.latest_accuracy !== null)
          ? r.latest_accuracy
          : (r.recent_accuracy || 0);

        const hasComparison = Boolean(r.attempt_count > 1 && r.score_change !== null && r.score_change !== undefined);
        let deltaHtml = "";
        if (hasComparison) {
          const changeVal = r.score_change;
          const prevAcc = (r.previous_accuracy !== undefined && r.previous_accuracy !== null) ? r.previous_accuracy : "--";
          if (r.change_type === "improved" || changeVal > 0) {
            deltaHtml = `
              <span class="rec-delta rec-delta-improved" title="Improved by ${changeVal}% compared to previous attempt (${prevAcc}%)">
                <span class="delta-arrow" aria-hidden="true">&uarr;</span> +${changeVal}% vs prev (${prevAcc}%)
              </span>
            `;
          } else if (r.change_type === "declined" || changeVal < 0) {
            deltaHtml = `
              <span class="rec-delta rec-delta-declined" title="Declined by ${Math.abs(changeVal)}% compared to previous attempt (${prevAcc}%)">
                <span class="delta-arrow" aria-hidden="true">&darr;</span> ${changeVal}% vs prev (${prevAcc}%)
              </span>
            `;
          } else {
            deltaHtml = `
              <span class="rec-delta rec-delta-neutral" title="Score unchanged compared to previous attempt">
                No change (${prevAcc}%)
              </span>
            `;
          }
        } else if (r.attempt_count === 1) {
          deltaHtml = `<span class="rec-delta rec-delta-single">First attempt</span>`;
        }



        const historyItems = (Array.isArray(r.history) && r.history.length > 1) ? r.history : [];
        const hasHistory = historyItems.length > 1;

        let historyHtml = "";
        if (hasHistory) {
          const rows = historyItems.map((h, hIdx) => {
            const isLatest = hIdx === 0;
            const dateStr = h.created_at ? new Date(h.created_at).toLocaleDateString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "--";
            const rowClass = isLatest ? "history-row-current" : "";
            const attemptLabel = isLatest ? "Latest" : `#${historyItems.length - hIdx}`;
            return `
              <tr class="${rowClass}">
                <td>${isLatest ? `<strong>${attemptLabel}</strong>` : attemptLabel}</td>
                <td><strong>${h.score}/${h.total}</strong></td>
                <td>${h.accuracy}%</td>
                <td><span class="level-chip">${escapeHtml(h.recommended_difficulty || "mixed")}</span></td>
                <td>${dateStr}</td>
              </tr>
            `;
          }).join("");

          historyHtml = `
            <div id="history-panel-${cardId}" class="topic-history-panel hidden" aria-hidden="true">
              <div class="topic-history-header">Attempt History</div>
              <div class="table-container">
                <table class="history-table" aria-label="Attempt history for ${escapeHtml(r.topic_name)}">
                  <thead>
                    <tr>
                      <th scope="col">Attempt</th>
                      <th scope="col">Score</th>
                      <th scope="col">Accuracy</th>
                      <th scope="col">Level</th>
                      <th scope="col">Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${rows}
                  </tbody>
                </table>
              </div>
            </div>
          `;
        }

        const topicContentHtml = topicName
          ? `<strong class="rec-topic-name">${escapeHtml(topicName)}</strong>`
          : `<span class="rec-topic-name rec-topic-muted">Topic unavailable</span>`;

        return `
          <div class="rec-card ${isHigh ? 'card-review-high' : 'card-review-adv'}" id="${cardId}">
            <div class="rec-card-body">
              <div class="rec-card-header">
                <h3 class="rec-course-name rec-title">${escapeHtml(courseName)}</h3>
                <div class="rec-topic-row">
                  <span class="rec-topic-label">Topic:</span>
                  ${topicContentHtml}
                </div>
                <div class="rec-attempts-wrap">
                  <span class="rec-attempts-pill">${escapeHtml(attemptsText)}</span>
                </div>
              </div>

              <!-- Primary Score & Progress Section -->
              <div class="rec-score-box">
                <div class="rec-score-header">
                  <div class="rec-score-primary">
                    <span class="rec-score-pct">${latestAcc}%</span>
                    ${latestScore !== null && latestTotal !== null ? `<span class="rec-score-fraction">(${latestScore}/${latestTotal})</span>` : ""}
                  </div>
                  ${deltaHtml}
                </div>
                <div class="rec-progress-track" role="progressbar" aria-valuenow="${latestAcc}" aria-valuemin="0" aria-valuemax="100" aria-label="Latest score: ${latestAcc}%">
                  <div class="rec-progress-fill ${isHigh ? 'fill-warning' : 'fill-brand'}" style="width: ${Math.min(100, Math.max(0, latestAcc))}%"></div>
                </div>
              </div>

              <p class="rec-reason">${escapeHtml(r.reason)}</p>
            </div>

            <div class="rec-card-footer">
              <div class="rec-actions">
                <button type="button" class="practice-topic-btn ${isHigh ? 'btn-primary' : 'secondary'}" data-course="${escapeHtml(r.course_code)}" data-topic="${escapeHtml(r.topic_name)}">
                  Practice Topic
                </button>
                ${hasHistory ? `
                  <button type="button" class="secondary topic-history-toggle-btn" aria-expanded="false" aria-controls="history-panel-${cardId}">
                    View history
                  </button>
                ` : ""}
              </div>
              ${historyHtml}
            </div>
          </div>
        `;
      }).join("");
    }
  }

  // Backward compatibility alias for any caller expecting recommendedTopicsList
  const recListEl = $("recommendedTopicsList");
  if (recListEl && reviewListEl) {
    recListEl.innerHTML = reviewListEl.innerHTML;
  }

  // 4. Performance Trends (Conditional Course-Specific Graph + Attempts List)
  const graphCard = $("performanceGraphCard");
  const trendsWrap = $("trendChartWrap");
  const tableWrap = $("recentAttemptsTableWrap");
  const countTag = $("trendsCountTag");
  const subtitleEl = $("performanceGraphSubtitle");

  // Determine current active course filter:
  // If the course filter element is in the DOM, its value is the user's explicit selection.
  // When 'All Courses' is selected, activeCourse is empty string.
  const filterEl = $("dashboardCourseFilter");
  const activeCourse = filterEl ? (filterEl.value || "").trim() : ((data.course_code || "").trim());

  if (!activeCourse) {
    // Rule 1 & 3: When 'All Courses' is selected, hide the performance graph completely
    if (graphCard) {
      graphCard.classList.add("hidden");
      graphCard.setAttribute("aria-hidden", "true");
    }
    if (trendsWrap) {
      trendsWrap.innerHTML = "";
    }
    if (countTag) {
      countTag.textContent = "";
    }
  } else {
    // Rule 2: When a specific course is selected, display the performance graph for that course only
    if (graphCard) {
      graphCard.classList.remove("hidden");
      graphCard.setAttribute("aria-hidden", "false");
    }

    if (subtitleEl) {
      subtitleEl.textContent = `Historical score progression for ${activeCourse}.`;
    }

    const rawTrends = data.recent_trends || [];
    // Ensure the graph uses data belonging exclusively to the selected course (case/whitespace resilient)
    const normActive = activeCourse.toLowerCase();
    const courseTrends = rawTrends.filter(t => (t.course_code || "").trim().toLowerCase() === normActive);

    if (trendsWrap) {
      if (!courseTrends.length) {
        if (countTag) countTag.textContent = "0 attempts";
        trendsWrap.innerHTML = `
          <div class="trend-empty-state" role="status">
            <p class="muted">No quiz attempts recorded yet for <strong>${escapeHtml(activeCourse)}</strong>. Complete a quiz to view performance trends.</p>
          </div>
        `;
      } else {
        if (countTag) {
          countTag.textContent = `${courseTrends.length} attempt${courseTrends.length > 1 ? "s" : ""}`;
        }
        const chronological = [...courseTrends].reverse();
        let chartHtml = `
          <div class="trend-chart-wrapper">
            <div class="trend-y-axis" aria-hidden="true">
              <span class="trend-y-tick">100%</span>
              <span class="trend-y-tick">50%</span>
              <span class="trend-y-tick">0%</span>
            </div>
            <div class="trend-plot-area">
              <div class="trend-grid" aria-hidden="true">
                <div class="trend-grid-line line-100"></div>
                <div class="trend-grid-line line-50"></div>
                <div class="trend-grid-line line-0"></div>
              </div>
              <div class="trend-chart" role="region" aria-label="Recent quiz performance chart for ${escapeHtml(activeCourse)}">
        `;

        chronological.forEach((t, i) => {
          const rawScore = Number(t.percentage) || 0;
          const pct = Math.max(0, Math.min(100, Math.round(rawScore)));
          const maxBarHeight = 64;
          const barHeight = Math.max(6, Math.min(maxBarHeight, Math.round((pct / 100) * maxBarHeight)));
          const barClass = t.percentage >= 70 ? "high" : (t.percentage >= 50 ? "medium" : "low");

          // Calculate score change from previous consecutive attempt
          let deltaHtml = "";
          let deltaAria = "";
          let tooltipDeltaHtml = "";

          if (i === 0) {
            deltaHtml = `<span class="trend-delta-badge initial" aria-hidden="true">1st</span>`;
            deltaAria = "Initial attempt for this course.";
            tooltipDeltaHtml = `<div class="tooltip-detail"><span class="tooltip-delta neu">First recorded attempt</span></div>`;
          } else {
            const prevScore = Number(chronological[i - 1].percentage) || 0;
            const diff = Math.round((pct - prevScore) * 10) / 10;
            if (diff > 0) {
              deltaHtml = `<span class="trend-delta-badge positive" aria-hidden="true">↑ ${diff} pp</span>`;
              deltaAria = `Score change: +${diff} percentage points compared to attempt #${i}.`;
              tooltipDeltaHtml = `<div class="tooltip-detail">Score change: <span class="tooltip-delta pos">+${diff} percentage points vs #${i}</span></div>`;
            } else if (diff < 0) {
              const absDiff = Math.abs(diff);
              deltaHtml = `<span class="trend-delta-badge negative" aria-hidden="true">↓ ${absDiff} pp</span>`;
              deltaAria = `Score change: -${absDiff} percentage points compared to attempt #${i}.`;
              tooltipDeltaHtml = `<div class="tooltip-detail">Score change: <span class="tooltip-delta neg">-${absDiff} percentage points vs #${i}</span></div>`;
            } else {
              deltaHtml = `<span class="trend-delta-badge neutral" aria-hidden="true">— 0 pp</span>`;
              deltaAria = `Score change: 0 percentage points (unchanged) compared to attempt #${i}.`;
              tooltipDeltaHtml = `<div class="tooltip-detail">Score change: <span class="tooltip-delta neu">0 percentage points vs #${i}</span></div>`;
            }
          }

          const topicName = escapeHtml(t.topic_name || "Quiz");
          const nativeTooltip = `${topicName}: ${t.score}/${t.total} (${t.percentage}%)`;
          const fullAriaLabel = `Attempt #${i + 1}: ${t.score} out of ${t.total} questions correct, ${t.percentage}% on ${topicName}. ${deltaAria}`;

          chartHtml += `
            <div class="trend-bar-wrap" tabindex="0" role="group" aria-label="${fullAriaLabel}" title="${nativeTooltip}">
              <span class="trend-bar-score">${t.percentage}%</span>
              <div class="trend-bar ${barClass}" style="height:${barHeight}px;" data-percentage="${pct}"></div>
              <div class="trend-column-footer">
                <span class="trend-bar-label">#${i + 1}</span>
                ${deltaHtml}
              </div>
              <div class="trend-tooltip" role="tooltip">
                <div class="tooltip-title">Attempt #${i + 1} • ${topicName}</div>
                <div class="tooltip-sub">${t.score}/${t.total} questions correct (${t.percentage}%)</div>
                ${tooltipDeltaHtml}
              </div>
            </div>
          `;
        });

        chartHtml += `
              </div>
            </div>
          </div>
        `;
        trendsWrap.innerHTML = chartHtml;
      }
    }
  }

  // Attempts table: displays scoped course attempts if a course is selected, or all attempts if 'All Courses'
  if (tableWrap) {
    const rawTrends = data.recent_trends || [];
    const normActive = activeCourse.toLowerCase();
    const tableTrends = activeCourse
      ? rawTrends.filter(t => (t.course_code || "").trim().toLowerCase() === normActive)
      : rawTrends;

    if (!tableTrends.length) {
      tableWrap.innerHTML = "<p class='muted' style='padding: var(--space-4) var(--space-2);'>No recent attempts recorded.</p>";
    } else {
      let tHtml = `
        <table class="history-table" aria-label="Recent quiz attempts">
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Topic</th>
              <th scope="col">Course</th>
              <th scope="col">Difficulty</th>
              <th scope="col">Score</th>
              <th scope="col">Accuracy</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
      `;
      tableTrends.forEach(t => {
        const dateStr = t.created_at ? new Date(t.created_at).toLocaleString([], {
          month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
        }) : "--";
        tHtml += `
          <tr>
            <td>${dateStr}</td>
            <td><strong>${escapeHtml(t.topic_name)}</strong></td>
            <td>${escapeHtml(t.course_code)}</td>
            <td><span class="level-badge ${escapeHtml(t.recommended_difficulty || 'mixed')}">${escapeHtml(t.recommended_difficulty || 'mixed')}</span></td>
            <td>${t.score} / ${t.total}</td>
            <td><strong>${t.percentage}%</strong></td>
            <td>${escapeHtml(t.status)}</td>
          </tr>
        `;
      });
      tHtml += `</tbody></table>`;
      tableWrap.innerHTML = tHtml;
    }
  }

  // 5. Topic Progress & Mastery
  const topicListEl = $("topicProgressList");
  if (topicListEl) {
    const topics = data.topic_progress || [];
    if (!topics.length) {
      topicListEl.innerHTML = "<p class='muted'>No topic mastery records found.</p>";
    } else {
      topicListEl.innerHTML = topics.map(tp => {
        const level = tp.current_level || "mixed";
        const cumPct = Math.min(100, Math.max(0, tp.cumulative_accuracy));
        const recPct = Math.min(100, Math.max(0, tp.recent_accuracy));

        const weakChips = (tp.weak_concepts && tp.weak_concepts.length)
          ? tp.weak_concepts.map(w => `<span class="tag-chip tag-chip-weak">${escapeHtml(w)}</span>`).join("")
          : "";

        const strongChips = (tp.strong_concepts && tp.strong_concepts.length)
          ? tp.strong_concepts.map(s => `<span class="tag-chip tag-chip-strong">${escapeHtml(s)}</span>`).join("")
          : "";

        return `
          <div class="topic-card">
            <div>
              <div class="topic-card-header">
                <div>
                  <div class="quiz-course-label">${escapeHtml(tp.course_code)}</div>
                  <h3 class="topic-card-title">${escapeHtml(tp.topic_name)}</h3>
                </div>
                <span class="level-badge ${escapeHtml(level)}">${escapeHtml(level)}</span>
              </div>

              <div class="accuracy-dual-row">
                <div class="accuracy-dual-item">
                  <span class="accuracy-dual-label">Cumulative (${tp.total_questions} Qs)</span>
                  <span class="accuracy-dual-val">${tp.cumulative_accuracy}%</span>
                </div>
                <div class="accuracy-dual-item accuracy-dual-right">
                  <span class="accuracy-dual-label">Recent (${tp.recent_questions_count} Qs)</span>
                  <span class="accuracy-dual-val ${recPct >= 70 ? 'acc-high' : (recPct < 50 ? 'acc-low' : 'acc-mid')}">${tp.recent_accuracy}%</span>
                </div>
              </div>

              <div class="progress topic-progress-bar" role="progressbar" aria-valuenow="${cumPct}" aria-valuemin="0" aria-valuemax="100">
                <div class="progress-bar-fill" style="width:${cumPct}%;"></div>
              </div>

              <div class="topic-chips-wrap">
                ${weakChips}
                ${strongChips}
              </div>
            </div>

            <button type="button" class="secondary practice-topic-btn" data-course="${escapeHtml(tp.course_code)}" data-topic="${escapeHtml(tp.topic_name)}">
              Practice This Topic
            </button>
          </div>
        `;
      }).join("");
    }
  }

  // 6. Concept Insights
  const ci = data.concept_insights || {};
  if ($("dashboardStrongConcepts")) {
    const sc = ci.strong_concepts || [];
    $("dashboardStrongConcepts").innerHTML = sc.length
      ? sc.map(c => `<span class="tag-chip tag-chip-strong">${escapeHtml(c)}</span>`).join("")
      : "<span class='muted'>No strong concepts identified yet. Complete quizzes to showcase strengths.</span>";
  }

  if ($("dashboardWeakConcepts")) {
    const wc = ci.weak_concepts || [];
    $("dashboardWeakConcepts").innerHTML = wc.length
      ? wc.map(c => `<span class="tag-chip tag-chip-weak">${escapeHtml(c)}</span>`).join("")
      : "<span class='muted'>No active weak concepts identified. Great work!</span>";
  }

  attachPracticeTopicListeners();
  attachHistoryToggleListeners();
}

function attachHistoryToggleListeners() {
  document.querySelectorAll(".topic-history-toggle-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const panelId = btn.getAttribute("aria-controls");
      if (!panelId) return;
      const panel = document.getElementById(panelId);
      if (!panel) return;
      const isHidden = panel.classList.contains("hidden");
      if (isHidden) {
        panel.classList.remove("hidden");
        panel.setAttribute("aria-hidden", "false");
        btn.setAttribute("aria-expanded", "true");
        btn.textContent = "Hide history";
      } else {
        panel.classList.add("hidden");
        panel.setAttribute("aria-hidden", "true");
        btn.setAttribute("aria-expanded", "false");
        btn.textContent = "View history";
      }
    });
  });
}

function attachPracticeTopicListeners() {
  document.querySelectorAll(".practice-topic-btn").forEach(btn => {
    if (btn.getAttribute("data-listener-attached") === "true") return;
    btn.setAttribute("data-listener-attached", "true");

    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();

      if (isGenerating || btn.disabled || btn.dataset.busy === "true") return;
      btn.disabled = true;
      btn.dataset.busy = "true";

      const courseCode = btn.getAttribute("data-course");
      const topicName = btn.getAttribute("data-topic");
      const topicId = btn.getAttribute("data-topic-id") || null;

      const p = selectAndPracticeTopic(courseCode, topicName, topicId);
      btn._practicePromise = p;
      if (p && typeof p.finally === "function") {
        p.finally(() => {
          btn.disabled = false;
          btn.dataset.busy = "false";
        });
      } else {
        btn.disabled = false;
        btn.dataset.busy = "false";
      }
    });
  });
}

async function selectAndPracticeTopic(courseCode, topicName, topicId = null) {
  if (isGenerating) return;

  const cleanCourse = (courseCode || "").trim();
  const cleanTopic = (topicName || "").trim();

  // 1. Validation: Ensure both course code and topic name are provided
  if (!cleanCourse || !cleanTopic) {
    showScreen("loadingSection");
    const spinner = document.querySelector("#loadingSection .spinner");
    if (spinner) spinner.style.display = "none";
    if ($("loadingTopicLabel")) {
      $("loadingTopicLabel").textContent = `${cleanCourse || "Course unavailable"} • ${cleanTopic || "Topic unavailable"}`;
    }
    if ($("loadingErrorMsg")) {
      $("loadingErrorMsg").textContent = "Please choose a valid course and topic to practice.";
    }
    if ($("loadingErrorBox")) {
      $("loadingErrorBox").classList.remove("hidden");
    }
    const retryBtn = $("loadingRetryBtn");
    if (retryBtn) {
      retryBtn.onclick = () => returnFromLoading();
    }
    return;
  }

  // 2. Ensure curriculum is loaded
  if (!Array.isArray(curriculum) || curriculum.length === 0) {
    try {
      await loadCourses();
    } catch (e) {
      console.warn("Failed to load curriculum courses:", e);
    }
  }

  // 3. Resolve course and topic in curriculum (case, whitespace, and punctuation resilient)
  const courseObj = (curriculum || []).find(
    c => (c.code || "").trim().toLowerCase() === cleanCourse.toLowerCase()
  );
  let resolvedTopic = null;
  if (courseObj && Array.isArray(courseObj.topics)) {
    // 3a. Lookup by explicit topicId
    if (topicId) {
      resolvedTopic = courseObj.topics.find(t => t.id === topicId);
    }
    // 3b. Lookup by exact topic name (case-insensitive)
    if (!resolvedTopic) {
      resolvedTopic = courseObj.topics.find(
        t => (t.name || "").trim().toLowerCase() === cleanTopic.toLowerCase()
      );
    }
    // 3c. Lookup ignoring hyphens, en-dashes, em-dashes, and punctuation
    if (!resolvedTopic) {
      const cleanAlnum = cleanTopic.toLowerCase().replace(/[^a-z0-9]/g, "");
      if (cleanAlnum) {
        resolvedTopic = courseObj.topics.find(
          t => (t.name || "").toLowerCase().replace(/[^a-z0-9]/g, "") === cleanAlnum
        );
      }
    }
    // 3d. Lookup by raw_name alias
    if (!resolvedTopic) {
      resolvedTopic = courseObj.topics.find(
        t => (t.raw_name || "").trim().toLowerCase() === cleanTopic.toLowerCase()
      );
    }
    // 3e. Lookup by normalized raw_name
    if (!resolvedTopic) {
      const cleanAlnum = cleanTopic.toLowerCase().replace(/[^a-z0-9]/g, "");
      if (cleanAlnum) {
        resolvedTopic = courseObj.topics.find(
          t => (t.raw_name || "").toLowerCase().replace(/[^a-z0-9]/g, "") === cleanAlnum
        );
      }
    }
    // 3f. Lookup by substring containment in name or raw_name
    if (!resolvedTopic) {
      const cleanLower = cleanTopic.toLowerCase();
      resolvedTopic = courseObj.topics.find(t => {
        const tLower = (t.name || "").toLowerCase();
        const rLower = (t.raw_name || "").toLowerCase();
        return (tLower && (cleanLower.includes(tLower) || tLower.includes(cleanLower))) ||
               (rLower && (cleanLower.includes(rLower) || rLower.includes(cleanLower)));
      });
    }
    // 3g. Word overlap matching for composite or historical titles (e.g. "History and Applications of Deep Learning")
    if (!resolvedTopic) {
      const stopWords = new Set(["a", "an", "the", "and", "or", "of", "in", "to", "with", "for", "is", "what", "by", "from", "through"]);
      const targetWords = cleanTopic.toLowerCase().replace(/[^a-z0-9\s]/g, " ").split(/\s+/).filter(w => w.length > 1 && !stopWords.has(w));
      if (targetWords.length > 0) {
        let bestScore = 0;
        let bestCandidate = null;
        for (const t of courseObj.topics) {
          const tText = `${t.name || ""} ${t.raw_name || ""} ${t.parent_topic || ""}`.toLowerCase();
          const tWords = new Set(tText.replace(/[^a-z0-9\s]/g, " ").split(/\s+/).filter(w => w.length > 1 && !stopWords.has(w)));
          let matches = 0;
          for (const tw of targetWords) {
            if (tWords.has(tw)) matches++;
          }
          const score = matches / targetWords.length;
          if (score > bestScore && score >= 0.4) {
            bestScore = score;
            bestCandidate = t;
          }
        }
        if (bestCandidate) {
          resolvedTopic = bestCandidate;
        }
      }
    }
    // 3h. Lookup by parent_topic or source_topic_name
    if (!resolvedTopic) {
      resolvedTopic = courseObj.topics.find(
        t => (t.parent_topic || t.parent_section || t.source_topic_name || "").trim().toLowerCase() === cleanTopic.toLowerCase()
      );
    }
  }

  const finalCourseCode = courseObj ? courseObj.code : cleanCourse;
  const finalTopicName = resolvedTopic ? resolvedTopic.name : cleanTopic;
  const finalTopicId = (resolvedTopic && resolvedTopic.id) ? resolvedTopic.id : (topicId || "");

  // 4. Preselect in setup form (without navigating there) so underlying form state stays synced
  const courseSelect = $("course");
  const topicSelect = $("topic");
  if (courseSelect && courseObj) {
    courseSelect.value = courseObj.code;
    if (typeof Event !== "undefined") {
      courseSelect.dispatchEvent(new Event("change"));
    }
    if (topicSelect && resolvedTopic) {
      topicSelect.value = resolvedTopic.name;
    }
  }

  // 5. If topic cannot be resolved from the known curriculum, report actionable error without substituting unrelated topic
  if (!courseObj || !resolvedTopic) {
    showScreen("loadingSection");
    const spinner = document.querySelector("#loadingSection .spinner");
    if (spinner) spinner.style.display = "none";
    if ($("loadingTopicLabel")) {
      $("loadingTopicLabel").textContent = `${finalCourseCode} • ${finalTopicName}`;
    }
    if ($("loadingErrorMsg")) {
      $("loadingErrorMsg").textContent = `Topic "${finalTopicName}" could not be resolved in curriculum course "${courseObj ? courseObj.name : finalCourseCode}". Please choose another topic.`;
    }
    if ($("loadingErrorBox")) {
      $("loadingErrorBox").classList.remove("hidden");
    }
    const retryBtn = $("loadingRetryBtn");
    if (retryBtn) {
      retryBtn.onclick = () => selectAndPracticeTopic(courseCode, topicName, topicId);
    }
    return;
  }

  // 6. Seamlessly launch quiz generation directly — NO home page detour!
  activeNotesSessionId = null;
  await startQuizGeneration(finalCourseCode, finalTopicName, false, finalTopicId);
}

// ── Dashboard Navigation & Button Listeners ──────────────────────────────────
$("navPracticeBtn")?.addEventListener("click", goToHome);
$("navDashboardBtn")?.addEventListener("click", openDashboard);
$("viewDashboardFromResultBtn")?.addEventListener("click", openDashboard);
$("dashboardStartQuizBtn")?.addEventListener("click", goToHome);
$("dashboardRefreshBtn")?.addEventListener("click", loadDashboard);
$("dashboardRetryBtn")?.addEventListener("click", loadDashboard);
$("dashboardCourseFilter")?.addEventListener("change", onDashboardCourseFilterChange);

// ── Notes-to-Quiz Feature State & Handlers ──────────────────────────────────
let selectedNotesFiles = [];
let isDraggingNotes = false;
let isUploadingNotes = false;
let notesToastTimer = null;
let lastExtractionResult = null;
let selectedTopicIndex = 0;
let activeImagePreviewUrl = null;

function cleanupImagePreview() {
  if (activeImagePreviewUrl) {
    try {
      URL.revokeObjectURL(activeImagePreviewUrl);
    } catch (e) {}
    activeImagePreviewUrl = null;
  }
}

function showNotesToast(message, type = "success") {
  const container = $("notesToastContainer");
  if (!container) return;
  if (notesToastTimer) {
    clearTimeout(notesToastTimer);
    notesToastTimer = null;
  }
  const isSuccess = type === "success";
  container.innerHTML = `
    <div class="fixed top-6 right-6 z-50 px-4 py-3 rounded shadow-lg text-white animate-fade-in ${isSuccess ? 'bg-green-600' : 'bg-red-600'}" role="alert">
      <div class="flex items-center gap-2">
        <span>${isSuccess ? '✅' : '❌'}</span>
        <span>${escapeHtml(message)}</span>
        <button type="button" class="ml-3 text-white/80 hover:text-white text-lg notes-toast-close" aria-label="Close">×</button>
      </div>
    </div>
  `;
  container.querySelector(".notes-toast-close")?.addEventListener("click", () => {
    container.innerHTML = "";
  });
  notesToastTimer = setTimeout(() => {
    container.innerHTML = "";
    notesToastTimer = null;
  }, 5000);
}

function announceNotesStatus(message) {
  const statusEl = $("notesLiveStatus");
  if (!statusEl || !message) return;
  if (statusEl.textContent === message) {
    statusEl.textContent = "";
  }
  statusEl.textContent = message;
}

function handleNotesFiles(files) {
  if (!files || files.length === 0) return;
  const maxBytes = 5 * 1024 * 1024;
  const allowedExtensions = [".jpg", ".jpeg", ".png", ".webp", ".pdf", ".docx", ".txt", ".ppt", ".pptx"];

  const fileArr = Array.from(files);
  for (const f of fileArr) {
    if (f.size > maxBytes) {
      showNotesToast(`File "${f.name}" exceeds the 5 MB limit.`, "error");
      return;
    }
    const dotIdx = f.name.lastIndexOf(".");
    const ext = dotIdx >= 0 ? f.name.slice(dotIdx).toLowerCase() : "";
    if (!allowedExtensions.includes(ext)) {
      showNotesToast(`Unsupported file type for "${f.name}". Please upload PDF, DOCX, TXT, PPT, PPTX or photos.`, "error");
      return;
    }
  }

  // Deduplicate and append
  const newFiles = fileArr.filter(
    f => !selectedNotesFiles.some(p => p.name === f.name && p.size === f.size)
  );
  if (newFiles.length === 0) {
    if (fileArr.length > 0) {
      announceNotesStatus("Selected file is already staged.");
    }
    return;
  }
  selectedNotesFiles = [...selectedNotesFiles, ...newFiles];
  renderNotesFilesUI();

  const total = selectedNotesFiles.length;
  if (newFiles.length === 1) {
    announceNotesStatus(`Staged ${newFiles[0].name}. ${total} file${total === 1 ? "" : "s"} ready for upload.`);
  } else {
    announceNotesStatus(`Staged ${newFiles.length} files. ${total} files ready for upload.`);
  }
}

function removeNotesFile(index) {
  cleanupImagePreview();
  if (isUploadingNotes && currentUploadXhr) {
    uploadRequestSeq++;
    try { currentUploadXhr.abort(); } catch (e) {}
    currentUploadXhr = null;
    isUploadingNotes = false;
    if ($("notesProgressWrap")) $("notesProgressWrap").classList.add("hidden");
    if ($("notesProgressBar")) $("notesProgressBar").style.width = "0%";
    cleanupUploadState();
  }
  const removedFile = selectedNotesFiles[index];
  selectedNotesFiles.splice(index, 1);
  if ($("notesFileInput")) {
    $("notesFileInput").value = "";
  }
  renderNotesFilesUI();

  const total = selectedNotesFiles.length;
  const fileName = removedFile ? removedFile.name : "File";
  if (total === 0) {
    announceNotesStatus(`Removed ${fileName}. No files staged.`);
    const dropzone = $("notesDropzone");
    if (dropzone && (document.activeElement === document.body || !document.activeElement)) {
      dropzone.focus?.();
    }
  } else {
    announceNotesStatus(`Removed ${fileName}. ${total} file${total === 1 ? "" : "s"} ready for upload.`);
    const container = $("notesFilesList");
    if (container) {
      const remainingBtns = container.querySelectorAll(".notes-pill-remove, .notes-single-remove");
      const nextIdx = Math.min(index, remainingBtns.length - 1);
      if (remainingBtns[nextIdx]) {
        remainingBtns[nextIdx].focus?.();
      }
    }
  }
}

function renderNotesFilesUI() {
  const container = $("notesFilesList");
  const uploadBtn = $("notesUploadBtn");
  if (!container || !uploadBtn) return;

  if (selectedNotesFiles.length === 0) {
    cleanupImagePreview();
    container.innerHTML = "";
    container.classList.add("hidden");
    uploadBtn.disabled = true;
    uploadBtn.className = "w-full py-2 px-4 rounded-lg font-semibold text-base transition focus:outline-none focus:ring-2 focus:ring-gray-400 focus:ring-opacity-50 shadow-lg flex items-center justify-center gap-2 bg-gray-300 text-gray-500 cursor-not-allowed";
    return;
  }

  container.classList.remove("hidden");
  if (!isUploadingNotes) {
    uploadBtn.disabled = false;
    uploadBtn.className = "w-full py-2 px-4 rounded-lg font-semibold text-base transition focus:outline-none focus:ring-2 focus:ring-gray-400 focus:ring-opacity-50 shadow-lg flex items-center justify-center gap-2 bg-gray-700 text-white hover:bg-gray-800 active:scale-95";
  }

  if (selectedNotesFiles.length === 1) {
    cleanupImagePreview();
    const file = selectedNotesFiles[0];
    const isImage = file.type && file.type.startsWith("image/");
    if (isImage) {
      try {
        activeImagePreviewUrl = URL.createObjectURL(file);
      } catch (e) {
        activeImagePreviewUrl = null;
      }
    }

    container.innerHTML = `
      <div class="flex items-center gap-3 bg-gray-50 rounded-lg p-2 shadow-sm border border-gray-200 mb-2 animate-fade-in">
        ${activeImagePreviewUrl ? `
          <img
            src="${activeImagePreviewUrl}"
            alt="Preview"
            class="w-16 h-16 object-cover rounded-md border border-gray-200"
          />
        ` : `
          <div class="w-16 h-16 flex items-center justify-center bg-gray-200 rounded-md text-gray-500 text-xl">
            <span role="img" aria-label="file">📄</span>
          </div>
        `}

        <div class="flex-1 truncate">
          <div class="font-medium text-gray-800 truncate" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</div>
        </div>

        <button
          type="button"
          class="ml-2 px-2 py-1 text-xs flex items-center gap-1 bg-red-50 text-red-600 rounded hover:bg-red-100 transition border border-red-200 shadow-sm notes-single-remove"
          aria-label="Remove file"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    `;
    container.querySelector(".notes-single-remove")?.addEventListener("click", (e) => {
      e.stopPropagation();
      e.preventDefault();
      removeNotesFile(0);
    });
  } else {
    // Multi-file pill rendering (matching file-uploader.tsx chip layout)
    cleanupImagePreview();
    container.innerHTML = `
      <div class="mb-4" style="max-height: 180px; overflow-y: auto;">
        <div class="flex flex-wrap gap-2">
          ${selectedNotesFiles.map((file, idx) => `
            <span
              class="inline-flex items-center max-w-xs px-3 py-1 rounded-full bg-gray-100 text-gray-800 text-sm font-medium shadow-sm border border-gray-200 truncate"
              style="min-width: 0;"
              title="${escapeHtml(file.name)}"
            >
              <span class="truncate max-w-[120px] text-xs">${escapeHtml(file.name)}</span>
              <button
                type="button"
                class="ml-2 p-0.5 rounded-full hover:bg-red-100 text-red-600 transition border border-transparent focus:outline-none focus:ring-2 focus:ring-red-200 notes-pill-remove"
                data-index="${idx}"
                aria-label="Remove file"
              >
                <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </span>
          `).join("")}
        </div>
      </div>
    `;
    container.querySelectorAll(".notes-pill-remove").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        e.preventDefault();
        const idx = parseInt(btn.dataset.index, 10);
        removeNotesFile(idx);
      });
    });
  }
}

/* ── Progressive Flux Loader (from 21st.dev) ──────────────────────────────────
   Reproduces the exact ProgressiveFluxLoader component from 21st.dev in vanilla JS.
   - Phases: Uploading (0), Processing (40), Finalizing (75), Complete (100)
   - 3D perspective transitions, letter-by-letter staggered entrance
   - Color-mix gradients, outer glow, inset depth shadows, and moving sheen
   - Controlled mode (0..100) & Uncontrolled mode (indeterminate looping sweep)
   - Reduced-motion support (WCAG compliant)
   - Real completion lifecycle (onComplete)
───────────────────────────────────────────────────────────────────────────── */

const FLUX_DEFAULT_PHASES = [
  { at: 0, label: "Uploading" },
  { at: 40, label: "Processing" },
  { at: 75, label: "Finalizing" },
  { at: 100, label: "Complete" },
];

class ProgressiveFluxLoader {
  constructor(options = {}) {
    this.phases = options.phases || FLUX_DEFAULT_PHASES;
    this.duration = options.duration || 12;
    this.loop = options.loop !== false;
    this.showLabel = options.showLabel !== false;
    this.onComplete = options.onComplete || null;

    this.sortedPhases = [...this.phases].sort((a, b) => a.at - b.at);
    this.currentValue = 0;
    this.currentLabel = "";
    this.isControlled = true;
    this.completed = false;
    this.isTransitioningLabel = false;
    this.pendingLabel = null;

    this.rafId = null;
    this.timerId = null;
    this.startTime = null;

    this.labelEl = document.getElementById("fluxLabel");
    this.trackEl = document.getElementById("fluxTrack");
    this.fillEl = document.getElementById("notesProgressBar");

  }

  get isReducedMotion() {
    return typeof window !== "undefined" && window.matchMedia
      ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
      : false;
  }

  pickLabel(val) {
    let active = this.sortedPhases[0]?.label ?? "";
    for (const phase of this.sortedPhases) {
      if (val >= phase.at) active = phase.label;
    }
    return active;
  }

  updateLabel(newLabel) {
    if (!this.showLabel || !this.labelEl) return;
    if (newLabel === this.currentLabel && this.labelEl.innerHTML !== "") return;

    if (this.isTransitioningLabel) {
      this.pendingLabel = newLabel;
      return;
    }

    if (this.isReducedMotion || !this.currentLabel) {
      this.renderLabelDOM(newLabel);
      this.currentLabel = newLabel;
      return;
    }

    this.isTransitioningLabel = true;
    this.labelEl.classList.remove("flux-label-enter");
    this.labelEl.classList.add("flux-label-exit");

    setTimeout(() => {
      this.renderLabelDOM(newLabel);
      this.currentLabel = newLabel;
      this.labelEl.classList.remove("flux-label-exit");
      this.labelEl.classList.add("flux-label-enter");
      this.isTransitioningLabel = false;

      if (this.pendingLabel && this.pendingLabel !== newLabel) {
        const next = this.pendingLabel;
        this.pendingLabel = null;
        this.updateLabel(next);
      }
    }, 450);
  }

  renderLabelDOM(label) {
    if (!this.labelEl) return;
    if (this.isReducedMotion) {
      this.labelEl.textContent = label;
      return;
    }

    const chars = label.split("");
    let html = '<span class="inline-flex">';
    chars.forEach((ch, idx) => {
      if (ch === " ") {
        html += '<span class="flux-letter-space">&nbsp;</span>';
      } else {
        html += `<span class="flux-letter" style="--char-index: ${idx};">${escapeHtml(ch)}</span>`;
      }
    });
    html += '</span>';
    this.labelEl.innerHTML = html;
  }

  setValue(val, labelOverride) {
    this.isControlled = true;
    this.cancelSweep();

    const raw = typeof val === "number" ? val : 0;
    const current = Math.min(100, Math.max(0, raw));
    this.currentValue = current;
    const rounded = Math.round(current);

    const label = labelOverride || this.pickLabel(current);

    if (this.fillEl) {
      this.fillEl.classList.remove("is-processing", "is-indeterminate");
      this.fillEl.style.width = `${current}%`;
    }
    if (this.trackEl) {
      this.trackEl.setAttribute("aria-busy", current < 100 ? "true" : "false");
      this.trackEl.setAttribute("aria-valuenow", rounded);
      this.trackEl.setAttribute("aria-valuetext", label ? `${rounded}% – ${label}` : `${rounded}%`);
    }

    this.updateLabel(label);

    if (current >= 100 && !this.completed) {
      this.completed = true;
      if (this.onComplete) this.onComplete();
    } else if (current < 100) {
      this.completed = false;
    }
  }

  setProcessing(customLabel = "Processing notes…") {
    this.isControlled = true;
    this.cancelSweep();
    this.completed = false;
    this.currentValue = 100;

    if (this.fillEl) {
      this.fillEl.style.width = "100%";
      this.fillEl.classList.add("is-processing");
      this.fillEl.classList.remove("is-indeterminate");
    }
    if (this.trackEl) {
      this.trackEl.setAttribute("aria-busy", "true");
      this.trackEl.setAttribute("aria-valuenow", 100);
      this.trackEl.setAttribute("aria-valuetext", `100% – ${customLabel}`);
    }
    this.updateLabel(customLabel);
  }

  setIndeterminate(label = "Uploading notes…") {
    this.isControlled = false;
    this.completed = false;
    this.cancelSweep();

    if (this.fillEl) {
      this.fillEl.classList.add("is-indeterminate");
      this.fillEl.classList.remove("is-processing");
    }
    if (this.trackEl) {
      this.trackEl.setAttribute("aria-busy", "true");
      this.trackEl.removeAttribute("aria-valuenow");
      this.trackEl.setAttribute("aria-valuetext", label);
    }
    this.updateLabel(label);
  }

  startUncontrolled(customLabel) {
    this.isControlled = false;
    this.completed = false;
    this.cancelSweep();

    if (customLabel) {
      this.updateLabel(customLabel);
    }

    const totalMs = Math.max(500, this.duration * 1000);
    this.startTime = null;

    const tick = (ts) => {
      if (this.startTime === null) this.startTime = ts;
      const pct = Math.min(100, ((ts - this.startTime) / totalMs) * 100);
      this.currentValue = pct;

      if (this.fillEl) {
        this.fillEl.style.width = `${pct}%`;
      }
      const label = customLabel || this.pickLabel(pct);
      if (this.trackEl) {
        const rounded = Math.round(pct);
        this.trackEl.setAttribute("aria-valuenow", rounded);
        this.trackEl.setAttribute("aria-valuetext", label ? `${rounded}% – ${label}` : `${rounded}%`);
      }
      this.updateLabel(label);

      if (pct >= 100) {
        if (!this.completed) {
          this.completed = true;
          if (this.onComplete) this.onComplete();
        }
        if (this.loop) {
          this.startTime = null;
          this.completed = false;
          this.timerId = setTimeout(() => {
            if (this.fillEl) this.fillEl.style.width = "0%";
            this.rafId = requestAnimationFrame(tick);
          }, 700);
        }
        return;
      }
      this.rafId = requestAnimationFrame(tick);
    };

    this.rafId = requestAnimationFrame(tick);
  }

  cancelSweep() {
    if (this.rafId) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
    if (this.timerId) {
      clearTimeout(this.timerId);
      this.timerId = null;
    }
    this.startTime = null;
  }

  complete(callback) {
    this.cancelSweep();
    this.isControlled = true;
    this.currentValue = 100;
    if (this.fillEl) {
      this.fillEl.classList.remove("is-processing", "is-indeterminate");
      this.fillEl.style.width = "100%";
    }
    if (this.trackEl) {
      this.trackEl.setAttribute("aria-busy", "false");
      this.trackEl.setAttribute("aria-valuenow", 100);
      this.trackEl.setAttribute("aria-valuetext", "100% – Complete");
    }
    this.updateLabel("Complete");
    this.completed = true;
    if (callback) {
      callback();
    } else if (this.onComplete) {
      this.onComplete();
    }
  }

  reset() {
    this.cancelSweep();
    this.isControlled = true;
    this.currentValue = 0;
    this.completed = false;
    this.isTransitioningLabel = false;
    this.pendingLabel = null;
    this.currentLabel = "";
    if (this.fillEl) {
      this.fillEl.classList.remove("is-processing", "is-indeterminate");
      this.fillEl.style.width = "0%";
    }
    if (this.trackEl) {
      this.trackEl.setAttribute("aria-busy", "false");
      this.trackEl.setAttribute("aria-valuenow", 0);
      this.trackEl.setAttribute("aria-valuetext", "0% – Uploading");
    }
    if (this.labelEl) {
      this.labelEl.innerHTML = "";
      this.labelEl.classList.remove("flux-label-enter", "flux-label-exit");
    }
  }
}

let fluxLoaderInstance = null;
let currentUploadXhr = null;
let uploadRequestSeq = 0;

function getFluxLoader() {
  if (!fluxLoaderInstance) {
    fluxLoaderInstance = new ProgressiveFluxLoader({
      phases: FLUX_DEFAULT_PHASES,
      duration: 12,
      loop: true
    });
  }
  return fluxLoaderInstance;
}

function updateProgressiveFluxLoader(progress) {
  const loader = getFluxLoader();
  loader.setValue(progress, "Uploading");
}

function setUploadIndeterminateState(label = "Uploading") {
  const loader = getFluxLoader();
  loader.startUncontrolled(label);
}

function cleanupUploadState() {
  isUploadingNotes = false;
  const spinner = $("notesSpinner");
  const btnText = $("notesUploadBtnText");
  const uploadBtn = $("notesUploadBtn");

  if (spinner) spinner.classList.add("hidden");
  if (btnText) btnText.textContent = "Upload & Find Topics";
  if (uploadBtn) {
    if (selectedNotesFiles.length > 0) {
      uploadBtn.disabled = false;
      uploadBtn.className = "w-full py-2 px-4 rounded-lg font-semibold text-base transition focus:outline-none focus:ring-2 focus:ring-gray-400 focus:ring-opacity-50 shadow-lg flex items-center justify-center gap-2 bg-gray-700 text-white hover:bg-gray-800 active:scale-95";
    } else {
      uploadBtn.disabled = true;
      uploadBtn.className = "w-full py-2 px-4 rounded-lg font-semibold text-base transition focus:outline-none focus:ring-2 focus:ring-gray-400 focus:ring-opacity-50 shadow-lg flex items-center justify-center gap-2 bg-gray-300 text-gray-500 cursor-not-allowed";
    }
  }
}

function handleNotesUpload() {
  if (selectedNotesFiles.length === 0 || isUploadingNotes) return;

  isUploadingNotes = true;
  const uploadBtn = $("notesUploadBtn");
  const spinner = $("notesSpinner");
  const btnText = $("notesUploadBtnText");
  const progressWrap = $("notesProgressWrap");
  const progressBar = $("notesProgressBar");

  if (uploadBtn) {
    uploadBtn.disabled = true;
    uploadBtn.className = "w-full py-2 px-4 rounded-lg font-semibold text-base transition focus:outline-none focus:ring-2 focus:ring-gray-400 focus:ring-opacity-50 shadow-lg flex items-center justify-center gap-2 bg-gray-300 text-gray-500 cursor-not-allowed";
  }
  if (spinner) spinner.classList.remove("hidden");
  if (btnText) btnText.textContent = "Uploading...";
  if (progressWrap) progressWrap.classList.remove("hidden");
  if (progressBar) {
    progressBar.style.width = "0%";
    progressBar.setAttribute("aria-valuenow", 0);
  }

  const formData = new FormData();
  selectedNotesFiles.forEach(f => {
    formData.append("files", f);
  });

  const thisRequestId = ++uploadRequestSeq;
  sendNotesUploadXhr(formData, thisRequestId, false);
}

async function sendNotesUploadXhr(formData, thisRequestId, isRetry) {
  if (thisRequestId !== uploadRequestSeq) return;

  const progressWrap = $("notesProgressWrap");
  const progressBar = $("notesProgressBar");
  const btnText = $("notesUploadBtnText");

  // Ensure fresh, unexpired session before dispatching upload
  await ensureAuthSession();
  if (thisRequestId !== uploadRequestSeq) return;

  const xhr = new XMLHttpRequest();
  currentUploadXhr = xhr;

  xhr.open("POST", apiUrl("/api/notes/extract-topics"), true);
  if (authToken && !isTokenExpired(authToken)) {
    xhr.setRequestHeader("Authorization", `Bearer ${authToken}`);
  }

  // 1. Upload byte-transfer progress directly following real transferred bytes
  xhr.upload.onprogress = (event) => {
    if (thisRequestId !== uploadRequestSeq || xhr !== currentUploadXhr) return;
    if (!event.lengthComputable || event.total <= 0) return;
    const progress = Math.max(0, Math.min(100, (event.loaded / event.total) * 100));
    if (progressBar) {
      progressBar.style.width = `${progress}%`;
      progressBar.setAttribute("aria-valuenow", Math.round(progress));
    }
  };

  // 2. Upload byte transfer finished, transition naturally into server processing
  xhr.upload.onload = () => {
    if (thisRequestId !== uploadRequestSeq || xhr !== currentUploadXhr) return;
    if (btnText) btnText.textContent = "Processing...";
    if (progressBar) {
      progressBar.style.width = "100%";
      progressBar.setAttribute("aria-valuenow", 100);
    }
  };

  // 3. Response handling
  xhr.onload = async () => {
    if (thisRequestId !== uploadRequestSeq || xhr !== currentUploadXhr) return;
    currentUploadXhr = null;
    let data = null;
    try {
      data = JSON.parse(xhr.responseText);
    } catch (e) {
      data = null;
    }

    // ── Authentication Expiration Handling (401) ──
    if (xhr.status === 401) {
      if (!isRetry) {
        console.warn("[AUTH] Notes upload 401 received. Renewing session and retrying upload once...");
        if (btnText) btnText.textContent = "Refreshing session...";
        if (progressBar) {
          progressBar.style.width = "0%";
          progressBar.setAttribute("aria-valuenow", 0);
        }
        await ensureAuthSession(true);
        if (thisRequestId === uploadRequestSeq) {
          sendNotesUploadXhr(formData, thisRequestId, true);
          return;
        }
      } else {
        if (typeof localStorage !== "undefined" && localStorage.removeItem) {
          localStorage.removeItem("learnai_auth_token");
        }
        authToken = null;
        showNotesToast("Session expired. Please refresh the page to sign in again.", "error");
        setTimeout(() => {
          if (progressWrap) progressWrap.classList.add("hidden");
          if (progressBar) progressBar.style.width = "0%";
        }, 500);
        cleanupUploadState();
        return;
      }
    }

    if (xhr.status >= 200 && xhr.status < 300 && data) {
      if (!data.success || data.relevant === false || !data.matched_topics || data.matched_topics.length === 0) {
        lastExtractionResult = data;
        selectedTopicIndex = 0;
        showNotesToast(data.message || "No curriculum syllabus topics matched your notes.", "error");
        if ($("notesTopicResultsCard")) $("notesTopicResultsCard").classList.add("hidden");
        setTimeout(() => {
          if (progressWrap) progressWrap.classList.add("hidden");
          if (progressBar) progressBar.style.width = "0%";
        }, 500);
      } else {
        // Success: valid response with matched syllabus topics
        if (progressBar) {
          progressBar.style.width = "100%";
          progressBar.setAttribute("aria-valuenow", 100);
        }
        lastExtractionResult = data;
        selectedTopicIndex = 0;

        // Ensure intermediate results panel is not shown
        if ($("notesTopicResultsCard")) $("notesTopicResultsCard").classList.add("hidden");

        // Compact success notification
        const topicCount = Array.isArray(data.matched_topics) ? data.matched_topics.length : 0;
        const topicWord = topicCount === 1 ? "syllabus topic" : "syllabus topics";
        const successMsg = data.message || `Notes processed successfully. Extracted ${topicCount} ${topicWord}.`;
        showNotesToast(successMsg, "success");

        // Reset staged uploader files and dropzone preview
        cleanupImagePreview();
        selectedNotesFiles = [];
        renderNotesFilesUI();
        if ($("notesFileInput")) $("notesFileInput").value = "";

        // Invalidate cache and refresh saved study materials, auto-expanding the new material
        studyMaterialsCacheTime = 0;
        materialTopicsCache.clear();
        if (typeof loadStudyMaterials === "function") {
          loadStudyMaterials({ forceRefresh: true, expandMaterialId: data.study_material_id });
        }

        setTimeout(() => {
          if (progressWrap) progressWrap.classList.add("hidden");
          if (progressBar) progressBar.style.width = "0%";
          // Smoothly scroll to the expanded material card
          const targetCard = data.study_material_id ? $(`materialCard_${data.study_material_id}`) : $("savedMaterialsSection");
          if (targetCard) {
            targetCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
          }
        }, 600);
      }
    } else {
      let errMsg = "Unable to process files. Please try again.";
      if (data && data.detail) {
        errMsg = typeof data.detail === "object" ? JSON.stringify(data.detail) : data.detail;
      } else if (xhr.status === 413) {
        errMsg = "File exceeds the maximum allowed size (5 MB).";
      } else if (xhr.status === 429) {
        errMsg = "Upload rate limit reached. Please wait a moment before trying again.";
      } else if (xhr.status >= 500) {
        errMsg = "Server error while processing your document. Please try again.";
      } else if (xhr.statusText) {
        errMsg = `Upload failed (${xhr.status}): ${xhr.statusText}`;
      }
      showNotesToast(errMsg, "error");
      setTimeout(() => {
        if (progressWrap) progressWrap.classList.add("hidden");
        if (progressBar) progressBar.style.width = "0%";
      }, 500);
    }
    cleanupUploadState();
  };

  // 4. Network error handling
  xhr.onerror = () => {
    if (thisRequestId !== uploadRequestSeq || xhr !== currentUploadXhr) return;
    currentUploadXhr = null;
    showNotesToast("Network error during upload. Please check your connection and try again.", "error");
    setTimeout(() => {
      if (progressWrap) progressWrap.classList.add("hidden");
      if (progressBar) progressBar.style.width = "0%";
    }, 500);
    cleanupUploadState();
  };

  // 5. Timeout handling
  xhr.ontimeout = () => {
    if (thisRequestId !== uploadRequestSeq || xhr !== currentUploadXhr) return;
    currentUploadXhr = null;
    showNotesToast("Upload request timed out. Please try uploading a smaller file.", "error");
    setTimeout(() => {
      if (progressWrap) progressWrap.classList.add("hidden");
      if (progressBar) progressBar.style.width = "0%";
    }, 500);
    cleanupUploadState();
  };

  xhr.send(formData);
}

function renderMatchedTopicsUI(data) {
  const card = $("notesTopicResultsCard");
  const listContainer = $("notesTopicOptions");
  const partialAlert = $("notesPartialAlert");
  const irrelevantAlert = $("notesIrrelevantAlert");
  const generateQuizBtn = $("notesGenerateQuizBtn");
  const badge = $("notesResultsBadge");

  if (!card || !listContainer) return;
  card.classList.remove("hidden");

  // Scroll smoothly to results card
  setTimeout(() => {
    card.scrollIntoView({ behavior: "smooth", block: "start" });
  }, 100);

  if (!data || !data.relevant || !data.matched_topics || data.matched_topics.length === 0) {
    if (badge) {
      badge.className = "badge badge-error";
      badge.textContent = "No Match";
    }
    if (irrelevantAlert) irrelevantAlert.classList.remove("hidden");
    if (partialAlert) partialAlert.classList.add("hidden");
    if (generateQuizBtn) generateQuizBtn.classList.add("hidden");
    listContainer.innerHTML = "";
    if ($("notesIrrelevantMsg") && data.message) {
      $("notesIrrelevantMsg").textContent = data.message;
    }
    return;
  }

  // Relevant match found
  if (badge) {
    badge.className = "badge badge-success";
    badge.textContent = "Curriculum Match Found";
  }
  if (irrelevantAlert) irrelevantAlert.classList.add("hidden");
  if (generateQuizBtn) generateQuizBtn.classList.remove("hidden");

  if (data.partial && partialAlert) {
    partialAlert.classList.remove("hidden");
  } else if (partialAlert) {
    partialAlert.classList.add("hidden");
  }

  listContainer.innerHTML = data.matched_topics.map((t, idx) => {
    const isChecked = idx === selectedTopicIndex;
    const pct = Math.round((t.confidence || 0) * 100);
    const concepts = (t.matched_concepts || []).map(c => `
      <span class="notes-concept-chip">${escapeHtml(c)}</span>
    `).join("");

    return `
      <div class="notes-topic-item ${isChecked ? 'selected' : ''}" data-index="${idx}" role="button" tabindex="0">
        <input type="radio" name="matchedTopicRadio" class="notes-topic-radio" id="topicRadio_${idx}" value="${idx}" ${isChecked ? 'checked' : ''} />
        <div class="notes-topic-body">
          <div class="notes-topic-header-row">
            <span class="notes-course-tag">${escapeHtml(t.course_code)} - ${escapeHtml(t.course_name)}</span>
            <span class="notes-confidence-badge">${pct}% Match</span>
          </div>
          <div class="notes-topic-name">${escapeHtml(t.topic_name)}</div>
          ${concepts ? `<div class="notes-concepts-row">${concepts}</div>` : ''}
        </div>
      </div>
    `;
  }).join("");

  listContainer.querySelectorAll(".notes-topic-item").forEach(item => {
    item.addEventListener("click", () => {
      const idx = parseInt(item.dataset.index, 10);
      selectedTopicIndex = idx;
      listContainer.querySelectorAll(".notes-topic-item").forEach((el, i) => {
        const isSel = i === idx;
        el.classList.toggle("selected", isSel);
        const radio = el.querySelector(".notes-topic-radio");
        if (radio) radio.checked = isSel;
      });
    });
  });
}

function initNotesUploaderListeners() {
  const dropzone = $("notesDropzone");
  const fileInput = $("notesFileInput");
  const uploadBtn = $("notesUploadBtn");
  const generateQuizBtn = $("notesGenerateQuizBtn");
  const resetBtn = $("notesResetBtn");

  if (dropzone) {
    dropzone.addEventListener("click", () => {
      if (fileInput) fileInput.click();
    });
    dropzone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (fileInput) fileInput.click();
      }
    });
    dropzone.addEventListener("dragover", (e) => {
      e.preventDefault();
      isDraggingNotes = true;
      dropzone.classList.remove("border-gray-300", "bg-gray-50");
      dropzone.classList.add("border-blue-500", "bg-blue-50");
    });
    dropzone.addEventListener("dragleave", () => {
      isDraggingNotes = false;
      dropzone.classList.remove("border-blue-500", "bg-blue-50");
      dropzone.classList.add("border-gray-300", "bg-gray-50");
    });
    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      isDraggingNotes = false;
      dropzone.classList.remove("border-blue-500", "bg-blue-50");
      dropzone.classList.add("border-gray-300", "bg-gray-50");
      if (e.dataTransfer && e.dataTransfer.files) {
        handleNotesFiles(e.dataTransfer.files);
      }
    });
  }

  if (fileInput) {
    fileInput.addEventListener("change", (e) => {
      if (e.target.files && e.target.files.length > 0) {
        handleNotesFiles(e.target.files);
      }
      e.target.value = "";
    });
  }

  if (uploadBtn) {
    uploadBtn.addEventListener("click", handleNotesUpload);
  }

  if (generateQuizBtn) {
    generateQuizBtn.addEventListener("click", () => {
      if (!lastExtractionResult || !lastExtractionResult.matched_topics) return;
      const topicObj = lastExtractionResult.matched_topics[selectedTopicIndex] || lastExtractionResult.matched_topics[0];
      if (!topicObj) return;

      startQuizGeneration(
        topicObj.course_code,
        topicObj.topic_name,
        false,
        topicObj.topic_id,
        lastExtractionResult.notes_session_id,
        lastExtractionResult.study_material_id
      );
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener("click", () => {
      cleanupImagePreview();
      uploadRequestSeq++;
      if (currentUploadXhr) {
        try { currentUploadXhr.abort(); } catch (e) {}
        currentUploadXhr = null;
      }
      isUploadingNotes = false;
      if (fluxLoaderInstance) fluxLoaderInstance.reset();
      if ($("notesProgressWrap")) $("notesProgressWrap").classList.add("hidden");
      cleanupUploadState();
      selectedNotesFiles = [];
      lastExtractionResult = null;
      selectedTopicIndex = 0;
      if ($("notesFileInput")) {
        $("notesFileInput").value = "";
      }
      renderNotesFilesUI();
      announceNotesStatus("Notes files cleared.");
      if ($("notesTopicResultsCard")) {
        $("notesTopicResultsCard").classList.add("hidden");
      }
      if ($("notesUploaderCard")) {
        $("notesUploaderCard").scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  }

  $("navNotesBtn")?.addEventListener("click", () => {
    if (isGenerating) return;
    showScreen("notesUploadSection");
  });

  $("setupNotesBtn")?.addEventListener("click", () => {
    if (isGenerating) return;
    showScreen("notesUploadSection");
  });
}


// ── Study Materials Client Implementation ────────────────────────────────────

let activeStudyMaterialId = null;
let lastPracticedStudyMaterialId = null;
let materialPendingRemoval = null;
let studyMaterialsCache = null;
let studyMaterialsCacheTime = 0;
let materialTopicsCache = new Map();
let expandedMaterialId = null;
let isLoadingMaterials = false;

function formatFileSize(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function formatMaterialDate(isoString) {
  if (!isoString) return "Recently";
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return "Recently";
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  } catch (e) {
    return "Recently";
  }
}

function getFileTypeBadgeClass(fileType) {
  const ft = (fileType || "").toLowerCase();
  if (ft.includes("ppt")) return "icon-pptx";
  if (ft.includes("pdf")) return "icon-pdf";
  if (ft.includes("doc")) return "icon-docx";
  if (ft.includes("txt")) return "icon-txt";
  if (ft.includes("image") || ft.includes("png") || ft.includes("jpg")) return "icon-image";
  return "icon-txt";
}

async function loadStudyMaterials(options = {}) {
  const forceRefresh = options.forceRefresh || false;
  const targetExpandId = options.expandMaterialId || expandedMaterialId || null;

  const loadingEl = $("materialsLoading");
  const emptyState = $("materialsEmptyState");
  const listView = $("materialsListView");

  const now = Date.now();
  // If not force-refreshing, reuse cache if fresh within 15 seconds
  if (!forceRefresh && studyMaterialsCache && (now - studyMaterialsCacheTime < 15000)) {
    if (loadingEl) loadingEl.classList.add("hidden");
    if (studyMaterialsCache.length === 0) {
      if (emptyState) emptyState.classList.remove("hidden");
      if (listView) listView.classList.add("hidden");
    } else {
      if (emptyState) emptyState.classList.add("hidden");
      if (listView) listView.classList.remove("hidden");
      renderMaterialsGrid(studyMaterialsCache);
      if (targetExpandId) {
        expandMaterialCard(targetExpandId);
      }
    }
    return;
  }

  if (isLoadingMaterials) return;
  isLoadingMaterials = true;

  if (!studyMaterialsCache) {
    if (loadingEl) loadingEl.classList.remove("hidden");
    if (emptyState) emptyState.classList.add("hidden");
  }

  try {
    const res = await authenticatedFetch("/api/study-materials");
    if (!res.ok) {
      throw new Error(`Failed to load study materials (HTTP ${res.status})`);
    }
    const data = await res.json();
    studyMaterialsCache = data.materials || [];
    studyMaterialsCacheTime = Date.now();

    if (loadingEl) loadingEl.classList.add("hidden");

    if (studyMaterialsCache.length === 0) {
      if (emptyState) emptyState.classList.remove("hidden");
      if (listView) listView.classList.add("hidden");
    } else {
      if (emptyState) emptyState.classList.add("hidden");
      if (listView) listView.classList.remove("hidden");
      renderMaterialsGrid(studyMaterialsCache);
      if (targetExpandId) {
        expandMaterialCard(targetExpandId);
      }
    }
  } catch (err) {
    console.error("Error loading study materials:", err);
    if (loadingEl) loadingEl.classList.add("hidden");
    if (!studyMaterialsCache) {
      if (emptyState) emptyState.classList.remove("hidden");
      if (listView) listView.classList.add("hidden");
    }
  } finally {
    isLoadingMaterials = false;
  }
}

// Backward-compatibility alias
function openStudyMaterials() {
  if (isGenerating) return;
  showScreen("notesUploadSection");
  loadStudyMaterials({ forceRefresh: true });
}

function renderMaterialsGrid(materials) {
  const grid = $("materialsGrid");
  if (!grid) return;

  grid.innerHTML = materials.map(m => {
    const iconClass = getFileTypeBadgeClass(m.file_type);
    const dateStr = formatMaterialDate(m.created_at);
    const sizeStr = formatFileSize(m.file_size);
    const masteryPct = typeof m.mastery_percentage === "number" ? m.mastery_percentage : 0;
    const fillClass = masteryPct >= 80 ? "fill-high" : (masteryPct > 0 ? "fill-medium" : "");
    const isExpanded = expandedMaterialId === m.id;

    return `
      <div class="material-card ${isExpanded ? 'is-expanded' : ''}" data-material-id="${escapeHtml(m.id)}" id="materialCard_${escapeHtml(m.id)}">
        <div class="material-card-summary">
          <div class="material-card-top">
            <div class="material-card-type-icon ${iconClass}">
              ${escapeHtml(m.file_type || "FILE")}
            </div>
            <div class="material-card-title-wrap">
              <h3 class="material-card-filename" title="${escapeHtml(m.filename)}">
                ${escapeHtml(m.filename)}
              </h3>
              <div class="material-card-submeta">
                <span>${dateStr}</span>
                <span>•</span>
                <span>${sizeStr}</span>
              </div>
            </div>
          </div>

          <div class="material-card-stats-row">
            <div class="material-stat">
              <span class="material-stat-num">${m.topic_count || 0}</span>
              <span class="material-stat-lbl">Topics</span>
            </div>
            <div class="material-stat">
              <span class="material-stat-num">${m.attempted_count || 0}</span>
              <span class="material-stat-lbl">Attempted</span>
            </div>
            <div class="material-stat">
              <span class="material-stat-num">${m.mastered_count || 0}</span>
              <span class="material-stat-lbl">Mastered</span>
            </div>
          </div>

          <div class="material-card-progress-section">
            <div class="material-progress-labels">
              <span class="muted text-xs">Mastery Progress</span>
              <span class="text-xs font-semibold">${masteryPct}%</span>
            </div>
            <div class="progress-bar-track">
              <div class="progress-bar-fill ${fillClass}" style="width: ${masteryPct}%;"></div>
            </div>
          </div>

          <div class="material-card-footer">
            <button
              type="button"
              class="btn-primary btn-open-material"
              data-material-id="${escapeHtml(m.id)}"
              aria-expanded="${isExpanded ? 'true' : 'false'}"
              aria-controls="materialTopics_${escapeHtml(m.id)}"
            >
              <span class="btn-open-text">${isExpanded ? 'Hide Topics &uarr;' : 'Open Topics &amp; Practice &rarr;'}</span>
            </button>
            <button
              type="button"
              class="btn-icon-danger btn-remove-material"
              data-material-id="${escapeHtml(m.id)}"
              data-filename="${escapeHtml(m.filename)}"
              title="Remove study material (${escapeHtml(m.filename)})"
              aria-label="Remove study material ${escapeHtml(m.filename)}"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon-trash">
                <polyline points="3 6 5 6 21 6"></polyline>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                <line x1="10" y1="11" x2="10" y2="17"></line>
                <line x1="14" y1="11" x2="14" y2="17"></line>
              </svg>
            </button>
          </div>
        </div>

        <div class="material-card-topics-panel ${isExpanded ? '' : 'hidden'}" id="materialTopics_${escapeHtml(m.id)}" role="region" aria-label="Extracted topics for ${escapeHtml(m.filename)}">
          <div class="material-topics-header-inline">
            <h4 class="material-topics-heading-inline">Extracted Syllabus Topics (${m.topic_count || 0})</h4>
            <span class="muted text-xs">${m.mastered_count || 0} of ${m.topic_count || 0} mastered</span>
          </div>
          <div class="material-topics-list-inline" id="materialTopicsList_${escapeHtml(m.id)}">
            <!-- Rendered dynamically -->
          </div>
        </div>
      </div>
    `;
  }).join("");

  grid.querySelectorAll(".btn-open-material").forEach(btn => {
    btn.addEventListener("click", () => {
      const matId = btn.getAttribute("data-material-id");
      if (!matId) return;
      if (expandedMaterialId === matId) {
        collapseMaterialCard(matId);
      } else {
        expandMaterialCard(matId);
      }
    });
  });

  grid.querySelectorAll(".btn-remove-material").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const matId = btn.getAttribute("data-material-id");
      const filename = btn.getAttribute("data-filename") || "this material";
      if (matId) promptRemoveMaterial(matId, filename);
    });
  });
}

function collapseMaterialCard(materialId) {
  const card = $(`materialCard_${materialId}`);
  const panel = $(`materialTopics_${materialId}`);
  const btn = card?.querySelector(".btn-open-material");
  const btnText = btn?.querySelector(".btn-open-text");

  if (card) card.classList.remove("is-expanded");
  if (panel) panel.classList.add("hidden");
  if (btn) btn.setAttribute("aria-expanded", "false");
  if (btnText) btnText.innerHTML = "Open Topics &amp; Practice &rarr;";

  if (expandedMaterialId === materialId) {
    expandedMaterialId = null;
  }
}

async function expandMaterialCard(materialId) {
  // If another card is expanded, collapse it first
  if (expandedMaterialId && expandedMaterialId !== materialId) {
    collapseMaterialCard(expandedMaterialId);
  }

  expandedMaterialId = materialId;
  const card = $(`materialCard_${materialId}`);
  const panel = $(`materialTopics_${materialId}`);
  const listEl = $(`materialTopicsList_${materialId}`);
  const btn = card?.querySelector(".btn-open-material");
  const btnText = btn?.querySelector(".btn-open-text");

  if (card) card.classList.add("is-expanded");
  if (panel) panel.classList.remove("hidden");
  if (btn) btn.setAttribute("aria-expanded", "true");
  if (btnText) btnText.innerHTML = "Hide Topics &uarr;";

  if (!listEl) return;

  // Check cache for this material's topics
  if (materialTopicsCache.has(materialId)) {
    renderInlineTopicsList(materialId, materialTopicsCache.get(materialId));
    return;
  }

  // Show inline loading state
  listEl.innerHTML = `
    <div class="topics-loading-inline">
      <div class="spinner spinner-sm" aria-hidden="true"></div>
      <span>Loading extracted topics and quiz progress...</span>
    </div>
  `;

  try {
    const res = await authenticatedFetch(`/api/study-materials/${encodeURIComponent(materialId)}`);
    if (!res.ok) {
      throw new Error(`Failed to load topics (HTTP ${res.status})`);
    }
    const material = await res.json();
    materialTopicsCache.set(materialId, material);
    renderInlineTopicsList(materialId, material);
  } catch (err) {
    console.error("Failed to load study material topics:", err);
    listEl.innerHTML = `
      <div class="notes-alert notes-alert-error" style="margin: var(--space-2) 0;">
        <span>Unable to load topics for this material.</span>
        <button type="button" class="secondary btn-sm" onclick="expandMaterialCard('${escapeHtml(materialId)}')">Retry</button>
      </div>
    `;
  }
}

function renderInlineTopicsList(materialId, material) {
  const listEl = $(`materialTopicsList_${materialId}`);
  if (!listEl) return;

  const topics = material.topics || [];
  if (topics.length === 0) {
    listEl.innerHTML = '<p class="muted text-sm">No syllabus topics extracted from this document.</p>';
    return;
  }

  listEl.innerHTML = topics.map(t => {
    const statusKey = t.status || "not_attempted";
    const statusLabel = t.status_label || "Not Attempted";
    const attempts = t.attempt_count || 0;
    const latestScore = t.latest_score !== null && t.latest_score !== undefined ? `${Math.round(t.latest_score)}%` : null;
    const matchScore = (typeof t.confidence === "number" && t.confidence > 0)
      ? Math.round(t.confidence * (t.confidence <= 1.0 ? 100 : 1))
      : null;
    const matchBadge = matchScore !== null
      ? `<span class="notes-confidence-badge">${matchScore}% Match</span>`
      : "";

    let subStatusText = "";
    if (statusKey === "mastered") {
      subStatusText = latestScore ? ` • Latest score: ${latestScore}` : "";
    } else if (statusKey === "attempted") {
      subStatusText = latestScore ? ` • Latest score: ${latestScore}` : ` • ${attempts} attempt${attempts === 1 ? '' : 's'}`;
    }

    const concepts = (t.matched_concepts || []).map(c => `
      <span class="concept-chip-sm">${escapeHtml(c)}</span>
    `).join("");

    return `
      <div class="material-topic-card">
        <div class="material-topic-main">
          <div class="material-topic-header">
            <span class="notes-course-tag">${escapeHtml(t.course_code)} - ${escapeHtml(t.course_name)}</span>
            ${matchBadge}
            <span class="topic-badge-status status-${statusKey}">
              ${escapeHtml(statusLabel)}${subStatusText}
            </span>
          </div>
          <h4 class="material-topic-title">${escapeHtml(t.topic_name)}</h4>
          ${concepts ? `<div class="material-topic-concepts">${concepts}</div>` : ''}
        </div>
        <div class="material-topic-actions">
          <button
            type="button"
            class="btn-primary btn-sm btn-practice-topic"
            data-course="${escapeHtml(t.course_code)}"
            data-topic="${escapeHtml(t.topic_name)}"
            data-topic-id="${escapeHtml(t.topic_id || '')}"
            data-material-id="${escapeHtml(material.id)}"
          >
            Practice Topic &rarr;
          </button>
        </div>
      </div>
    `;
  }).join("");

  listEl.querySelectorAll(".btn-practice-topic").forEach(btn => {
    btn.addEventListener("click", () => {
      const course = btn.getAttribute("data-course");
      const topic = btn.getAttribute("data-topic");
      const topicId = btn.getAttribute("data-topic-id") || null;
      const matId = btn.getAttribute("data-material-id");
      if (course && topic) {
        startQuizGeneration(course, topic, false, topicId, null, matId);
      }
    });
  });
}

function promptRemoveMaterial(materialId, filename) {
  materialPendingRemoval = { id: materialId, filename };
  const nameEl = $("removeMaterialFilename");
  if (nameEl) nameEl.textContent = filename;
  const modal = $("materialRemoveModal");
  if (modal) modal.classList.remove("hidden");
}

function closeRemoveModal() {
  materialPendingRemoval = null;
  const modal = $("materialRemoveModal");
  if (modal) modal.classList.add("hidden");
}

async function confirmRemoveMaterial() {
  if (!materialPendingRemoval) return;
  const { id } = materialPendingRemoval;
  const confirmBtn = $("removeModalConfirmBtn");
  if (confirmBtn) {
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Removing...";
  }

  try {
    const res = await authenticatedFetch(`/api/study-materials/${encodeURIComponent(id)}`, {
      method: "DELETE"
    });
    if (!res.ok) {
      throw new Error(`Failed to remove material: HTTP ${res.status}`);
    }
    materialTopicsCache.delete(id);
    if (expandedMaterialId === id) {
      expandedMaterialId = null;
    }
    closeRemoveModal();
    loadStudyMaterials({ forceRefresh: true });
  } catch (err) {
    console.error("Error removing study material:", err);
    alert("Could not remove the study material. Please try again.");
  } finally {
    if (confirmBtn) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = "Remove Material";
    }
  }
}

let studyMaterialsListenersInitialized = false;

function initStudyMaterialsListeners() {
  if (studyMaterialsListenersInitialized) return;
  studyMaterialsListenersInitialized = true;

  $("navMaterialsBtn")?.addEventListener("click", () => {
    if (isGenerating) return;
    showScreen("notesUploadSection");
    loadStudyMaterials({ forceRefresh: true });
  });

  $("materialsRefreshBtn")?.addEventListener("click", () => {
    materialTopicsCache.clear();
    loadStudyMaterials({ forceRefresh: true });
  });

  $("materialsEmptyUploadBtn")?.addEventListener("click", () => {
    const uploaderCard = $("notesUploaderCard");
    if (uploaderCard) {
      uploaderCard.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    const dropzone = $("notesDropzone");
    if (dropzone) {
      dropzone.focus();
    }
  });

  $("materialsUploadNewBtn")?.addEventListener("click", () => {
    if (isGenerating) return;
    showScreen("notesUploadSection");
  });

  $("removeModalCancelBtn")?.addEventListener("click", closeRemoveModal);
  $("removeModalConfirmBtn")?.addEventListener("click", confirmRemoveMaterial);

  // Close modal when clicking backdrop outside dialog
  $("materialRemoveModal")?.addEventListener("click", (e) => {
    if (e.target === $("materialRemoveModal")) {
      closeRemoveModal();
    }
  });

  // Close modal on Escape
  if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && $("materialRemoveModal") && !$("materialRemoveModal").classList.contains("hidden")) {
        closeRemoveModal();
      }
    });
  }
}

// ── App Initialization ──────────────────────────────────────────────────────
async function initApp() {
  await ensureAuthSession();
  await loadCourses();
  initNotesUploaderListeners();
  initStudyMaterialsListeners();
  const restored = restoreSessionState();

  // Initialize base history state if not already present
  if (typeof window !== "undefined" && window.history && !window.history.state) {
    try {
      window.history.replaceState({ screen: restored ? "quizSection" : "setup" }, "");
    } catch (e) {
      // Ignore if sandbox restricts replaceState
    }
  }

  // If no active quiz was restored, check URL parameters
  if (!restored && typeof window !== "undefined" && window.location && window.location.search) {
    try {
      const params = new URLSearchParams(window.location.search);
      const urlCourse = params.get("course");
      const urlTopic = params.get("topic");
      const urlAction = params.get("action");

      if (urlCourse && urlTopic) {
        if (urlAction === "practice") {
          selectAndPracticeTopic(urlCourse, urlTopic);
        } else {
          const courseSelect = $("course");
          const topicSelect = $("topic");
          if (courseSelect && (curriculum || []).some(c => (c.code || "").toLowerCase() === urlCourse.trim().toLowerCase())) {
            courseSelect.value = urlCourse;
            if (typeof Event !== "undefined") {
              courseSelect.dispatchEvent(new Event("change"));
            }
            if (topicSelect) {
              topicSelect.value = urlTopic;
            }
          }
        }
      }
    } catch (e) {
      console.warn("Failed to parse navigation parameters:", e);
    }
  }
}

// ── Browser Popstate Navigation ─────────────────────────────────────────────
if (typeof window !== "undefined" && window.addEventListener) {
  window.addEventListener("popstate", (e) => {
    const targetScreen = (e.state && e.state.screen) || lastActiveScreen || "setup";
    if (isGenerating && targetScreen !== "loadingSection") {
      if (activeAbortController) {
        activeAbortController.abort();
        activeAbortController = null;
      }
      isGenerating = false;
    }
    showScreen(targetScreen, false);
    if (targetScreen === "dashboardSection") {
      loadDashboard();
    }
  });
}

if (typeof window !== "undefined" && typeof document !== "undefined" && typeof require === "undefined") {
  initApp();
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    getApiBaseUrl,
    apiUrl,
    isTokenExpired,
    escapeHtml,
    LearnAICodeRenderer
  };
}
