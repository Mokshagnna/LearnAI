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

    // Standalone frontend development servers (e.g. VS Code Live Server on 5500, Vite/CRA on 3000/5173,
    // or an explicitly configured dev server flag) route API requests to the standalone FastAPI server.
    const isExplicitDevServer = Boolean(
      (win && win.LEARNAI_DEV_SERVER) ||
      (win && win.localStorage && (function() { try { return win.localStorage.getItem("learnai_dev_server") === "1" || win.localStorage.getItem("learnai_dev_server") === "true"; } catch (_) { return false; } })())
    );
    const KNOWN_FRONTEND_DEV_PORTS = ["5500", "5501", "3000", "5173", "4173"];
    const isKnownDevPort = KNOWN_FRONTEND_DEV_PORTS.indexOf(port) !== -1;

    if (isLocalhost && (isExplicitDevServer || isKnownDevPort)) {
      return "http://127.0.0.1:8000";
    }
  }

  // When served directly by FastAPI (e.g. port 8000, isolated test port 8001, or any custom port)
  // or in production (e.g. Render / custom domains), use same-origin relative paths (empty base prefix).
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
const CANONICAL_TOPIC_ALIASES_JS = {
  "bptt": "Backpropagation through Time",
  "back propagation through time": "Backpropagation through Time",
  "backpropagation through time bptt": "Backpropagation through Time",
  "bptt algorithm": "Backpropagation through Time",
  "lstm": "Long Short Term Memory (LSTM)",
  "long short term memory": "Long Short Term Memory (LSTM)",
  "long short term memory lstm": "Long Short Term Memory (LSTM)",
  "long short term memory networks": "Long Short Term Memory (LSTM)",
  "lstm networks": "Long Short Term Memory (LSTM)",
  "lstm network": "Long Short Term Memory (LSTM)",
  "lstm cell": "Long Short Term Memory (LSTM)",
  "gru": "Gated Recurrent Units",
  "gated recurrent unit": "Gated Recurrent Units",
  "gated recurrent units gru": "Gated Recurrent Units",
  "gru networks": "Gated Recurrent Units",
  "gru cell": "Gated Recurrent Units",
  "architecture of an rnn": "Architecture of an RNN",
  "rnn architecture": "Architecture of an RNN",
  "basic rnn structure": "Architecture of an RNN",
  "recurrent neural network architecture": "Architecture of an RNN",
  "structure of an rnn": "Architecture of an RNN",
  "applications text classification": "Applications- Text Classification",
  "text classification": "Applications- Text Classification",
  "sentiment analysis": "Sentiment Analysis",
  "sgd": "Stochastic Gradient Descent",
  "stochastic gradient descent": "Stochastic Gradient Descent",
  "stochastic gradient descent method": "Stochastic Gradient Descent",
  "dutch national flag": "Algorithm, Pseudo-Code for Expressing Algorithms, Performance Analysis - Space and Time Complexity",
  "dutch national flag problem": "Algorithm, Pseudo-Code for Expressing Algorithms, Performance Analysis - Space and Time Complexity",
  "dnf problem": "Algorithm, Pseudo-Code for Expressing Algorithms, Performance Analysis - Space and Time Complexity",
  "three way partitioning": "Algorithm, Pseudo-Code for Expressing Algorithms, Performance Analysis - Space and Time Complexity",
  "3 way partitioning": "Algorithm, Pseudo-Code for Expressing Algorithms, Performance Analysis - Space and Time Complexity",
  "greedy method": "Greedy Method",
  "greedy approach": "Greedy Method",
  "greedy algorithm": "Greedy Method",
  "fractional knapsack": "Greedy Method",
  "fractional knapsack problem": "Greedy Method"
};

let curriculum = [];
let currentQuiz = null;
let currentIndex = 0;
let answers = [];
let activeCourseCode = "";
let activeTopicName = "";
let activeTopicId = "";
let activeNotesSessionId = null;
let isGenerating = false;
// generationScreenOwner is set when quiz generation starts and cleared when the
// user navigates away to an unrelated screen (Dashboard, Notes, Materials). It
// determines whether the finished quiz should be auto-shown or left in memory
// for in-memory resume via handlePracticeNav.
let generationScreenOwner = null;
let activeAbortController = null;
let studentId = (typeof localStorage !== "undefined" && localStorage.getItem) ? (localStorage.getItem("learnai_student_id") || null) : null;
let authToken = (typeof localStorage !== "undefined" && localStorage.getItem) ? (localStorage.getItem("learnai_auth_token") || null) : null;
let authSessionPromise = null;
// ── Quiz lifecycle status ────────────────────────────────────────────────
// idle: no quiz (show topic selection)
// generating: quiz generation in flight
// displayed: questions rendered, zero answers saved (NOT "in progress")
// in_progress: at least one answer saved — restorable across refresh/tabs
// completed: quiz submitted — cleared everywhere
let quizStatus = "idle";
// Storage version for staleness/cleanup. Bump when storage shape changes so
// old learnai_active_quiz values (e.g. zero-answer phantom state) are ignored.
const QUIZ_STORAGE_VERSION = 2;
// Staleness window aligned with backend quiz_sessions.expires_at (24 hours / 86400s).
const QUIZ_STORAGE_TTL_MS = 24 * 60 * 60 * 1000;
// Guard against double-submit on the Submit Quiz button.
let submitInFlight = false;
var expandedReviewCourseId = null;
var lastDashboardData = null;
let lastCompletedResult = null;

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
  if (currentUser && currentUser.student_id) {
    studentId = currentUser.student_id;
  } else if (!studentId && typeof localStorage !== "undefined" && localStorage.getItem) {
    studentId = localStorage.getItem("learnai_student_id") || null;
  }
  if (!authToken && typeof localStorage !== "undefined" && localStorage.getItem) {
    authToken = localStorage.getItem("learnai_auth_token") || null;
  }

  const hasRegisteredSession = Boolean(
    (currentUser && currentUser.student_id) ||
    (typeof localStorage !== "undefined" && localStorage.getItem("learnai_session_active"))
  );

  // If we already have a valid unexpired token and are not forcing renewal, return it
  if (!forceRefresh && authToken && studentId && !isTokenExpired(authToken)) {
    return { token: authToken, studentId: studentId };
  }

  // If a registered session is active and studentId exists, do not mint an anonymous identity
  if (hasRegisteredSession && studentId && !forceRefresh) {
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
          headers: headers,
          credentials: "include"
        });
        if (res.ok) {
          const data = await res.json();
          // Never overwrite a registered student ID with a minted anonymous UUID
          const hasRegisteredIdentity = Boolean(
            (currentUser && currentUser.student_id && String(currentUser.student_id).startsWith("stu_")) ||
            (studentId && String(studentId).startsWith("stu_")) ||
            (typeof localStorage !== "undefined" && localStorage.getItem("learnai_session_active") === "1") ||
            (typeof localStorage !== "undefined" && (localStorage.getItem("learnai_student_id") || "").startsWith("stu_"))
          );
          if (!hasRegisteredIdentity) {
            studentId = data.student_id;
            if (typeof localStorage !== "undefined" && localStorage.setItem) {
              localStorage.setItem("learnai_student_id", studentId);
            }
          }
          authToken = data.token;
          if (typeof localStorage !== "undefined" && localStorage.setItem) {
            localStorage.setItem("learnai_auth_token", authToken);
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
  options.credentials = options.credentials || "include";
  let res = await fetch(resolvedUrl, options);
  if (res.status === 401) {
    console.warn("[AUTH] 401 Unauthorized received. Session expired or revoked.");
    currentUser = null;
    authToken = null;
    if (typeof localStorage !== "undefined" && localStorage.removeItem) {
      localStorage.removeItem("learnai_auth_token");
      localStorage.removeItem("learnai_session_active");
      localStorage.removeItem("learnai_student_id");
      localStorage.removeItem("learnai_user_name");
    }
    clearSessionState();
    if (typeof document !== "undefined" && document.documentElement) {
      document.documentElement.classList.add("unauthenticated");
    }
    showScreen("loginSection");
    showAuthMessage("loginMsg", "Your session has expired or was revoked. Please log in again.");
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
// ── Mathematical Typesetting & KaTeX Integration ──────────────────────────────
const LearnAIMathRenderer = {
  currencyRegex: /(?<!\\)\$(?=\d+(?:,\d{3})*(?:\.\d+)?(?:\s*(?:million|billion|trillion|[kKmMbB]|USD|usd|cents?|dollars?))?(?!\s*[+\-*/=^<>_\\~])(?:[.,;!?)\s]|$))/g,

  mathRegex: /(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\begin\{([a-zA-Z*]+)\}[\s\S]+?\\end\{\2\}|\\\([\s\S]+?\\\)|\$(?!\s)[^$\n]+?(?<!\s)\$)/g,

  ignoredTags: ["SCRIPT", "STYLE", "TEXTAREA", "PRE", "CODE", "BUTTON", "INPUT", "SELECT", "OPTION"],
  ignoredClasses: [
    "inline-code",
    "code-pre",
    "code-content",
    "option-code",
    "code-block-container",
    "code-lang-badge",
    "code-copy-btn",
    "token-keyword",
    "token-string",
    "token-comment"
  ],

  hasMath(text) {
    if (!text || typeof text !== "string") return false;
    if (/\\(?:frac|sum|int|sqrt|alpha|beta|gamma|delta|lambda|theta|sigma|omega|pi|mu|partial|nabla|infty|times|div|pm|le|ge|neq|approx|equiv|forall|exists|in|subset|cap|cup|vec|mathbf|mathcal|text|det|lim|log|ln|sin|cos|tan)\b/.test(text)) {
      return true;
    }
    if (/\\\(|\\\[|\$\$|\\begin\{[a-zA-Z*]+\}/.test(text)) {
      return true;
    }
    if (typeof this._hasPlainTextMath === "function" && this._hasPlainTextMath(text)) {
      return true;
    }
    const tokens = this.tokenize(text);
    return tokens.some(t => t.type === "math");
  },

  escapeCurrency(text) {
    if (!text || typeof text !== "string") return "";
    return text.replace(this.currencyRegex, "\\$");
  },

  tokenize(text) {
    if (!text || typeof text !== "string") return [];

    const currencies = [];
    const safeText = text.replace(this.currencyRegex, (m) => {
      const ph = `___CURR_${currencies.length}___`;
      currencies.push(m);
      return ph;
    });

    const tokens = [];
    let lastIndex = 0;
    let match;

    const regex = new RegExp(this.mathRegex.source, "g");
    while ((match = regex.exec(safeText)) !== null) {
      if (match.index > lastIndex) {
        tokens.push({
          type: "text",
          raw: safeText.slice(lastIndex, match.index)
        });
      }

      const rawMatch = match[0];
      let mathContent = "";
      let display = false;

      if (rawMatch.startsWith("$$") && rawMatch.endsWith("$$")) {
        mathContent = rawMatch.slice(2, -2).trim();
        display = true;
      } else if (rawMatch.startsWith("\\[") && rawMatch.endsWith("\\]")) {
        mathContent = rawMatch.slice(2, -2).trim();
        display = true;
      } else if (rawMatch.startsWith("\\begin{")) {
        mathContent = rawMatch.trim();
        display = true;
      } else if (rawMatch.startsWith("\\(") && rawMatch.endsWith("\\)")) {
        mathContent = rawMatch.slice(2, -2).trim();
        display = false;
      } else if (rawMatch.startsWith("$") && rawMatch.endsWith("$")) {
        mathContent = rawMatch.slice(1, -1).trim();
        display = false;
      }

      tokens.push({
        type: "math",
        display,
        content: mathContent,
        raw: rawMatch
      });

      lastIndex = match.index + rawMatch.length;
    }

    if (lastIndex < safeText.length) {
      tokens.push({
        type: "text",
        raw: safeText.slice(lastIndex)
      });
    }

    tokens.forEach(tok => {
      if (tok.type === "text") {
        currencies.forEach((c, i) => {
          tok.raw = tok.raw.replace(new RegExp(`___CURR_${i}___`, "g"), c);
        });
      }
    });

    return tokens;
  },

  renderToString(mathStr, displayMode = false) {
    const k = (typeof katex !== "undefined" ? katex : (typeof globalThis !== "undefined" && globalThis.katex ? globalThis.katex : (typeof window !== "undefined" && window.katex ? window.katex : null)));
    if (!k || typeof k.renderToString !== "function") {
      return escapeHtml(displayMode ? `\\[ ${mathStr} \\]` : `\\( ${mathStr} \\)`);
    }

    // Stage 1: Mathematical normalization of plain-text/programming notation into standard LaTeX
    const normalized = this.normalizeMathExpression(mathStr);

    try {
      return k.renderToString(normalized, {
        displayMode: Boolean(displayMode),
        throwOnError: false,
        errorColor: "#DC2626",
        strict: false
      });
    } catch (err) {
      return `<span class="katex-error" title="${escapeHtml(err.message)}">${escapeHtml(mathStr)}</span>`;
    }
  },

  // ── Stage 1: Mathematical Expression Normalizer ───────────────────────────
  // Unicode superscripts map
  _superscriptsMap: {
    '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
    '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9',
    '⁺': '+', '⁻': '-', '⁼': '=', '⁽': '(', '⁾': ')'
  },

  normalizeSuperscripts(str) {
    if (!str || typeof str !== 'string') return str;
    return str.replace(/([a-zA-Z0-9_\)\]\|])([\u2070\u00B9\u00B2\u00B3\u2074-\u2079\u207A\u207B\u207C\u207D\u207E]+)/g, (m, base, sups) => {
      let exp = '';
      for (const ch of sups) {
        exp += this._superscriptsMap[ch] || ch;
      }
      return `${base}^{${exp}}`;
    });
  },

  // Parses piecewise function syntax into LaTeX cases environments
  parsePiecewise(text) {
    if (!text || typeof text !== 'string') return null;
    const trimmed = text.trim();

    // Guard: Must not look like prose or contain prose connectives
    if (this._looksLikeProse(trimmed)) return null;
    if (/\b(?:and|or|transition|transitions|behave|behaves|because|therefore)\b/i.test(trimmed)) return null;

    // Must contain if, for, or when with at least two branches
    if (!/\b(?:if|for|when)\b/i.test(trimmed)) return null;

    // Check for LHS: e.g. L_delta(e) = ... or f(x) = ...
    const eqMatch = trimmed.match(/^([a-zA-Z_]\w*(?:\([^)]*\))?)\s*=\s*(.+)$/s);
    let lhs = '';
    let rhs = trimmed;
    if (eqMatch) {
      lhs = eqMatch[1].trim();
      rhs = eqMatch[2].trim();
    } else {
      if (!rhs.includes(';') && !rhs.includes('\n') && !(rhs.startsWith('{') && rhs.endsWith('}'))) {
        return null;
      }
    }

    if (rhs.startsWith('{') && rhs.endsWith('}')) {
      rhs = rhs.slice(1, -1).trim();
    }

    let branches = rhs.split(/;\s*|\n+/).map(b => b.trim()).filter(Boolean);
    if (branches.length < 2) {
      const commaBranches = rhs.split(/,\s*(?=[^,]+?\b(?:if|for|when)\b)/i).map(b => b.trim()).filter(Boolean);
      if (commaBranches.length >= 2 && !commaBranches.some(b => this._looksLikeProse(b))) {
        branches = commaBranches;
      }
    }

    if (branches.length < 2) return null;

    const rows = [];
    for (const b of branches) {
      const parts = b.split(/\s+\b(?:if|for|when)\b\s+/i);
      if (parts.length !== 2) return null;
      if (this._looksLikeProse(parts[0]) || this._looksLikeProse(parts[1])) return null;
      const val = this.normalizeMathExpression(parts[0].trim());
      const cond = this.normalizeMathExpression(parts[1].trim());
      rows.push(`${val} & ${cond}`);
    }

    const casesLatex = `\\begin{cases} ${rows.join(' \\\\ ')} \\end{cases}`;
    if (lhs) {
      const normLhs = this.normalizeMathExpression(lhs);
      return `\\[ ${normLhs} = ${casesLatex} \\]`;
    }
    return `\\[ ${casesLatex} \\]`;
  },

  // Converts programming-style and plain-text mathematical expressions into
  // standard mathematically correct LaTeX syntax
  normalizeMathExpression(expr) {
    if (!expr || typeof expr !== "string") return expr;
    let s = expr.trim();
    if (!s) return s;

    // Pre-normalize unicode superscripts
    s = this.normalizeSuperscripts(s);
    // Pre-normalize exponent parentheses: ^(expr) -> ^{expr} and ^-x -> ^{-x}
    s = s.replace(/\^\(([^()]+)\)/g, '^{$1}');
    s = s.replace(/\^-\s*([a-zA-Z0-9]+)/g, '^{-$1}');

    // Helper: Find matching closing parenthesis, bracket, or brace
    function findMatching(str, openIdx) {
      const openChar = str[openIdx];
      const closeChar = openChar === '(' ? ')' : openChar === '[' ? ']' : openChar === '{' ? '}' : null;
      if (!closeChar) return -1;
      let depth = 1;
      for (let i = openIdx + 1; i < str.length; i++) {
        const c = str[i];
        if (c === openChar) depth++;
        else if (c === closeChar) {
          depth--;
          if (depth === 0) return i;
        }
      }
      return -1;
    }

    // Helper: Split by top-level separator (commas outside nested parens)
    function splitTopLevel(str, sep = ',') {
      const parts = [];
      let depth = 0;
      let last = 0;
      for (let i = 0; i < str.length; i++) {
        const c = str[i];
        if (c === '(' || c === '[' || c === '{') depth++;
        else if (c === ')' || c === ']' || c === '}') depth--;
        else if (c === sep && depth === 0) {
          parts.push(str.slice(last, i).trim());
          last = i + 1;
        }
      }
      parts.push(str.slice(last).trim());
      return parts;
    }

    // Normalize function signatures on LHS (e.g. L(y, s) -> L(y,s), f(x, y) -> f(x,y))
    s = s.replace(/\b([a-zA-Z][a-zA-Z0-9_]*)\s*\(([a-zA-Z0-9_,\s]+)\)/g, (match, fnName, args) => {
      const mathFns = ['max', 'min', 'exp', 'log', 'ln', 'sin', 'cos', 'tan', 'sqrt', 'abs', 'sigmoid', 'softmax', 'relu'];
      if (mathFns.includes(fnName.toLowerCase())) return match;
      const cleanArgs = args.split(',').map(a => a.trim()).join(',');
      return `${fnName}(${cleanArgs})`;
    });

    // Greek letters normalization: (?<=^|[^a-zA-Z\\]) so _delta becomes _\delta
    const greekPattern = /(?<=^|[^a-zA-Z\\])(alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|mu|nu|xi|rho|sigma|tau|upsilon|phi|chi|psi|omega|Alpha|Beta|Gamma|Delta|Epsilon|Theta|Lambda|Mu|Pi|Sigma|Phi|Psi|Omega)(?=[^a-zA-Z]|$)/g;
    const greekMap = {
      alpha:"\\alpha",beta:"\\beta",gamma:"\\gamma",delta:"\\delta",
      epsilon:"\\epsilon",zeta:"\\zeta",eta:"\\eta",theta:"\\theta",
      iota:"\\iota",kappa:"\\kappa",lambda:"\\lambda",mu:"\\mu",
      nu:"\\nu",xi:"\\xi",rho:"\\rho",sigma:"\\sigma",tau:"\\tau",
      upsilon:"\\upsilon",phi:"\\phi",chi:"\\chi",psi:"\\psi",
      omega:"\\omega",Alpha:"\\Alpha",Beta:"\\Beta",Gamma:"\\Gamma",
      Delta:"\\Delta",Epsilon:"\\Epsilon",Theta:"\\Theta",
      Lambda:"\\Lambda",Mu:"\\Mu",Pi:"\\Pi",Sigma:"\\Sigma",
      Phi:"\\Phi",Psi:"\\Psi",Omega:"\\Omega"
    };
    s = s.replace(greekPattern, (m) => greekMap[m] || m);

    // Transform known mathematical functions
    function transformFunctions(str) {
      let res = '';
      let i = 0;
      while (i < str.length) {
        const fnMatch = str.slice(i).match(/^\\?(max|min|exp|log|ln|sqrt|abs|sin|cos|tan|sinh|cosh|tanh|arcsin|arccos|arctan|det|lim|argmax|argmin|softmax|sigmoid|relu)\b\s*\(/i);
        if (fnMatch) {
          const fnName = fnMatch[1].toLowerCase();
          const openParenIdx = i + fnMatch[0].length - 1;
          const closeParenIdx = findMatching(str, openParenIdx);
          if (closeParenIdx !== -1) {
            const innerContent = str.slice(openParenIdx + 1, closeParenIdx);

            if (fnName === 'max' || fnName === 'min') {
              const args = splitTopLevel(innerContent, ',').map(a => transformFunctions(normalizeSubExpr(a)));
              res += '\\' + fnName + '\\{' + args.join(',') + '\\}';
            } else if (fnName === 'exp') {
              const innerNorm = transformFunctions(normalizeSubExpr(innerContent));
              res += `e^{${innerNorm}}`;
            } else if (fnName === 'sqrt') {
              const innerNorm = transformFunctions(normalizeSubExpr(innerContent));
              res += `\\sqrt{${innerNorm}}`;
            } else if (fnName === 'abs') {
              const innerNorm = transformFunctions(normalizeSubExpr(innerContent));
              res += `\\left|${innerNorm}\\right|`;
            } else if (['log', 'ln'].includes(fnName)) {
              const innerNorm = transformFunctions(normalizeSubExpr(innerContent));
              res += `\\${fnName}(${innerNorm})`;
            } else if (['sin', 'cos', 'tan', 'sinh', 'cosh', 'tanh', 'arcsin', 'arccos', 'arctan', 'det', 'lim'].includes(fnName)) {
              const innerNorm = transformFunctions(normalizeSubExpr(innerContent));
              res += `\\${fnName}(${innerNorm})`;
            } else {
              const innerNorm = transformFunctions(normalizeSubExpr(innerContent));
              res += `\\operatorname{${fnName}}(${innerNorm})`;
            }
            i = closeParenIdx + 1;
            continue;
          }
        }
        res += str[i];
        i++;
      }
      return res;
    }

    // Transform division 'a / b' into '\frac{a}{b}'
    function transformFractions(str) {
      if (str.includes('/')) {
        // Skip dates, URLs, Unix paths, and units
        if (/\b\d{1,2}\/\d{1,2}\/\d{2,4}\b/.test(str)) return str;
        if (/https?:\/\//.test(str)) return str;
        if (/^\/[a-zA-Z0-9_.-]+/.test(str)) return str;
        if (/\b(km\/h|m\/s|MB\/s|GB\/s)\b/i.test(str)) return str;

        // 1 / (1 + e^{-x}) -> \frac{1}{1+e^{-x}}
        str = str.replace(/(?:^|(?<=[=+\-*\s(]))([a-zA-Z0-9_.]+|\\?[a-zA-Z]+)\s*\/\s*\(([^()]+)\)/g, (match, num, den) => {
          return `\\frac{${num}}{${den}}`;
        });
        // (a) / (b) -> \frac{a}{b}
        str = str.replace(/\(([^()]+)\)\s*\/\s*\(([^()]+)\)/g, (match, num, den) => {
          return `\\frac{${num}}{${den}}`;
        });
        // (a) / b -> \frac{a}{b}
        str = str.replace(/\(([^()]+)\)\s*\/\s*([a-zA-Z0-9_.]+|\\?[a-zA-Z]+)/g, (match, num, den) => {
          return `\\frac{${num}}{${den}}`;
        });
        // a / b -> \frac{a}{b} (for short alphanumeric variables or numbers)
        str = str.replace(/(?:^|(?<=[=+\-*\s(]))([a-zA-Z0-9_.]+|\\?[a-zA-Z]+)\s*\/\s*([a-zA-Z0-9_.]+|\\?[a-zA-Z]+)(?=$|[=+\-*\s),;])/g, (match, num, den) => {
          return `\\frac{${num}}{${den}}`;
        });
      }
      return str;
    }

    function normalizeSubExpr(sub) {
      // 0.5 * -> \frac{1}{2} (and 1/2 * -> \frac{1}{2})
      sub = sub.replace(/(^|[^a-zA-Z0-9_\.])0\.5\s*\*?\s*(?=[a-zA-Z0-9_\.\\|(\[])/g, '$1\\frac{1}{2}');
      sub = sub.replace(/(^|[^a-zA-Z0-9_\.])1\s*\/\s*2\s*\*?\s*(?=[a-zA-Z0-9_\.\\|(\[])/g, '$1\\frac{1}{2}');

      // Relational operators
      sub = sub.replace(/(?<![<>!])<=(?![=])/g, '\\leq');
      sub = sub.replace(/(?<![<>])>=(?![=])/g, '\\geq');
      sub = sub.replace(/!=(?![=])/g, '\\neq');
      sub = sub.replace(/\b(infinity|infty)\b/gi, '\\infty');

      // Absolute values: |e| -> \left|e\right|
      sub = sub.replace(/(?:\\left\||\\right\||\|([^|\n]+)\|)/g, (match, inner) => {
        if (match.startsWith('\\left') || match.startsWith('\\right')) {
          return match;
        }
        return `\\left|${inner}\\right|`;
      });

      // Multiplications:
      // a * b -> ab (letters/subscripts/commands)
      sub = sub.replace(/(\b[a-zA-Z_]\w*|\\(?:[a-zA-Z]+))\s*\*\s*(\b[a-zA-Z_]\w*|\\(?:[a-zA-Z]+))/g, '$1$2');
      // number * variable -> 2x
      sub = sub.replace(/(\b\d+(?:\.\d+)?)\s*\*\s*(\b[a-zA-Z_]\w*|\\(?:[a-zA-Z]+))/g, '$1$2');
      // term * (expr) -> term\left(expr\right) or term(expr)
      sub = sub.replace(/(\b[a-zA-Z0-9_\.]+|\\(?:[a-zA-Z]+)|\))\s*\*\s*\(([^()]+)\)/g, (m, left, inner) => {
        if (inner.includes('\\frac') || inner.includes('\\left') || inner.includes('|')) {
          return `${left}\\left(${inner}\\right)`;
        }
        return `${left}(${inner})`;
      });
      // (expr) * term -> (expr)term
      sub = sub.replace(/\(([^()]+)\)\s*\*\s*(\b[a-zA-Z_]\w*|\\(?:[a-zA-Z]+))/g, '($1)$2');
      // (expr) * (expr) -> (expr)(expr)
      sub = sub.replace(/\(([^()]+)\)\s*\*\s*\(([^()]+)\)/g, '($1)($2)');
      // number * number -> 2 \cdot 3
      sub = sub.replace(/(\b\d+)\s*\*\s*(\b\d+)/g, '$1 \\cdot $2');

      // Exponents grouping
      sub = sub.replace(/\^\(([^()]+)\)/g, '^{$1}');
      sub = sub.replace(/\^-\s*([a-zA-Z0-9]+)/g, '^{-$1}');

      // Clean whitespace around binary + and - inside terms
      sub = sub.replace(/\s*([\+\-])\s*/g, (match, op, offset) => {
        const prevChar = offset === 0 ? '' : sub[offset - 1];
        if (op === '-' && (offset === 0 || '(=[{,'.includes(prevChar))) {
          return '-';
        }
        return op;
      });

      sub = sub.trim();
      return sub;
    }

    // Pipeline
    s = transformFunctions(s);
    s = transformFractions(s);
    s = normalizeSubExpr(s);

    // Equal sign normalization
    if (s.includes('=')) {
      const parts = s.split('=').map(p => p.trim());
      if (parts.length === 2) {
        s = `${parts[0]}=${parts[1]}`;
      }
    }

    return s;
  },

  // ── Plain-text math auto-detection ─────────────────────────────────────────
  convertPlainTextMath(text) {
    if (!text || typeof text !== "string") return text;
    if (text.length < 3) return text;

    // Skip URLs, emails
    if (/https?:\/\/|ftp:\/\/|www\.|@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/.test(text)) return text;
    // Skip backtick code
    if (/`[^`]+`/.test(text)) return text;
    // Skip strings starting like version numbers
    if (/^\d+\.\d+\.\d+/.test(text.trim())) return text;

    const trimmed = text.trim();

    // Check 1: Piecewise function definition covering the expression
    const piecewise = this.parsePiecewise(trimmed);
    if (piecewise) {
      return piecewise;
    }

    // Check 2: Full standalone mathematical expression
    const isFullMathExpr = (
      /^[a-zA-Z_][a-zA-Z_0-9]*(?:\([a-zA-Z_0-9, ]*\))?\s*(?:=|<=|>=|!=|<|>)\s*.+$/.test(trimmed) ||
      /^P\([^)]+\)\s*=\s*.+$/.test(trimmed) ||
      /^(?:max|min|exp|log|ln|sqrt|abs|sin|cos|tan|sigmoid|softmax|relu)\s*\(.+\)$/i.test(trimmed) ||
      /^\([^)]+\)(?:\s*[\^\/\*\+\-]\s*.+)?$/.test(trimmed) ||
      /^(?:[a-zA-Z0-9_\.]+|\([^)]+\))\s*\/\s*.+$/.test(trimmed) ||
      /^[-+]?[a-zA-Z0-9_\.]+\s*[\*\^\/]\s*.+$/.test(trimmed)
    ) && !this._looksLikeProse(trimmed);

    if (isFullMathExpr) {
      let exprToNorm = trimmed;
      if (exprToNorm.startsWith("\\(") && exprToNorm.endsWith("\\)")) {
        exprToNorm = exprToNorm.slice(2, -2).trim();
      } else if (exprToNorm.startsWith("\\[") && exprToNorm.endsWith("\\]")) {
        exprToNorm = exprToNorm.slice(2, -2).trim();
      }
      return "\\(" + this.normalizeMathExpression(exprToNorm) + "\\)";
    }

    // Pre-normalize unicode superscripts in text (e.g. e² -> e^{2})
    const preprocessed = this.normalizeSuperscripts(text);

    // Skip if already contains LaTeX markup in mixed prose
    if (preprocessed.includes("\\") || /\\\(|\\\[|\$\$|\\begin\{/.test(preprocessed)) return text;

    const inlinePatterns = [
      // 1. Embedded equations: L(y, s) = max(...) or y = 2*x + 1
      /\b[a-zA-Z_][a-zA-Z_0-9]*(?:\([a-zA-Z_0-9, ]+\))?\s*=\s*(?:max|min|exp|log|ln|sqrt|abs|sin|cos|tan)?\s*[^\n,;.?!]{2,60}/g,
      // 2. Expressions with multiplication: delta * (|e| - 0.5 * delta), 0.5 * e^{2}, 2 * x, a * b
      /(?:[-+]?\d+(?:\.\d+)?|\\?[a-zA-Z_]\w*|\([^)]+\))\s*\*\s*(?:\([^)]+\)|\|[^|]+\||[-+]?\d+(?:\.\d+)?|\\?[a-zA-Z_]\w*(?:\^[a-zA-Z0-9\-+{}]+)?)/g,
      // 3. Inequalities: |e| <= delta, |e| > delta, x >= 0
      /(?:\|[^|\n,;()]+\||[a-zA-Z_]\w*(?:\^[0-9a-zA-Z\-+{}]+|_[0-9a-zA-Z\-+{}]+)?|\d+(?:\.\d+)?)\s*(?:<=|>=|!=|<|>)\s*(?:\|[^|\n,;()]+\||[a-zA-Z_]\w*(?:\^[0-9a-zA-Z\-+{}]+|_[0-9a-zA-Z\-+{}]+)?|\d+(?:\.\d+)?)/g,
      // 4. Standalone absolute values: |e|, |x - y|
      /\|[-+]?[a-zA-Z0-9_\.]+(?:\s*[-+*/]\s*[-+]?[a-zA-Z0-9_\.]+)*\|/g,
      // 5. Function calls: max(...), exp(...)
      /\b(?:sin|cos|tan|sinh|cosh|tanh|arcsin|arccos|arctan|log|ln|exp|sqrt|abs|sigmoid|softmax|relu|max|min|gcd|lcm|det|tr|rank)\s*\([^)\n]{1,60}\)/gi,
      // 6. Parenthesized expressions with exponents or division
      /\([^)]+\)\s*[\^\/]\s*(?:[a-zA-Z0-9\-+{}]+|\([^)]+\))/g,
      // 7. Exponents: x^2, e^{-x}, a^(n+1)
      /[a-zA-Z0-9)]\^[\{(]?[a-zA-Z0-9\-+][a-zA-Z0-9\-+{}() ]*[\})]?/g,
      // 8. Subscripts: x_1, a_n, L_delta
      /[a-zA-Z][_][{]?[a-zA-Z0-9+\-][a-zA-Z0-9+\-{}]*[}]?/g,
      // 9. Fractions: a/b, 1/2
      /(?<![a-zA-Z0-9_\/])(?:\d+|[a-zA-Z](?:\^\d+)?)\s*\/\s*(?:\d+|[a-zA-Z](?:\^\d+)?)(?![a-zA-Z0-9_\/])/g,
      // 10. Big-O: O(n)
      /\bO\([^)\n]{1,30}\)/g,
      // 11. Greek letter words: alpha, beta, etc.
      /\b(?:alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|mu|nu|xi|rho|sigma|tau|upsilon|phi|chi|psi|omega|Alpha|Beta|Gamma|Delta|Epsilon|Theta|Lambda|Mu|Pi|Sigma|Phi|Psi|Omega)\b/g
    ];

    const matched = [];
    for (const pattern of inlinePatterns) {
      const re = new RegExp(pattern.source, pattern.flags || "g");
      let m;
      while ((m = re.exec(preprocessed)) !== null) {
        const start = m.index;
        const end = m.index + m[0].length;
        if (matched.some(r => start < r.end && end > r.start)) continue;
        if (start > 0 && preprocessed[start - 1] === "$") continue;
        matched.push({ start, end, content: m[0] });
      }
    }

    if (matched.length === 0) return text;
    matched.sort((a, b) => a.start - b.start);

    let out = "";
    let cursor = 0;
    for (const seg of matched) {
      out += preprocessed.slice(cursor, seg.start);
      out += "\\(" + this.normalizeMathExpression(seg.content) + "\\)";
      cursor = seg.end;
    }
    out += preprocessed.slice(cursor);
    return out;
  },

  _looksLikeProse(text) {
    const words = text.split(/\s+/).filter(w => /^[a-zA-Z]{3,}$/.test(w));
    const mathChars = (text.match(/[+\-*\/^_=<>|{}()\\]/g) || []).length;
    return words.length > 4 && mathChars < 3;
  },

  _hasPlainTextMath(text) {
    if (!text || text.length < 3) return false;
    return (
      /\bO\([^)\n]{1,30}\)/.test(text) ||
      /\b(?:sin|cos|tan|log|ln|exp|sqrt|sigmoid|softmax|relu|max|min|argmax|argmin|det|lim|abs)\s*\(/.test(text) ||
      /[a-zA-Z0-9)]\^[{(]?[a-zA-Z0-9\-+]/.test(text) ||
      /[a-zA-Z]_[{]?[a-zA-Z0-9]/.test(text) ||
      /\b(?:alpha|beta|gamma|delta|theta|lambda|sigma|omega|mu|pi)\b/.test(text) ||
      /(?:\d+|\w)\s*\/\s*(?:\d+|\w)(?![a-zA-Z]{2})/.test(text) ||
      /^[a-zA-Z_]\w*(?:\([^)]*\))?\s*=\s*.+$/.test(text.trim()) ||
      /^\([^)]+\)\s*[\^\/\*\+\-]/.test(text.trim()) ||
      /^[-+]?[a-zA-Z0-9_\.]+\s*[\*\^\/]/.test(text.trim()) ||
      /\|[^|\n]+\|/.test(text) ||
      /(?:<=|>=|!=)/.test(text) ||
      /[\u2070\u00B9\u00B2\u00B3\u2074-\u2079\u207A\u207B\u207C\u207D\u207E]/.test(text) ||
      /\b\d+(?:\.\d+)?\s*\*\s*[a-zA-Z]/.test(text)
    );
  },

  typeset(rootEl) {
    if (!rootEl) return;
    const k = (typeof katex !== "undefined" ? katex : (typeof globalThis !== "undefined" && globalThis.katex ? globalThis.katex : (typeof window !== "undefined" && window.katex ? window.katex : null)));
    if (!k) {
      if (typeof window !== "undefined") {
        setTimeout(() => this.typeset(rootEl), 100);
      }
      return;
    }

    const textNodes = [];
    const walk = (node) => {
      if (!node) return;
      if (node.nodeType === 3) {
        const tc = node.textContent || "";
        if (tc && (tc.includes("$") || tc.includes("\\") || this._hasPlainTextMath(tc))) {
          textNodes.push(node);
        }
        return;
      }
      if (node.nodeType === 1) {
        const tag = node.tagName.toUpperCase();
        if (this.ignoredTags.includes(tag)) return;
        const cls = " " + (node.className || "") + " ";
        if (this.ignoredClasses.some(c => cls.includes(" " + c + " "))) return;
        for (let child = node.firstChild; child; child = child.nextSibling) {
          walk(child);
        }
      }
    };

    walk(rootEl);

    for (const textNode of textNodes) {
      if (!textNode.parentNode) continue;
      const rawText = textNode.textContent;
      // Apply plain-text math conversion first, then tokenize
      const text = this.convertPlainTextMath(rawText);
      const tokens = this.tokenize(text);
      const hasMathToken = tokens.some(t => t.type === "math");
      if (!hasMathToken) continue;

      const fragment = document.createDocumentFragment();
      for (const tok of tokens) {
        if (tok.type === "text") {
          fragment.appendChild(document.createTextNode(tok.raw));
        } else if (tok.type === "math") {
          const renderedHtml = this.renderToString(tok.content, tok.display);
          const wrapper = document.createElement(tok.display ? "div" : "span");
          wrapper.className = tok.display ? "katex-display-container" : "katex-inline-wrapper";
          if (tok.display) {
            wrapper.setAttribute("tabindex", "0");
            wrapper.setAttribute("role", "region");
            wrapper.setAttribute("aria-label", "Mathematical equation, scrollable horizontally");
          }
          wrapper.innerHTML = renderedHtml;
          fragment.appendChild(wrapper);
        }
      }
      textNode.parentNode.replaceChild(fragment, textNode);
    }
  }
};

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

renderProse(rawText) {
    if (!rawText) return "";
    const paragraphs = String(rawText).trim().split(/\n{2,}/);

    return paragraphs.map(para => {
      const lines = para.split("\n");
      const result = [];
      let currentList = null;

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const bulletMatch = line.match(/^\s*[-*•]\s+(.*)$/);
        const numMatch = line.match(/^\s*(\d+)\.\s+(.*)$/);

        if (bulletMatch) {
          if (!currentList || currentList.type !== "ul") {
            if (currentList) result.push(this._flushList(currentList));
            currentList = { type: "ul", items: [] };
          }
          currentList.items.push(bulletMatch[1]);
        } else if (numMatch) {
          if (!currentList || currentList.type !== "ol") {
            if (currentList) result.push(this._flushList(currentList));
            currentList = { type: "ol", items: [] };
          }
          currentList.items.push(numMatch[2]);
        } else {
          if (currentList) {
            result.push(this._flushList(currentList));
            currentList = null;
          }
          if (line.trim()) {
            result.push(`<div class="question-prose">${this.renderInline(line)}</div>`);
          }
        }
      }
      if (currentList) {
        result.push(this._flushList(currentList));
      }
      return result.join("");
    }).join("");
  },

  _flushList(list) {
    const tag = list.type;
    const items = list.items.map(it => `<li>${this.renderInline(it)}</li>`).join("");
    return `<${tag} class="prose-list">${items}</${tag}>`;
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
        return this.renderProse(seg.content || "");
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

  _codeSnippetRegex: new RegExp(
    [
      // C/C++ I/O & functions
      '\\b(?:printf|scanf|puts|putchar)\\s*\\([^)]*\\);?',
      // C++ streams
      '\\bcout\\s*<<\\s*[^;]+;?',
      '\\bcin\\s*>>\\s*[^;]+;?',
      // Python print & common builtins
      '\\bprint\\s*\\([^)]*\\)',
      '\\blen\\s*\\([^)]*\\)',
      '\\brange\\s*\\([^)]*\\)',
      '\\binput\\s*\\([^)]*\\)',
      // Java System.out
      '\\bSystem\\.out\\.(?:println|print|printf)\\s*\\([^)]*\\);?',
      // JavaScript console
      '\\bconsole\\.(?:log|error|warn|info|debug)\\s*\\([^)]*\\);?',
      // SQL statements
      '\\bSELECT\\s+[\\s\\S]+?\\s+FROM\\s+[^;]+;?',
      '\\bINSERT\\s+INTO\\s+[^;]+;?',
      '\\bUPDATE\\s+[^;]+SET\\s+[^;]+;?',
      '\\bDELETE\\s+FROM\\s+[^;]+;?',
      // Variable declarations with assignments
      '\\b(?:int|float|double|char|bool|boolean|let|const|var)\\s+[*&]?[a-zA-Z_]\\w*\\s*=\\s*[^;]+;',
      // Function/method calls ending in semicolon
      '\\b[a-zA-Z_]\\w*(?:\\.[a-zA-Z_]\\w*)+\\s*\\([^)]*\\);?',
      '\\b[a-zA-Z_]\\w*\\s*\\([^)]*\\);',
      // Statements ending in semicolon with assignment/operators
      '\\b[a-zA-Z_]\\w*\\s*(?:\\+=|-=|\\*=|/=|%=|=)\\s*[^;]+;'
    ].map(p => `(${p})`).join('|'),
    'i'
  ),

  _entireCodeRegex: new RegExp(
    [
      '^\\s*(?:printf|scanf|puts|putchar|cin|cout)\\b',
      '^\\s*cout\\s*<<',
      '^\\s*cin\\s*>>',
      '^\\s*(?:print|input|len|range)\\s*\\(',
      '^\\s*(?:System\\.out\\.(?:print|println|printf)|console\\.(?:log|error|warn|info))\\s*\\(',
      '^\\s*(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|WITH)\\b',
      '^\\s*(?:int|float|double|char|long|short|void|bool|boolean|unsigned|const|let|var|auto)\\s+[*&]?[a-zA-Z_]\\w*',
      '^\\s*(?:def|class|return|import|from|#include|#define|public|private|protected)\\b',
      ';\\s*$',
      '^[a-zA-Z_]\\w*(?:\\.[a-zA-Z_]\\w*)*\\s*\\([^)]*\\)\\s*;?$',
      '^[a-zA-Z_]\\w*\\s*(?:\\+=|-=|\\*=|/=|%=|=)\\s*.+;?$'
    ].join('|'),
    'i'
  ),

  isQuestionCodeOrOutput(q) {
    if (!q) return false;
    const text = q.question || "";
    if (/```|\bprintf\b|\bscanf\b|\bprint\s*\(|%\d*d|%0\d+d|%-\d*d|%\.\d+f|%[sScpxX]|\\n|\\t|#include|\bSystem\.out\./i.test(text)) {
      return true;
    }
    const opts = q.options || {};
    for (const val of Object.values(opts)) {
      if (typeof val === "string") {
        if (/[ \t]{2,}/.test(val) || /^[ \t]+|[ \t]+$/.test(val) || /[\n\t]/.test(val) || this._entireCodeRegex.test(val.trim())) {
          return true;
        }
      }
    }
    return false;
  },

  isWhitespaceSensitiveOrCode(s) {
    if (!s || typeof s !== "string") return false;
    // Multiple consecutive horizontal spaces
    if (/[ \t]{2,}/.test(s)) return true;
    // Leading or trailing whitespace
    if (/^[ \t]+|[ \t]+$/.test(s)) return true;
    // Tabs or newlines
    if (/[\n\t]/.test(s)) return true;
    // Backtick inline code
    if (/`[^`\n]+`/.test(s)) return true;
    // Semicolon ending
    if (/;\s*$/.test(s)) return true;
    // Variable assignment
    if (/^[a-zA-Z_]\w*\s*=\s*.+$/.test(s)) return true;
    // Entire code match
    if (this._entireCodeRegex.test(s.trim())) return true;
    // Format specifier tokens
    if (/%[-+0-9.*]*[diufoxXcspeEgp]/.test(s)) return true;
    return false;
  },

  renderOptionToHtml(optionValue, renderOptions = {}) {
    if (optionValue === null || optionValue === undefined) return "";
    const s = String(optionValue);
    if (!s) return "";

    // Math check: If option contains math delimiters and/or LaTeX syntax, preserve for typesetting
    if (typeof LearnAIMathRenderer !== "undefined" && LearnAIMathRenderer.hasMath(s)) {
      return this.renderInline(s);
    }

    // 1. Multiline code in option: use light grey code block <pre class="option-pre">
    if (s.indexOf("\n") !== -1 && (this.isWhitespaceSensitiveOrCode(s) || this._entireCodeRegex.test(s) || s.indexOf("```") !== -1)) {
      let codeContent = s;
      const fenceMatch = s.match(/^```[a-zA-Z_-]*\n([\s\S]*?)\n```$/);
      if (fenceMatch) {
        codeContent = fenceMatch[1];
      }
      return `<pre class="option-pre"><code class="option-code">${escapeHtml(codeContent)}</code></pre>`;
    }

    // 2. Explicit backticks: render inline code snippets
    if (/`[^`\n]+`/.test(s)) {
      return this.renderInline(s);
    }

    // 3. Question context is code/output (e.g. C formatted I/O %4d)
    const isCodeContext = Boolean(renderOptions && renderOptions.isCodeOrOutput);

    // 4. Check if the entire string is code or whitespace-sensitive output
    const isEntirelyCode = this._entireCodeRegex.test(s.trim()) ||
      (isCodeContext && this.isWhitespaceSensitiveOrCode(s)) ||
      (isCodeContext && !/^[a-zA-Z0-9\s.,'?!-]{30,}$/.test(s.trim()));

    if (isEntirelyCode) {
      return `<code class="inline-code">${escapeHtml(s)}</code>`;
    }

    // 5. Check if option contains mixed prose and code snippets
    const codeSnippetMatches = [];
    const regex = new RegExp(this._codeSnippetRegex.source, 'gi');
    let m;
    while ((m = regex.exec(s)) !== null) {
      codeSnippetMatches.push({
        start: m.index,
        end: m.index + m[0].length,
        text: m[0]
      });
    }

    if (codeSnippetMatches.length > 0) {
      const parts = [];
      let lastIndex = 0;
      for (const snippet of codeSnippetMatches) {
        if (snippet.start > lastIndex) {
          parts.push(escapeHtml(s.slice(lastIndex, snippet.start)));
        }
        parts.push(`<code class="inline-code">${escapeHtml(snippet.text)}</code>`);
        lastIndex = snippet.end;
      }
      if (lastIndex < s.length) {
        parts.push(escapeHtml(s.slice(lastIndex)));
      }
      return parts.join("");
    }

    // 6. Normal academic text
    return escapeHtml(s);
  },

  renderOptionToElement(optionValue, targetEl, renderOptions = {}) {
    if (!targetEl) return;
    const html = this.renderOptionToHtml(optionValue, renderOptions);
    if (html.indexOf("<") !== -1) {
      targetEl.innerHTML = html;
    } else {
      targetEl.textContent = optionValue;
    }
  }
};


// ── Mutually Exclusive Screen Management ────────────────────────────────────
// Ensures EXACTLY ONE screen is visible at any time.
const AUTH_SCREENS = ["loginSection", "registerSection", "forgotPasswordSection", "resetPasswordSection", "authCheckingSection"];
const ALL_SCREENS = ["setup", "loadingSection", "quizSection", "resultSection", "dashboardSection", "notesUploadSection", ...AUTH_SCREENS];

let lastActiveScreen = "setup";
let currentUser = null;

function showScreen(screenId, pushHistory = true) {
  if (screenId === "materialsSection") {
    screenId = "notesUploadSection";
  }

  if (typeof document !== "undefined" && document.documentElement) {
    if (screenId === "loginSection") {
      document.documentElement.classList.add("unauthenticated");
    } else {
      document.documentElement.classList.remove("unauthenticated");
    }
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

  const isAuthScreen = AUTH_SCREENS.includes(screenId);
  const mainNav = $("mainNav");
  const topbarUser = $("topbarUserArea");

  if (isAuthScreen) {
    mainNav?.classList.add("hidden");
    topbarUser?.classList.add("hidden");
    resetPasswordToggles();
  } else {
    mainNav?.classList.remove("hidden");
    if (currentUser) {
      topbarUser?.classList.remove("hidden");
    }
  }

  if (screenId !== "loadingSection" && !isAuthScreen) {
    lastActiveScreen = screenId;
  }

  const isDashboard = screenId === "dashboardSection";
  const isNotes = screenId === "notesUploadSection";
  $("navDashboardBtn")?.classList.toggle("active", isDashboard);
  $("navNotesBtn")?.classList.toggle("active", isNotes);
  $("navMaterialsBtn")?.classList.toggle("active", isNotes);
  $("navPracticeBtn")?.classList.toggle("active", !isDashboard && !isNotes && !isAuthScreen);

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

// ── Session State Persistence across Navigation & Browser Refresh ───────────
// Persist ONLY when the quiz has real progress (>= 1 saved answer).
// A generated-but-unanswered quiz (status "displayed") must never be stored
// as an active quiz.
function saveSessionState() {
  if (quizStatus !== "in_progress") {
    return;
  }
  if (!currentQuiz || !Array.isArray(currentQuiz.questions) || currentQuiz.questions.length === 0) {
    return;
  }
  const hasProgress = answers.some((a) => a !== "" && a !== null && a !== undefined);
  if (!hasProgress) {
    return;
  }
  const state = {
    version: QUIZ_STORAGE_VERSION,
    currentQuiz,
    currentIndex,
    answers,
    activeCourseCode: activeCourseCode || currentQuiz.course_code,
    activeTopicName: activeTopicName || currentQuiz.topic,
    activeTopicId: activeTopicId || currentQuiz.topic_id || "",
    activeNotesSessionId: activeNotesSessionId || null,
    activeStudyMaterialId: activeStudyMaterialId || currentQuiz.study_material_id || null,
    student_id: getStudentId() || null,
    expires_at: currentQuiz.expires_at || null,
    timestamp: Date.now()
  };
  try {
    localStorage.setItem("learnai_active_quiz", JSON.stringify(state));
  } catch (e) {}
  try {
    sessionStorage.setItem("learnai_active_quiz", JSON.stringify(state));
  } catch (e) {}
}

function clearSessionState() {
  try {
    localStorage.removeItem("learnai_active_quiz");
  } catch (e) {}
  try {
    sessionStorage.removeItem("learnai_active_quiz");
  } catch (e) {}
  currentQuiz = null;
  answers = [];
  currentIndex = 0;
  quizStatus = "idle";
  activeNotesSessionId = null;
  const banner = $("activeQuizBanner");
  if (banner) banner.classList.add("hidden");
}

function hasActiveQuiz() {
  // A quiz is "active" only when it has real progress: at least one answer saved.
  if (quizStatus !== "in_progress") {
    return false;
  }
  if (currentQuiz && Array.isArray(currentQuiz.questions) && currentQuiz.questions.length > 0) {
    return answers.some((a) => a !== "" && a !== null && a !== undefined);
  }
  try {
    const raw = (typeof localStorage !== "undefined" && localStorage.getItem("learnai_active_quiz")) ||
                (typeof sessionStorage !== "undefined" && sessionStorage.getItem("learnai_active_quiz"));
    if (!raw) return false;
    const data = JSON.parse(raw);
    if (!data || data.version !== QUIZ_STORAGE_VERSION) return false;
    if (!data.currentQuiz || !Array.isArray(data.currentQuiz.questions) || data.currentQuiz.questions.length === 0) {
      return false;
    }
    return (Array.isArray(data.answers) && data.answers.some((a) => a !== "" && a !== null && a !== undefined));
  } catch (e) {
    return false;
  }
}

function updateActiveQuizBanner(quiz) {
  const banner = $("activeQuizBanner");
  const details = $("activeQuizBannerDetails");
  if (!banner) return;
  if (!quiz || !Array.isArray(quiz.questions) || quiz.questions.length === 0) {
    banner.classList.add("hidden");
    return;
  }
  const courseCode = quiz.course_code || activeCourseCode || "Course";
  const topic = quiz.topic || activeTopicName || "Topic";
  const qNum = (currentIndex || 0) + 1;
  const qTotal = quiz.questions.length;
  if (details) {
    details.textContent = `${courseCode} — ${topic} (Question ${qNum} of ${qTotal})`;
  }
  banner.classList.remove("hidden");
}

async function loadActiveQuizState() {
  try {
    // One-time cleanup: drop legacy storage entries that lack a version field
    // (e.g. pre-fix phantom state generated but never answered).
    try {
      const legacyRaw = (typeof localStorage !== "undefined" && localStorage.getItem("learnai_active_quiz")) ||
                        (typeof sessionStorage !== "undefined" && sessionStorage.getItem("learnai_active_quiz"));
      if (legacyRaw) {
        try {
          const legacyData = JSON.parse(legacyRaw);
          if (!legacyData || legacyData.version !== QUIZ_STORAGE_VERSION) {
            try { localStorage.removeItem("learnai_active_quiz"); } catch (e) {}
            try { sessionStorage.removeItem("learnai_active_quiz"); } catch (e) {}
          }
        } catch (e) {
          // Unparseable: treat as legacy and clear.
          try { localStorage.removeItem("learnai_active_quiz"); } catch (e) {}
          try { sessionStorage.removeItem("learnai_active_quiz"); } catch (e) {}
        }
      }
    } catch (e) {}

    // 1. In-memory fast path
    if (quizStatus === "in_progress" && currentQuiz && Array.isArray(currentQuiz.questions) && currentQuiz.questions.length > 0) {
      return hasActiveQuiz();
    }

    let raw = null;
    try {
      raw = (typeof localStorage !== "undefined" && localStorage.getItem("learnai_active_quiz")) ||
            (typeof sessionStorage !== "undefined" && sessionStorage.getItem("learnai_active_quiz"));
    } catch (e) {}

    let data = null;
    if (raw) {
      try { data = JSON.parse(raw); } catch (e) {}
    }

    if (!data || data.version !== QUIZ_STORAGE_VERSION) {
      const banner = $("activeQuizBanner");
      if (banner) banner.classList.add("hidden");
      return false;
    }

    if (!data.currentQuiz || !Array.isArray(data.currentQuiz.questions) || data.currentQuiz.questions.length === 0) {
      const banner = $("activeQuizBanner");
      if (banner) banner.classList.add("hidden");
      return false;
    }

    // Validate student ownership
    const activeStudentId = (currentUser && currentUser.student_id) || studentId;
    if (activeStudentId && data.currentQuiz.student_id && data.currentQuiz.student_id !== activeStudentId) {
      clearSessionState();
      return false;
    }

    // Validate expiration (aligned with backend quiz_sessions.expires_at, 24h)
    if (data.currentQuiz.expires_at) {
      const expTime = new Date(data.currentQuiz.expires_at).getTime();
      if (!isNaN(expTime) && Date.now() > expTime) {
        clearSessionState();
        return false;
      }
    } else if (data.timestamp && Date.now() - data.timestamp > QUIZ_STORAGE_TTL_MS) {
      clearSessionState();
      return false;
    }

    // Require real progress: at least one saved answer
    if (!(Array.isArray(data.answers) && data.answers.some((a) => a !== "" && a !== null && a !== undefined))) {
      clearSessionState();
      return false;
    }

    currentQuiz = data.currentQuiz;
    currentQuiz.quiz_id = currentQuiz.quiz_id || currentQuiz.session_id;
    currentQuiz.session_id = currentQuiz.session_id || currentQuiz.quiz_id;

    currentIndex = (typeof data.currentIndex === "number" && data.currentIndex >= 0 && data.currentIndex < currentQuiz.questions.length)
      ? data.currentIndex : 0;
    answers = (Array.isArray(data.answers) && data.answers.length === currentQuiz.questions.length)
      ? data.answers : Array(currentQuiz.questions.length).fill("");
    activeCourseCode = data.activeCourseCode || currentQuiz.course_code;
    activeTopicName = data.activeTopicName || currentQuiz.topic;
    activeTopicId = data.activeTopicId || currentQuiz.topic_id || "";
    activeNotesSessionId = data.activeNotesSessionId || null;
    activeStudyMaterialId = data.activeStudyMaterialId || currentQuiz.study_material_id || null;
    quizStatus = "in_progress";

    updateActiveQuizBanner(currentQuiz);
    return true;
  } catch (err) {
    console.warn("[QUIZ] Failed to load active quiz state:", err);
    clearSessionState();
  }
  const banner = $("activeQuizBanner");
  if (banner) banner.classList.add("hidden");
  return false;
}

function resumeActiveQuiz() {
  if (!currentQuiz || !Array.isArray(currentQuiz.questions) || currentQuiz.questions.length === 0) {
    return false;
  }
  currentQuiz.quiz_id = currentQuiz.quiz_id || currentQuiz.session_id;
  currentQuiz.session_id = currentQuiz.session_id || currentQuiz.quiz_id;
  quizStatus = "in_progress";
  if (activeCourseCode) {
    if ($("course")) $("course").value = activeCourseCode;
    populateTopicDropdown(activeCourseCode);
    if ($("topic") && activeTopicName) {
      $("topic").value = activeTopicName;
    }
  }
  renderQuizHeader(currentQuiz);
  renderQuestion();
  showScreen("quizSection");
  saveSessionState();
  return true;
}

let restoreSessionPromise = null;

async function restoreSessionState() {
  if (restoreSessionPromise) {
    return restoreSessionPromise;
  }
  restoreSessionPromise = (async () => {
    try {
      const hasActive = await loadActiveQuizState();
      if (hasActive) {
        resumeActiveQuiz();
        return true;
      }
    } catch (err) {
      console.warn("[QUIZ] Failed to restore session state:", err);
    } finally {
      restoreSessionPromise = null;
    }
    return false;
  })();
  return restoreSessionPromise;
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

// ── Topic Dropdown Population ────────────────────────────────────────────────
function populateTopicDropdown(courseCode) {
  const topicSelect = $("topic");
  if (!topicSelect) return;
  topicSelect.innerHTML = "";

  const course = (curriculum || []).find(c => c.code === courseCode);
  if (!course) {
    topicSelect.disabled = true;
    topicSelect.innerHTML = '<option value="">Select a course first</option>';
    topicSelect.value = "";
    activeTopicName = "";
    activeTopicId = "";
    return;
  }

  // 1. Add default "Select a topic" option
  const defaultOption = document.createElement("option");
  defaultOption.value = "";
  defaultOption.textContent = "Select a topic";
  topicSelect.appendChild(defaultOption);

  // 2. Build coverage map from current uploaded material with canonical alias support
  const coverageMap = new Map();
  const activeNotes = lastExtractionResult && lastExtractionResult.matched_topics;
  if (activeNotes && Array.isArray(activeNotes)) {
    for (const t of activeNotes) {
      if (t && t.topic_name && (!t.course_code || t.course_code === course.code)) {
        const rawName = String(t.topic_name).trim().toLowerCase();
        const normName = rawName.replace(/[\-_\:\(\)\[\]\.,;]+/g, " ").replace(/\s+/g, " ").trim();
        const status = (t.coverage_status || "needs_review").toLowerCase();
        coverageMap.set(rawName, status);
        coverageMap.set(normName, status);
        if (t.topic_id) coverageMap.set(String(t.topic_id).trim().toLowerCase(), status);
        const canonName = CANONICAL_TOPIC_ALIASES_JS[normName];
        if (canonName) {
          const canonLower = canonName.toLowerCase();
          const canonNorm = canonLower.replace(/[\-_\:\(\)\[\]\.,;]+/g, " ").replace(/\s+/g, " ").trim();
          coverageMap.set(canonLower, status);
          coverageMap.set(canonNorm, status);
        }
      }
    }
  }

  // 3. Deduplicate syllabus topics by canonical identity strategy:
  // Priority 1: Stable syllabus topic ID (e.g. 22AM301-Q019-1)
  // Priority 2: Explicit canonical alias mapping
  // Priority 3: Normalized topic title
  const deduplicatedTopics = [];
  const seenTopicKeys = new Set();
  for (const topic of (course.topics || [])) {
    if (!topic || !topic.name) continue;
    const name = String(topic.name).trim();
    if (!name) continue;
    const normKey = name.toLowerCase().replace(/[\-_\:\(\)\[\]\.,;]+/g, " ").replace(/\s+/g, " ").trim();
    const idKey = topic.id ? String(topic.id).trim().toLowerCase() : "";
    const canonTarget = CANONICAL_TOPIC_ALIASES_JS[normKey]
      ? CANONICAL_TOPIC_ALIASES_JS[normKey].toLowerCase().replace(/[\-_\:\(\)\[\]\.,;]+/g, " ").replace(/\s+/g, " ").trim()
      : normKey;
    const groupKey = idKey || canonTarget;
    if (seenTopicKeys.has(groupKey) || seenTopicKeys.has(canonTarget) || seenTopicKeys.has(normKey)) {
      continue;
    }
    seenTopicKeys.add(groupKey);
    seenTopicKeys.add(canonTarget);
    seenTopicKeys.add(normKey);
    if (idKey) seenTopicKeys.add(idKey);
    deduplicatedTopics.push(topic);
  }

  // 4. Classify deduplicated topics into three intended groups
  const covered = [];
  const partial = [];
  const other = [];

  for (const topic of deduplicatedTopics) {
    const rawKey = String(topic.name).trim().toLowerCase();
    const normKey = rawKey.replace(/[\-_\:\(\)\[\]\.,;]+/g, " ").replace(/\s+/g, " ").trim();
    const idKey = topic.id ? String(topic.id).trim().toLowerCase() : "";

    let status = coverageMap.get(rawKey) || coverageMap.get(normKey);
    if (!status && idKey) {
      status = coverageMap.get(idKey);
    }
    if (!status && CANONICAL_TOPIC_ALIASES_JS[normKey]) {
      const canon = CANONICAL_TOPIC_ALIASES_JS[normKey].toLowerCase();
      status = coverageMap.get(canon) || coverageMap.get(canon.replace(/[\-_\:\(\)\[\]\.,;]+/g, " ").replace(/\s+/g, " ").trim());
    }

    if (status === "covered") {
      covered.push(topic);
    } else if (status === "partially_covered") {
      partial.push(topic);
    } else {
      other.push(topic);
    }
  }

  // 5. Helper to create standard <option> element
  const createTopicOption = (topic) => {
    const name = String(topic.name).trim();
    const rawKey = name.toLowerCase();
    const normKey = rawKey.replace(/[\-_\:\(\)\[\]\.,;]+/g, " ").replace(/\s+/g, " ").trim();
    const idKey = topic.id ? String(topic.id).trim().toLowerCase() : "";
    const coverage = (coverageMap.get(rawKey) || coverageMap.get(normKey) || (idKey ? coverageMap.get(idKey) : "") || "not_found").toLowerCase();

    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    option.dataset.topicId = topic.id || "";
    option.dataset.topicName = name;
    option.dataset.parentTopic = topic.parent_topic || topic.parent_section || topic.source_topic_name || "";
    option.dataset.module = (topic.module !== undefined && topic.module !== null) ? topic.module : "";
    option.dataset.unit = (topic.unit !== undefined && topic.unit !== null) ? topic.unit : "";
    option.dataset.coverage = coverage;
    return option;
  };

  // 6. Only render optgroup category headings when notes are uploaded with covered/partial matches!
  const hasNotesGrouping = (covered.length > 0 || partial.length > 0);

  if (hasNotesGrouping) {
    const appendOptGroup = (label, topicList) => {
      if (!topicList || topicList.length === 0) return;
      const og = document.createElement("optgroup");
      og.label = label;
      for (const topic of topicList) {
        og.appendChild(createTopicOption(topic));
      }
      topicSelect.appendChild(og);
    };

    appendOptGroup("Topics found in your notes", covered);
    appendOptGroup("Partially covered topics", partial);
    appendOptGroup("Other syllabus topics (general practice)", other);
  } else {
    // When no notes are uploaded for the selected course, remove the heading and display topics cleanly under <select>
    for (const topic of other) {
      topicSelect.appendChild(createTopicOption(topic));
    }
  }

  topicSelect.disabled = false;
  topicSelect.value = "";
}

$("course").addEventListener("change", () => {
  const selectedCourseCode = $("course").value;
  if (selectedCourseCode !== activeCourseCode) {
    activeCourseCode = selectedCourseCode;
    activeTopicName = "";
    activeTopicId = "";
    const topicSelect = $("topic");
    if (topicSelect) {
      topicSelect.value = "";
    }
  }
  if ($("setupMsg")) {
    $("setupMsg").innerHTML = "";
  }
  populateTopicDropdown(selectedCourseCode);
});

if ($("topic")) {
  $("topic").addEventListener("change", () => {
    const topicSelect = $("topic");
    const selectedOption = topicSelect.selectedOptions ? topicSelect.selectedOptions[0] : null;
    activeTopicName = topicSelect.value || "";
    activeTopicId = selectedOption?.dataset?.topicId || "";
    if ($("setupMsg")) {
      $("setupMsg").innerHTML = "";
    }
  });
}

// ── Navigation Buttons ───────────────────────────────────────────────────────
$("generateBtn").addEventListener("click", () => {
  const courseCode = $("course").value;
  const topicSelect = $("topic");
  const topic = topicSelect.value;
  const selectedOption = topicSelect.selectedOptions ? topicSelect.selectedOptions[0] : null;
  const topicId = selectedOption?.dataset?.topicId || "";
  const coverage = (selectedOption?.dataset?.coverage || "").toLowerCase();

  const invalidTopicLabels = new Set([
    "",
    "Select a topic",
    "Select a course first",
    "Topics found in your notes",
    "Partially covered topics",
    "Other syllabus topics (general practice)"
  ]);

  if (!courseCode || !topic || invalidTopicLabels.has(topic.trim()) || (selectedOption && selectedOption.disabled)) {
    $("setupMsg").innerHTML = '<span class="error-msg">Please select both course and topic.</span>';
    return;
  }
  $("setupMsg").innerHTML = "";

  // If this topic is found in uploaded notes (covered or partially_covered)
  // and we have an active notes session or study material, pass it!
  // If it is general syllabus practice (not_found or no notes), pass null so
  // quiz generation cleanly treats it as general syllabus practice.
  let notesSessionId = null;
  let studyMaterialId = null;
  if ((coverage === "covered" || coverage === "partially_covered") && lastExtractionResult) {
    notesSessionId = lastExtractionResult.notes_session_id || null;
    studyMaterialId = lastExtractionResult.study_material_id || null;
  }

  startQuizGeneration(courseCode, topic, false, topicId, notesSessionId, studyMaterialId);
});

$("prevBtn").addEventListener("click", () => {
  if (currentIndex > 0) {
    currentIndex--;
    saveSessionState();
    renderQuestion();
  }
});


$("resumeActiveQuizBtn")?.addEventListener("click", () => {
  resumeActiveQuiz();
});

$("discardActiveQuizBtn")?.addEventListener("click", async () => {
  if (confirm("Discard this unfinished quiz and choose another topic?")) {
    const sId = currentQuiz ? (currentQuiz.quiz_id || currentQuiz.session_id) : null;
    if (sId) {
      try {
        await authenticatedFetch("/api/quiz/abandon", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sId })
        });
      } catch (e) {
        console.warn("[QUIZ] Failed to abandon quiz on server:", e);
      }
    }
    clearSessionState();
    const banner = $("activeQuizBanner");
    if (banner) banner.classList.add("hidden");
  }
});

if (typeof window !== "undefined") {
  window.addEventListener("storage", (e) => {
    if (e.key === "learnai_result_sync" && e.newValue) {
      try {
        const syncData = JSON.parse(e.newValue);
        if (syncData && syncData.student_id === getStudentId()) {
          clearSessionState();
        }
      } catch (err) {}
    }
    if (e.key === "learnai_active_quiz" && !e.newValue) {
      clearSessionState();
    }
  });
}

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
    saveSessionState();
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

  // 3. Clear question view DOM elements and session state
  if ($("options")) $("options").innerHTML = "";
  if ($("question")) $("question").textContent = "";
  if ($("concept")) $("concept").textContent = "";
  if ($("progressBar")) $("progressBar").style.width = "0%";
  if ($("progressPercent")) $("progressPercent").textContent = "0% completed";
  clearSessionState();

  // 4. Launch fresh quiz generation for retake
  await startQuizGeneration(courseCode, topic, true, topicId, activeNotesSessionId, activeStudyMaterialId);

  retakeBtn.disabled = false;
  retakeBtn.textContent = "Retake with New AI Questions";
  retakeBtn.dataset.processing = "false";
});

// ── Explicit Home Navigation & Loading Screen Exit ──────────────────────────
function goToHome() {
  if (!currentUser) {
    showScreen("loginSection");
    return;
  }
  if (isGenerating) return;
  generationScreenOwner = null;
  lastCompletedResult = null;
  showScreen("setup");
  $("setupMsg").textContent = "";
}

function returnFromLoading() {
  generationScreenOwner = null;
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
    if (activeCourseCode && $("course")) {
      $("course").value = activeCourseCode;
      populateTopicDropdown(activeCourseCode);
      if ($("topic") && activeTopicName) {
        $("topic").value = activeTopicName;
      }
    }
  }
}

$("newTopicBtn").addEventListener("click", goToHome);
$("brandLogo")?.addEventListener("click", handlePracticeNav);
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
async function startQuizGeneration(courseCode, topic, isRetake = false, topicId = null, notesSessionId = null, studyMaterialId = null, overrideAbandonSessionIds = null) {
  if (isGenerating) return;
  let abandonSessionIds = null;
  if (Array.isArray(overrideAbandonSessionIds)) {
    abandonSessionIds = overrideAbandonSessionIds;
  } else if (typeof overrideAbandonSessionIds === "string" && overrideAbandonSessionIds) {
    abandonSessionIds = [overrideAbandonSessionIds];
  }
  let abandonSessionId = (abandonSessionIds && abandonSessionIds.length === 1) ? abandonSessionIds[0] : null;

  if (!isRetake && hasActiveQuiz()) {
    if (!abandonSessionIds) {
      const activeCourse = (currentQuiz && currentQuiz.course_code) || activeCourseCode;
      const activeTopic = (currentQuiz && currentQuiz.topic) || activeTopicName;
      const isSameTopic = (activeCourse === courseCode && activeTopic === topic);

      if (isSameTopic) {
        const resumeChoice = confirm("You have an unfinished quiz in progress for " + topic + ".\n\nClick OK to resume your existing quiz.\nClick Cancel to discard it and generate a fresh one.");
        if (resumeChoice) {
          restoreSessionState();
          return;
        } else {
          const sId = (currentQuiz && (currentQuiz.quiz_id || currentQuiz.session_id)) || null;
          abandonSessionIds = sId ? [sId] : null;
          abandonSessionId = sId;
        }
      } else {
        const discardChoice = confirm("You have an unfinished quiz in progress for " + (activeTopic || "another topic") + ".\n\nDiscard it and start a new quiz for " + topic + "?");
        if (!discardChoice) {
          return;
        }
        const sId = (currentQuiz && (currentQuiz.quiz_id || currentQuiz.session_id)) || null;
        abandonSessionIds = sId ? [sId] : null;
        abandonSessionId = sId;
      }
    }
  }
  isGenerating = true;
  generationScreenOwner = "generation";

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
    populateTopicDropdown(courseCode);
    if ($("topic")) $("topic").value = topic;

    // ── Setup Loading Screen (Calm, Minimal, Academic) ──
    if (spinner) spinner.style.display = "block";
    if ($("loadingErrorBox")) $("loadingErrorBox").classList.add("hidden");
    if ($("loadingSkeleton")) $("loadingSkeleton").classList.remove("hidden");

    // Populate course and topic label (small, secondary)
    const courseObj = curriculum.find(c => c.code === courseCode);
    const courseLabel = courseObj ? `${courseObj.code} - ${courseObj.name}` : courseCode;
    const notesTag = (activeNotesSessionId || activeStudyMaterialId) ? " [Notes-Grounded]" : "";
    if ($("loadingTopicLabel")) {
      $("loadingTopicLabel").textContent = `${courseLabel} • ${topic}${notesTag}`;
    }

    showScreen("loadingSection");

    const hintEl = document.querySelector("#loadingSection .loading-hint");
    if (hintEl) hintEl.textContent = "Connecting to AI generation service...";
    const stageTimer1 = setTimeout(() => {
      if (hintEl && isGenerating) hintEl.textContent = "Generating balanced 10-question MCQ set...";
    }, 2000);
    const stageTimer2 = setTimeout(() => {
      if (hintEl && isGenerating) hintEl.textContent = "Verifying question accuracy and answer keys...";
    }, 5500);
    const stageTimer3 = setTimeout(() => {
      if (hintEl && isGenerating) hintEl.textContent = "Finalizing quiz session...";
    }, 8500);

    activeAbortController = new AbortController();
    const timeoutSec = 50;
    timeoutId = setTimeout(() => activeAbortController?.abort(), timeoutSec * 1000);

    const quizPayload = {
      course_code: courseCode,
      topic: topic,
      topic_id: activeTopicId || undefined,
      student_id: getStudentId() || undefined,
      retake: isRetake,
      abandon_session_id: abandonSessionId || undefined,
      abandon_session_ids: abandonSessionIds || undefined
    };
    if (activeNotesSessionId) {
      quizPayload.notes_session_id = activeNotesSessionId;
    }
    if (activeStudyMaterialId) {
      quizPayload.study_material_id = activeStudyMaterialId;
    }
    quizPayload.practice_mode = (activeNotesSessionId || activeStudyMaterialId)
      ? "notes_grounded"
      : "general_syllabus";

    const res = await authenticatedFetch("/api/quiz/generate", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(quizPayload),
      signal: activeAbortController.signal
    });
    if (timeoutId) clearTimeout(timeoutId);

    const data = await res.json();

    if (!res.ok) {
      if (res.status === 409 && data && data.detail && typeof data.detail === "object") {
        const activeInfo = data.detail;
        const activeSessions = Array.isArray(activeInfo.active_sessions) ? activeInfo.active_sessions : [];

        // If we have local progress, offer the user resume vs discard.
        // Otherwise auto-abandon the stale backend session(s) and retry once.
        const hasLocalProgress = hasActiveQuiz();

        if (activeSessions.length > 1) {
          const sessionList = activeSessions.map(s => "• " + (s.course_code ? (s.course_code + " - ") : "") + s.topic).join("\n");
          if (hasLocalProgress) {
            const msg = "You have " + activeSessions.length + " unfinished quiz sessions in progress:\n\n" + sessionList + "\n\nDiscard ALL listed unfinished quizzes and start a new quiz for " + (courseCode ? (courseCode + " - ") : "") + topic + "?\n\nClick OK to discard all listed quizzes and proceed.\nClick Cancel to keep your existing quizzes.";
            const confirmMulti = confirm(msg);
            if (confirmMulti) {
              isGenerating = false;
              const allIds = activeSessions.map(s => s.session_id);
              return await startQuizGeneration(courseCode, topic, isRetake, topicId, activeNotesSessionId, activeStudyMaterialId, allIds);
            } else {
              return;
            }
          }
          // No local progress: auto-abandon all stale sessions and retry once.
          isGenerating = false;
          for (const s of activeSessions) {
            try {
              await authenticatedFetch("/api/quiz/abandon", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ session_id: s.session_id })
              });
            } catch (e) {
              console.warn("[QUIZ] Failed to abandon stale session", s.session_id, e);
            }
          }
          return await startQuizGeneration(courseCode, topic, isRetake, topicId, activeNotesSessionId, activeStudyMaterialId);
        } else {
          const conflictSessionId = activeInfo.active_session_id;
          const conflictTopic = activeInfo.topic || "another topic";
          const conflictCourse = activeInfo.course_code || "";
          if (hasLocalProgress) {
            const resumeConflict = confirm("You already have an active quiz session in progress for " + (conflictCourse ? (conflictCourse + " - ") : "") + conflictTopic + ".\n\nClick OK to resume your active quiz.\nClick Cancel to discard it and create this new quiz.");
            if (resumeConflict) {
              await restoreSessionState();
              return;
            } else {
              isGenerating = false;
              return await startQuizGeneration(courseCode, topic, isRetake, topicId, activeNotesSessionId, activeStudyMaterialId, [conflictSessionId]);
            }
          }
          // No local progress: auto-abandon the stale session and retry once.
          isGenerating = false;
          try {
            await authenticatedFetch("/api/quiz/abandon", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ session_id: conflictSessionId })
            });
          } catch (e) {
            console.warn("[QUIZ] Failed to abandon stale session", conflictSessionId, e);
          }
          return await startQuizGeneration(courseCode, topic, isRetake, topicId, activeNotesSessionId, activeStudyMaterialId);
        }
      }
      const errMsg = (data && data.detail)
        ? (typeof data.detail === "object" ? (data.detail.message || "Quiz generation is temporarily unavailable. Please try again shortly.") : data.detail)
        : "Quiz generation is temporarily unavailable. Please try again shortly.";
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

    // Hide skeleton before real content is rendered
    if ($("loadingSkeleton")) $("loadingSkeleton").classList.add("hidden");

    // ── Success: Initialize Clean Quiz State ──
    currentQuiz = data;
    if (data.topic_id) activeTopicId = data.topic_id;
    if (data.study_material_id) activeStudyMaterialId = data.study_material_id;
    if (data.notes_session_id) activeNotesSessionId = data.notes_session_id;
    currentIndex = 0;
    answers = Array(data.questions.length).fill("");
    // Questions rendered but zero answers saved: NOT "in progress" yet.
    quizStatus = "displayed";

    renderQuestion();
    // Only auto-open the quiz screen if the user is still in the generation
    // flow (hasn't navigated to Dashboard, Notes, Materials, etc.). Otherwise
    // leave the quiz in memory; it reappears when they return to Practice Quiz
    // via handlePracticeNav (in-memory resume).
    if (generationScreenOwner) {
      showScreen("quizSection");
    }
    generationScreenOwner = null;
    saveSessionState();

  } catch (e) {
    if (timeoutId) clearTimeout(timeoutId);

    generationScreenOwner = null;

    // AbortError comes from Cancel (returnFromLoading) or popstate back/forward.
    // Never show an error message for a user-initiated cancel.
    const isAbort = e.name === "AbortError";

    if (!isAbort) {
      let friendlyMsg = "Quiz generation is temporarily unavailable. Please try again shortly.";
      if (e.message && typeof e.message === "string" && e.message.trim().length > 0 && !e.message.startsWith("{")) {
        const lower = e.message.toLowerCase();
        if (
          lower.includes("high demand") ||
          lower.includes("rate limit") ||
          lower.includes("quota exceeded") ||
          lower.includes("unavailable") ||
          lower.includes("temporarily busy") ||
          lower.includes("timed out")
        ) {
          friendlyMsg = "Quiz generation is temporarily unavailable. Please try again shortly.";
        } else {
          friendlyMsg = e.message;
        }
      }

      // Never leave the loading screen stuck and never show a quiz for a failed
      // generation: return to topic selection with the error message.
      if ($("setupMsg")) $("setupMsg").textContent = friendlyMsg;
    }

    showScreen("setup");

  } finally {
    isGenerating = false;
    if (generateBtn) generateBtn.disabled = false;
    if (retakeBtn) retakeBtn.disabled = false;
    const hintEl = document.querySelector("#loadingSection .loading-hint");
    if (hintEl) hintEl.textContent = "This may take a few moments...";
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
    const fallbackNotice = "";
    $("quizAttemptNumber").textContent = attempt > 1 ? `Attempt #${attempt} • ${diff}${fallbackNotice}` : `Attempt #1 • ${diff}${fallbackNotice}`;
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

  const isCodeOrOutput = LearnAICodeRenderer.isQuestionCodeOrOutput(q);

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
    LearnAICodeRenderer.renderOptionToElement(value, textSpan, { isCodeOrOutput });

    div.appendChild(radioCircle);
    div.appendChild(letterSpan);
    div.appendChild(textSpan);

    const selectOption = () => {
      answers[currentIndex] = key;
      // Mark in_progress only when the first answer is saved (zero answers != active).
      if (quizStatus !== "in_progress") {
        quizStatus = "in_progress";
      }
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

  if ($("question") && typeof LearnAIMathRenderer !== "undefined") {
    LearnAIMathRenderer.typeset($("question"));
  }
  if ($("options") && typeof LearnAIMathRenderer !== "undefined") {
    LearnAIMathRenderer.typeset($("options"));
  }

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
      if (quizStatus !== "in_progress") {
        quizStatus = "in_progress";
      }
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
  // Double-click guard: never submit twice concurrently.
  if (submitInFlight) return;
  submitInFlight = true;

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
      // Expired / not found: clear and surface error.
      if (res.status === 404 || (res.status === 400 && typeof data.detail === "string" && data.detail.toLowerCase().includes("expired"))) {
        clearSessionState();
      }
      // Any submit failure: keep state (answers + in_progress) so the user can retry.
      throw new Error(data.detail || "Failed to submit quiz.");
    }

    // Confirmed success: mark completed and clear everywhere.
    quizStatus = "completed";
    lastPracticedStudyMaterialId = activeStudyMaterialId || currentQuiz.study_material_id || null;
    clearSessionState();
    studyMaterialsCacheTime = 0;
    materialTopicsCache.clear();
    lastCompletedResult = data;
    try {
      localStorage.setItem("learnai_result_sync", JSON.stringify({
        student_id: getStudentId(),
        session_id: data.session_id,
        timestamp: Date.now()
      }));
    } catch (e) {}
    showResult(data);
  } catch (e) {
    clearTimeout(timeoutId);
    let msg = "Failed to submit quiz. Please try again.";
    if (e.name === "AbortError") {
      msg = "Analysis timed out. Please click Submit Quiz again.";
    } else if (e.message) {
      msg = e.message;
    }
    alert(msg + "\n\nYour answers are kept — you can retry submitting.");
  } finally {
    submitInFlight = false;
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

  const retakeBtn = $("retakeBtn");
  if (retakeBtn) {
    retakeBtn.textContent = "Retake with New AI Questions";
  }

  const reviewHtml = (data.details || []).map((d, i) => {
    const rawConcept = d.concept || (currentQuiz && currentQuiz.questions && currentQuiz.questions[i] && currentQuiz.questions[i].concept) || "General";
    const concept = String(rawConcept).trim() || "General";
    const rawDiff = d.difficulty || (currentQuiz && currentQuiz.questions && currentQuiz.questions[i] && currentQuiz.questions[i].difficulty) || "Medium";
    const difficulty = String(rawDiff).trim() || "Medium";

    // Options mapping: prefer d.options, fallback to currentQuiz.questions[i].options
    const options = (d.options && Object.keys(d.options).length > 0)
      ? d.options
      : ((currentQuiz && currentQuiz.questions && currentQuiz.questions[i] && currentQuiz.questions[i].options) || {});
    const hasOptions = options && Object.keys(options).length > 0;

    const selectedKey = (d.selected_answer || "").trim().toUpperCase();
    const correctKey = (d.correct_answer || "").trim().toUpperCase();

    const hasSelected = Boolean(selectedKey && (!hasOptions || options[selectedKey] !== undefined));
    const isCorrect = Boolean(d.is_correct);
    const isSkipped = !hasSelected;

    // Student selected option text
    let selectedText = "";
    if (hasSelected && options[selectedKey]) {
      selectedText = options[selectedKey];
    } else if (hasSelected && !options[selectedKey]) {
      selectedText = d.selected_answer;
    }

    // Correct option text
    let correctText = "";
    if (options[correctKey]) {
      correctText = options[correctKey];
    } else {
      correctText = d.correct_answer;
    }

    const isCodeOrOutput = LearnAICodeRenderer.isQuestionCodeOrOutput(d) ||
      Boolean(currentQuiz && currentQuiz.questions && currentQuiz.questions[i] && LearnAICodeRenderer.isQuestionCodeOrOutput(currentQuiz.questions[i]));

    // Status pill determination (accessible text + icon)
    let statusPillHtml = "";
    if (isCorrect) {
      statusPillHtml = `
        <span class="review-status-pill pill-correct">
          <svg class="review-status-icon" viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true">
            <path d="M13.78 4.22a.75.75 0 010 1.06l-7.25 7.25a.75.75 0 01-1.06 0L2.22 9.28a.75.75 0 111.06-1.06L6 10.94l6.72-6.72a.75.75 0 011.06 0z"/>
          </svg>
          Correct
        </span>
      `;
    } else if (isSkipped) {
      statusPillHtml = `
        <span class="review-status-pill pill-skipped">
          <svg class="review-status-icon" viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true">
            <path d="M8 1.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM0 8a8 8 0 1116 0A8 8 0 010 8zm8-3.25a.75.75 0 01.75.75v3.25H11a.75.75 0 010 1.5H8a.75.75 0 01-.75-.75V5.5A.75.75 0 018 4.75z"/>
          </svg>
          Skipped / Unanswered
        </span>
      `;
    } else {
      statusPillHtml = `
        <span class="review-status-pill pill-incorrect">
          <svg class="review-status-icon" viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true">
            <path d="M3.72 3.72a.75.75 0 011.06 0L8 6.94l3.22-3.22a.75.75 0 111.06 1.06L9.06 8l3.22 3.22a.75.75 0 11-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 01-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 010-1.06z"/>
          </svg>
          Incorrect
        </span>
      `;
    }

    // Build Distractors List ("Why the other options are incorrect")
    let distractorsHtml = "";
    if (hasOptions) {
      const optionKeys = Object.keys(options).sort();
      const distractorKeys = optionKeys.filter(k => k.toUpperCase() !== correctKey);

      if (distractorKeys.length > 0) {
        const optionExplanations = d.option_explanations ||
          ((currentQuiz && currentQuiz.questions && currentQuiz.questions[i] && currentQuiz.questions[i].option_explanations) || {});

        const itemsHtml = distractorKeys.map(k => {
          const optVal = options[k];
          let reason = optionExplanations[k] || optionExplanations[k.toUpperCase()] || "";
          if (!reason) {
            reason = `Option ${k} is not the correct solution for this ${concept} question. Refer to the detailed explanation for the step-by-step logic.`;
          }
          return `
            <li class="review-distractor-entry">
              <div class="distractor-header">
                <div class="distractor-badge-row">
                  <span class="choice-badge badge-choice-incorrect">Option ${escapeHtml(k)}</span>
                  <span class="distractor-status-tag">Incorrect</span>
                </div>
                ${optVal ? `<div class="distractor-option-content">${LearnAICodeRenderer.renderOptionToHtml(optVal, { isCodeOrOutput })}</div>` : ""}
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

        distractorsHtml = `
          <div class="review-distractors-block">
            <h4 class="review-subheading">Why the other options are incorrect</h4>
            <ul class="review-distractor-list">
              ${itemsHtml}
            </ul>
          </div>
        `;
      }
    }

    return `
      <div class="review-question-item">
        <div class="review-item-header">
          <div class="review-item-meta">
            <span class="review-q-number">Question ${i + 1}</span>
            <span class="review-meta-dot" aria-hidden="true">&middot;</span>
            <span class="review-q-concept">${escapeHtml(concept)}</span>
            <span class="review-diff-tag diff-${difficulty.toLowerCase()}">${escapeHtml(difficulty)}</span>
          </div>
          ${statusPillHtml}
        </div>

        <div class="review-question-body">
          ${LearnAICodeRenderer.renderToHtml(d.question)}
        </div>

        <div class="review-answer-summary">
          <div class="review-answer-line student-answer-line">
            <div class="review-line-header">
              <span class="review-line-label">Your answer:</span>
              ${hasSelected ? `
                <span class="choice-badge ${isCorrect ? 'badge-choice-correct' : 'badge-choice-incorrect'}">Option ${escapeHtml(selectedKey)}</span>
                ${isCorrect ? `<span class="review-status-tag tag-correct">Correct</span>` : `<span class="review-status-tag tag-incorrect">Incorrect</span>`}
              ` : `
                <span class="choice-unanswered">Not answered</span>
              `}
            </div>
            ${hasSelected && selectedText ? `
              <div class="review-line-content">
                <span class="choice-text">${LearnAICodeRenderer.renderOptionToHtml(selectedText, { isCodeOrOutput })}</span>
              </div>
            ` : ""}
          </div>

          <div class="review-answer-line correct-answer-line">
            <div class="review-line-header">
              <span class="review-line-label">Correct answer:</span>
              <span class="choice-badge badge-choice-correct">Option ${escapeHtml(correctKey)}</span>
              <span class="review-status-tag tag-correct">Correct</span>
            </div>
            ${correctText ? `
              <div class="review-line-content">
                <span class="choice-text">${LearnAICodeRenderer.renderOptionToHtml(correctText, { isCodeOrOutput })}</span>
              </div>
            ` : ""}
          </div>
        </div>

        <div class="review-explanation-block">
          <h4 class="review-subheading">Explanation</h4>
          <div class="review-prose">
            ${LearnAICodeRenderer.renderToHtml(d.explanation || "No explanation provided.")}
          </div>
        </div>

        ${distractorsHtml}
      </div>
    `;
  }).join("");

  $("review").innerHTML = `
    <div class="review-header">
      <div class="review-header-title-group">
        <h3>Detailed Question Review &amp; Explanations</h3>
        <p class="review-header-subtitle">Review each question statement, step-by-step reasoning, and distractor analysis below to strengthen your understanding.</p>
      </div>
    </div>
    <div class="review-items-container">
      ${reviewHtml}
    </div>
  `;

  // Typeset all mathematical expressions across question statements, answers, explanations, and distractors
  if (typeof LearnAIMathRenderer !== "undefined") {
    LearnAIMathRenderer.typeset($("review"));
  }

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
  if (!currentUser) {
    showScreen("loginSection");
    return;
  }
  if (isGenerating) return;
  // Leaving the generation flow: clear the generation owner so a finishing
  // generation does not auto-show the quiz on an unrelated screen.
  generationScreenOwner = null;
  lastCompletedResult = null;
  saveSessionState();
  expandedReviewCourseId = null;
  if (typeof window !== "undefined") window.expandedReviewCourseId = null;
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
  expandedReviewCourseId = null;
  if (typeof window !== "undefined") window.expandedReviewCourseId = null;
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

// ── Recommended Review Single-Card Focus Mode Helpers ───────────────────────
if (typeof expandedReviewCourseId === "undefined") {
  var expandedReviewCourseId = null;
}
if (typeof lastDashboardData === "undefined") {
  var lastDashboardData = null;
}

function isCourseExpanded(r, idx, cardId) {
  if (!expandedReviewCourseId) return false;
  const target = String(expandedReviewCourseId).trim().toLowerCase();
  if (cardId && String(cardId).trim().toLowerCase() === target) return true;
  if (r.course_code && String(r.course_code).trim().toLowerCase() === target) return true;
  if (r.topic_name && `${r.course_code || ""}:::${r.topic_name}`.trim().toLowerCase() === target) return true;
  if (r.topic_id && String(r.topic_id).trim().toLowerCase() === target) return true;
  return false;
}

function renderReviewTopics(reviewTopics, data) {
  const reviewListEl = $("reviewTopicsList");
  if (!reviewListEl) return;

  // Synchronize external window.expandedReviewCourseId if set by caller/test
  if (typeof window !== "undefined" && window.expandedReviewCourseId !== undefined && window.expandedReviewCourseId !== expandedReviewCourseId) {
    expandedReviewCourseId = window.expandedReviewCourseId;
  }

  // If the expanded course disappears from the refreshed Recommended Review data, safely clear selection
  if (expandedReviewCourseId) {
    const matchExists = reviewTopics.some((r, idx) => isCourseExpanded(r, idx, `rec-card-${idx}`));
    if (!matchExists) {
      expandedReviewCourseId = null;
      if (typeof window !== "undefined") window.expandedReviewCourseId = null;
    }
  }

  const isFocusMode = Boolean(expandedReviewCourseId);
  if (isFocusMode) {
    reviewListEl.classList.add("has-focused-card");
  } else {
    reviewListEl.classList.remove("has-focused-card");
  }

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
    const recListEl = $("recommendedTopicsList");
    if (recListEl) recListEl.innerHTML = reviewListEl.innerHTML;
    return;
  }

  // When in single-card focus mode: render strictly the focused course card.
  // In normal state: render all Recommended Review course cards in the responsive grid.
  const topicsToRender = isFocusMode
    ? reviewTopics.filter((r, idx) => isCourseExpanded(r, idx, `rec-card-${idx}`))
    : reviewTopics;

  const effectiveTopics = topicsToRender.length ? topicsToRender : reviewTopics;

  reviewListEl.innerHTML = effectiveTopics.map((r) => {
    const origIdx = reviewTopics.indexOf(r);
    const idx = origIdx >= 0 ? origIdx : 0;
    const isHigh = r.priority === "high";
    const cardId = `rec-card-${idx}`;
    const isFocused = isFocusMode && isCourseExpanded(r, idx, cardId);

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

    let historyItems = (Array.isArray(r.history) && r.history.length > 0) ? r.history : [];
    if (!historyItems.length && (latestScore !== null || (typeof r.attempt_count === "number" && r.attempt_count > 0))) {
      historyItems = [{
        score: latestScore ?? 0,
        total: latestTotal ?? 10,
        accuracy: latestAcc ?? 0,
        recommended_difficulty: r.recommended_difficulty || "mixed",
        created_at: r.created_at || r.last_practiced_at || null
      }];
    }
    const hasHistory = historyItems.length > 0;

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
        <div id="history-panel-${cardId}" class="topic-history-panel ${isFocused ? '' : 'hidden'}" aria-hidden="${isFocused ? 'false' : 'true'}">
          <div class="topic-history-header">
            <span>Attempt History</span>
            ${isFocused ? `
              <button type="button" class="topic-history-close-btn topic-history-toggle-btn" aria-expanded="true" aria-controls="history-panel-${cardId}" data-course-id="${escapeHtml(r.course_code || cardId)}" data-item-key="${escapeHtml(r.course_code ? r.course_code + ':::' + r.topic_name : cardId)}" data-card-id="${cardId}" title="Hide details">
                &times; Hide details
              </button>
            ` : ""}
          </div>
          <div class="table-container history-table-container">
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

    const itemKey = (r.course_code && r.topic_name) ? `${r.course_code}:::${r.topic_name}` : (r.course_code || cardId);

    return `
      <div class="rec-card ${isHigh ? 'card-review-high' : 'card-review-adv'} ${isFocused ? 'rec-card-focused' : ''}" id="${cardId}">
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
              <button type="button" class="secondary topic-history-toggle-btn ${isFocused ? 'btn-hide-details' : ''}" aria-expanded="${isFocused ? 'true' : 'false'}" aria-controls="history-panel-${cardId}" data-course-id="${escapeHtml(r.course_code || cardId)}" data-item-key="${escapeHtml(itemKey)}" data-card-id="${cardId}" title="${isFocused ? 'Hide details' : 'View history'}">
                ${isFocused ? 'Hide details' : 'View history'}
              </button>
            ` : ""}
          </div>
          ${historyHtml}
        </div>
      </div>
    `;
  }).join("");

  const recListEl = $("recommendedTopicsList");
  if (recListEl && reviewListEl) {
    recListEl.innerHTML = reviewListEl.innerHTML;
  }
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

  // Cache latest dashboard data for client-side interactions and toggles
  lastDashboardData = data;

  // 2. Recommended Review (Topics Needing Review)
  const reviewTopics = data.review_topics || data.recommended_topics || [];
  renderReviewTopics(reviewTopics, data);

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
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();

      const isExpanded = btn.getAttribute("aria-expanded") === "true";
      const itemKey = btn.getAttribute("data-item-key");
      const courseId = btn.getAttribute("data-course-id");
      const cardId = btn.getAttribute("data-card-id");

      if (isExpanded) {
        // User clicked "Hide details" -> Clear expanded course selection and restore all cards
        expandedReviewCourseId = null;
        if (typeof window !== "undefined") window.expandedReviewCourseId = null;
      } else {
        // User clicked "View history" -> Focus on this selected course card
        expandedReviewCourseId = itemKey || courseId || cardId;
        if (typeof window !== "undefined") window.expandedReviewCourseId = expandedReviewCourseId;
      }

      if (lastDashboardData) {
        renderReviewTopics(lastDashboardData.review_topics || lastDashboardData.recommended_topics || [], lastDashboardData);
        attachPracticeTopicListeners();
        attachHistoryToggleListeners();
      } else {
        // Fallback for minimal DOM environments without lastDashboardData
        const gridEl = $("reviewTopicsList");
        const allCards = gridEl ? gridEl.querySelectorAll(".rec-card") : [];
        if (isExpanded) {
          if (gridEl) gridEl.classList.remove("has-focused-card");
          allCards.forEach(c => {
            c.classList.remove("rec-card-focused", "hidden");
            const p = c.querySelector(".topic-history-panel");
            if (p) { p.classList.add("hidden"); p.setAttribute("aria-hidden", "true"); }
            const b = c.querySelector(".topic-history-toggle-btn");
            if (b) { b.setAttribute("aria-expanded", "false"); b.textContent = "View history"; b.classList.remove("btn-hide-details"); }
          });
        } else {
          if (gridEl) gridEl.classList.add("has-focused-card");
          allCards.forEach(c => {
            if (c.id === cardId) {
              c.classList.add("rec-card-focused");
              c.classList.remove("hidden");
              const p = c.querySelector(".topic-history-panel");
              if (p) { p.classList.remove("hidden"); p.setAttribute("aria-hidden", "false"); }
              const b = c.querySelector(".topic-history-toggle-btn");
              if (b) { b.setAttribute("aria-expanded", "true"); b.textContent = "Hide details"; b.classList.add("btn-hide-details"); }
            } else {
              c.classList.add("hidden");
              c.classList.remove("rec-card-focused");
            }
          });
        }
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
    populateTopicDropdown(courseObj.code);
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
async function handlePracticeNav() {
  if (!currentUser && !localStorage.getItem("learnai_session_active")) {
    showScreen("loginSection");
    return;
  }
  if (isGenerating) return;
  // In-memory resume only: never read storage on tab switch (req #6).
  // A quiz left in memory after generation (status "displayed") or with progress
  // (status "in_progress") reappears here when the user returns to Practice Quiz.
  if ((quizStatus === "displayed" || quizStatus === "in_progress") && currentQuiz && Array.isArray(currentQuiz.questions) && currentQuiz.questions.length > 0) {
    resumeActiveQuiz();
    return;
  }
  // quizStatus 'idle' or 'completed': always show topic selection. Never fall back
  // to a completed or latest result (fixes the phantom-active-quiz bug).
  generationScreenOwner = null;
  lastCompletedResult = null;
  showScreen("setup");
  if ($("setupMsg")) $("setupMsg").textContent = "";
}

$("navPracticeBtn")?.addEventListener("click", handlePracticeNav);
$("navDashboardBtn")?.addEventListener("click", openDashboard);
$("viewDashboardFromResultBtn")?.addEventListener("click", openDashboard);
$("dashboardStartQuizBtn")?.addEventListener("click", handlePracticeNav);
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

  const coverageBadge = (status) => {
    const s = (status || "needs_review").toString().toLowerCase();
    if (s === "covered") return '<span class="badge badge-success">Strong Match</span>';
    if (s === "partially_covered") return '<span class="badge badge-warning">Partial Match</span>';
    if (s === "needs_review") return '<span class="badge badge-warning">Needs Review</span>';
    return '<span class="badge badge-error">Not Found</span>';
  };

  listContainer.innerHTML = data.matched_topics.map((t, idx) => {
    const isChecked = idx === selectedTopicIndex;
    const concepts = (t.matched_concepts || []).slice(0, 4).map(c => `
      <span class="notes-concept-chip">${escapeHtml(c)}</span>
    `).join("");
    const evidence = (t.evidence || []).map(e => `
      <div class="notes-evidence">
        <span class="notes-evidence-loc">${escapeHtml(e.location || "")}</span>
        <span class="notes-evidence-text">${escapeHtml(e.snippet || "")}</span>
      </div>
    `).join("");

    return `
      <div class="notes-topic-item ${isChecked ? 'selected' : ''}" data-index="${idx}" role="button" tabindex="0">
        <input type="radio" name="matchedTopicRadio" class="notes-topic-radio" id="topicRadio_${idx}" value="${idx}" ${isChecked ? 'checked' : ''} />
        <div class="notes-topic-body">
          <div class="notes-topic-header-row">
            <span class="notes-course-tag">${escapeHtml(t.course_code)} - ${escapeHtml(t.course_name)}</span>
            ${coverageBadge(t.coverage_status)}
          </div>
          <div class="notes-topic-name">${escapeHtml(t.topic_name)}</div>
          ${concepts ? `<div class="notes-concepts-row">${concepts}</div>` : ''}
          ${evidence ? `<div class="notes-evidence-row">${evidence}</div>` : ''}
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
    generationScreenOwner = null;
    lastCompletedResult = null;
    saveSessionState();
    showScreen("notesUploadSection");
  });

  $("setupNotesBtn")?.addEventListener("click", () => {
    if (isGenerating) return;
    generationScreenOwner = null;
    lastCompletedResult = null;
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
  generationScreenOwner = null;
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
    const coverage = (t.coverage_status || "needs_review").toString().toLowerCase();
    const matchBadge = coverage === "covered"
      ? '<span class="badge badge-success">Strong Match</span>'
      : coverage === "partially_covered"
        ? '<span class="badge badge-warning">Partial Match</span>'
        : coverage === "needs_review"
          ? '<span class="badge badge-warning">Needs Review</span>'
          : "";

    let subStatusText = "";
    if (statusKey === "mastered") {
      subStatusText = latestScore ? ` • Latest score: ${latestScore}` : "";
    } else if (statusKey === "attempted") {
      subStatusText = latestScore ? ` • Latest score: ${latestScore}` : ` • ${attempts} attempt${attempts === 1 ? '' : 's'}`;
    }

    const concepts = (t.matched_concepts || []).slice(0, 4).map(c => `
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


// ==========================================================================
// Student Authentication, Registration & Account Management
// ==========================================================================

function showAuthMessage(containerId, message, type = "error") {
  const box = $(containerId);
  if (!box) return;
  box.textContent = message;
  box.className = `auth-msg ${type}`;
  box.classList.remove("hidden");
}

function clearAuthMessage(containerId) {
  const box = $(containerId);
  if (!box) return;
  box.textContent = "";
  box.className = "auth-msg hidden";
}

function setAuthBtnLoading(btnId, isLoading, defaultText = "Submit") {
  const btn = $(btnId);
  if (!btn) return;
  btn.disabled = isLoading;
  const textSpan = btn.querySelector(".btn-text");
  const spinnerSpan = btn.querySelector(".btn-spinner");
  if (textSpan && spinnerSpan) {
    if (isLoading) {
      textSpan.textContent = "Please wait...";
      spinnerSpan.classList.remove("hidden");
    } else {
      textSpan.textContent = defaultText;
      spinnerSpan.classList.add("hidden");
    }
  }
}

function updateUserDisplay(name) {
  const el = $("userDisplayName");
  if (el) {
    el.textContent = name || "Student";
  }
}

async function checkSessionStatus() {
  if (!authToken && typeof localStorage !== "undefined" && localStorage.getItem) {
    authToken = localStorage.getItem("learnai_auth_token") || null;
  }
  if (!studentId && typeof localStorage !== "undefined" && localStorage.getItem) {
    studentId = localStorage.getItem("learnai_student_id") || null;
  }

  const headers = {};
  if (authToken && !isTokenExpired(authToken)) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }

  try {
    const res = await fetch(apiUrl("/api/auth/me"), {
      headers: headers,
      credentials: "include"
    });
    if (res.ok) {
      const data = await res.json();
      if (data.is_registered) {
        currentUser = data;
        studentId = data.student_id;
        updateUserDisplay(data.full_name || "Student");
        if (typeof localStorage !== "undefined" && localStorage.setItem) {
          localStorage.setItem("learnai_session_active", "1");
          localStorage.setItem("learnai_student_id", studentId);
          localStorage.setItem("learnai_user_name", data.full_name || "");
        }
        if (typeof document !== "undefined" && document.documentElement) {
          document.documentElement.classList.remove("unauthenticated");
        }
        return { status: "authenticated", user: data };
      }
    } else if (res.status === 401 || res.status === 403) {
      currentUser = null;
      if (typeof localStorage !== "undefined") {
        localStorage.removeItem("learnai_auth_token");
        localStorage.removeItem("learnai_session_active");
        localStorage.removeItem("learnai_student_id");
        localStorage.removeItem("learnai_user_name");
      }
      if (typeof document !== "undefined" && document.documentElement) {
        document.documentElement.classList.add("unauthenticated");
      }
      clearSessionState();
      authToken = null;
      studentId = null;
      return { status: "unauthenticated" };
    }
  } catch (err) {
    console.warn("[AUTH] Failed to verify user profile (network error):", err);
    return { status: "network_error", error: err };
  }

  currentUser = null;
  return { status: "unauthenticated" };
}

async function verifyCurrentUser() {
  const result = await checkSessionStatus();
  return result.status === "authenticated";
}

async function handleLoginSubmit(e) {
  if (e && e.preventDefault) e.preventDefault();
  clearAuthMessage("loginMsg");

  const emailInput = $("loginEmail");
  const passwordInput = $("loginPassword");
  const email = (emailInput?.value || "").trim();
  const password = passwordInput?.value || "";

  if (!email || !password) {
    showAuthMessage("loginMsg", "Please enter both your email address and password.", "error");
    return;
  }

  setAuthBtnLoading("loginSubmitBtn", true, "Login");

  try {
    const res = await fetch(apiUrl("/api/auth/login"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email, password: password }),
      credentials: "include"
    });

    const data = await res.json();
    if (!res.ok) {
      const errorMsg = data.detail || "Invalid email address or password.";
      showAuthMessage("loginMsg", errorMsg, "error");
      setAuthBtnLoading("loginSubmitBtn", false, "Login");
      return;
    }

    // Login successful
    authToken = data.token;
    studentId = data.student_id;
    currentUser = {
      student_id: data.student_id,
      full_name: data.full_name,
      email: data.email,
      is_registered: true
    };

    if (typeof localStorage !== "undefined" && localStorage.setItem) {
      localStorage.setItem("learnai_session_active", "1");
      localStorage.setItem("learnai_student_id", studentId);
      localStorage.setItem("learnai_user_name", data.full_name || "");
    }
    if (typeof document !== "undefined" && document.documentElement) {
      document.documentElement.classList.remove("unauthenticated");
    }

    updateUserDisplay(data.full_name);
    if (passwordInput) passwordInput.value = "";
    clearAuthMessage("loginMsg");
    setAuthBtnLoading("loginSubmitBtn", false, "Login");

    await loadCourses();
    const hasActive = await loadActiveQuizState();
    if (hasActive) {
      resumeActiveQuiz();
    } else {
      // Never fall back to a completed or latest result on login. Always show
      // topic selection unless there is an in-progress quiz to resume.
      lastCompletedResult = null;
      showScreen("setup");
    }
  } catch (err) {
    console.error("[AUTH] Login error:", err);
    showAuthMessage("loginMsg", "Connection error. Please check your internet connection.", "error");
    setAuthBtnLoading("loginSubmitBtn", false, "Login");
  }
}

async function handleRegisterSubmit(e) {
  if (e && e.preventDefault) e.preventDefault();
  clearAuthMessage("registerMsg");

  const nameInput = $("registerName");
  const emailInput = $("registerEmail");
  const passwordInput = $("registerPassword");
  const confirmPasswordInput = $("registerConfirmPassword");

  const fullName = (nameInput?.value || "").trim();
  const email = (emailInput?.value || "").trim();
  const password = passwordInput?.value || "";
  const confirmPassword = confirmPasswordInput?.value || "";

  // Client-side validations
  if (!fullName || fullName.length < 2) {
    showAuthMessage("registerMsg", "Please enter your full name (minimum 2 characters).", "error");
    nameInput?.focus();
    return;
  }

  const emailRegex = /^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+$/;
  if (!email || !emailRegex.test(email)) {
    showAuthMessage("registerMsg", "Please enter a valid email address.", "error");
    emailInput?.focus();
    return;
  }

  if (!password || password.length < 8) {
    showAuthMessage("registerMsg", "Password must be at least 8 characters long.", "error");
    passwordInput?.focus();
    return;
  }

  const hasLetter = /[a-zA-Z]/.test(password);
  const hasDigit = /[0-9]/.test(password);
  if (!hasLetter || !hasDigit) {
    showAuthMessage("registerMsg", "Password must contain at least one letter and one number.", "error");
    passwordInput?.focus();
    return;
  }

  if (password !== confirmPassword) {
    showAuthMessage("registerMsg", "Passwords do not match. Please re-enter your password.", "error");
    confirmPasswordInput?.focus();
    return;
  }

  setAuthBtnLoading("registerSubmitBtn", true, "Create account");

  try {
    const res = await fetch(apiUrl("/api/auth/register"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        full_name: fullName,
        email: email,
        password: password,
        confirm_password: confirmPassword
      }),
      credentials: "include"
    });

    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      data = null;
    }

    if (!res.ok) {
      let errorMsg = "Registration failed. Please check your information.";
      if (data && typeof data.detail === "string") {
        errorMsg = data.detail;
      } else if (data && Array.isArray(data.detail) && data.detail.length > 0) {
        errorMsg = data.detail.map(d => (typeof d === "string" ? d : d.msg || "")).filter(Boolean).join(". ") || errorMsg;
      } else if (res.status === 429) {
        errorMsg = "Too many registration attempts. Please wait a moment before trying again.";
      } else if (res.status >= 500) {
        errorMsg = "Server error while creating your account. Please try again shortly.";
      }
      showAuthMessage("registerMsg", errorMsg, "error");
      setAuthBtnLoading("registerSubmitBtn", false, "Create account");
      return;
    }

    // Registration successful
    authToken = data.token;
    studentId = data.student_id;
    currentUser = {
      student_id: data.student_id,
      full_name: data.full_name,
      email: data.email,
      is_registered: true
    };

    if (typeof localStorage !== "undefined" && localStorage.setItem) {
      localStorage.setItem("learnai_session_active", "1");
      localStorage.setItem("learnai_student_id", studentId);
      localStorage.setItem("learnai_user_name", data.full_name || "");
    }
    if (typeof document !== "undefined" && document.documentElement) {
      document.documentElement.classList.remove("unauthenticated");
    }

    updateUserDisplay(data.full_name);
    if (passwordInput) passwordInput.value = "";
    if (confirmPasswordInput) confirmPasswordInput.value = "";
    clearAuthMessage("registerMsg");
    setAuthBtnLoading("registerSubmitBtn", false, "Create account");

    // Enter main application
    goToHome();
  } catch (err) {
    console.error("[AUTH] Registration error:", err);
    showAuthMessage("registerMsg", "Connection error. Please try again.", "error");
    setAuthBtnLoading("registerSubmitBtn", false, "Create account");
  }
}

async function handleForgotPasswordSubmit(e) {
  if (e && e.preventDefault) e.preventDefault();
  clearAuthMessage("forgotMsg");
  $("devResetNotice")?.classList.add("hidden");

  const emailInput = $("forgotEmail");
  const email = (emailInput?.value || "").trim();

  if (!email) {
    showAuthMessage("forgotMsg", "Please enter your registered email address.", "error");
    return;
  }

  setAuthBtnLoading("forgotSubmitBtn", true, "Send reset link");

  try {
    const res = await fetch(apiUrl("/api/auth/forgot-password"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email })
    });

    const data = await res.json();
    setAuthBtnLoading("forgotSubmitBtn", false, "Send reset link");

    if (!res.ok) {
      showAuthMessage("forgotMsg", data.detail || "Unable to request password reset. Please try again.", "error");
      return;
    }

    showAuthMessage("forgotMsg", data.message || "If an account is associated with this email, password reset instructions have been generated.", "success");

    // Handle development mode token inspection
    if (data.dev_reset_token) {
      const devNotice = $("devResetNotice");
      const devCode = $("devResetTokenCode");
      if (devNotice && devCode) {
        devCode.textContent = data.dev_reset_token;
        devNotice.classList.remove("hidden");
      }
    }
  } catch (err) {
    console.error("[AUTH] Forgot password error:", err);
    showAuthMessage("forgotMsg", "Connection error. Please try again.", "error");
    setAuthBtnLoading("forgotSubmitBtn", false, "Send reset link");
  }
}

async function handleResetPasswordSubmit(e) {
  if (e && e.preventDefault) e.preventDefault();
  clearAuthMessage("resetMsg");

  const tokenInput = $("resetTokenInput");
  const newPwdInput = $("resetNewPassword");
  const confirmPwdInput = $("resetConfirmPassword");

  const token = (tokenInput?.value || "").trim();
  const newPassword = newPwdInput?.value || "";
  const confirmPassword = confirmPwdInput?.value || "";

  if (!token) {
    showAuthMessage("resetMsg", "Please paste or enter your reset token.", "error");
    return;
  }

  if (!newPassword || newPassword.length < 8) {
    showAuthMessage("resetMsg", "New password must be at least 8 characters long.", "error");
    return;
  }

  const hasLetter = /[a-zA-Z]/.test(newPassword);
  const hasDigit = /[0-9]/.test(newPassword);
  if (!hasLetter || !hasDigit) {
    showAuthMessage("resetMsg", "Password must contain at least one letter and one number.", "error");
    return;
  }

  if (newPassword !== confirmPassword) {
    showAuthMessage("resetMsg", "Passwords do not match. Please re-enter your new password.", "error");
    return;
  }

  setAuthBtnLoading("resetSubmitBtn", true, "Reset password");

  try {
    const res = await fetch(apiUrl("/api/auth/reset-password"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: token,
        new_password: newPassword,
        confirm_password: confirmPassword
      })
    });

    const data = await res.json();
    setAuthBtnLoading("resetSubmitBtn", false, "Reset password");

    if (!res.ok) {
      showAuthMessage("resetMsg", data.detail || "Failed to reset password. Token may be invalid or expired.", "error");
      return;
    }

    // Success: return to login view with success alert
    showScreen("loginSection");
    showAuthMessage("loginMsg", "Password successfully reset! Please log in with your new password.", "success");
    if (newPwdInput) newPwdInput.value = "";
    if (confirmPwdInput) confirmPwdInput.value = "";
    if (tokenInput) tokenInput.value = "";
  } catch (err) {
    console.error("[AUTH] Reset password error:", err);
    showAuthMessage("resetMsg", "Connection error. Please try again.", "error");
    setAuthBtnLoading("resetSubmitBtn", false, "Reset password");
  }
}


// ── Password Visibility Toggle Implementation ───────────────────────────────
const EYE_ICON_SVG = '<svg class="eye-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>';
const EYE_OFF_ICON_SVG = '<svg class="eye-off-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>';

function togglePasswordVisibility(btn) {
  if (!btn) return;
  const targetId = btn.getAttribute("data-target");
  const input = targetId ? document.getElementById(targetId) : null;
  if (!input) return;

  const isCurrentlyPassword = input.type === "password";
  input.type = isCurrentlyPassword ? "text" : "password";
  btn.innerHTML = isCurrentlyPassword ? EYE_OFF_ICON_SVG : EYE_ICON_SVG;
  btn.setAttribute("aria-label", isCurrentlyPassword ? "Hide password" : "Show password");
  btn.setAttribute("title", isCurrentlyPassword ? "Hide password" : "Show password");
  btn.setAttribute("aria-pressed", isCurrentlyPassword ? "true" : "false");
}

function initPasswordToggles() {
  if (typeof document === "undefined") return;
  const toggleButtons = document.querySelectorAll(".password-toggle-btn");
  toggleButtons.forEach(btn => {
    if (btn.dataset.toggleBound === "true") return;
    btn.dataset.toggleBound = "true";

    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      togglePasswordVisibility(btn);
    });

    btn.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        e.stopPropagation();
        togglePasswordVisibility(btn);
      }
    });
  });

  // Global delegated listener for resilient dynamic handling
  if (!document._learnaiPasswordToggleDelegated) {
    document._learnaiPasswordToggleDelegated = true;
    document.addEventListener("click", (e) => {
      const btn = e.target.closest ? e.target.closest(".password-toggle-btn") : null;
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      togglePasswordVisibility(btn);
    });
  }
}

function resetPasswordToggles() {
  if (typeof document === "undefined") return;
  const toggleButtons = document.querySelectorAll(".password-toggle-btn");
  toggleButtons.forEach(btn => {
    const targetId = btn.getAttribute("data-target");
    const input = targetId ? document.getElementById(targetId) : null;
    if (input) {
      input.type = "password";
    }
    btn.innerHTML = EYE_ICON_SVG;
    btn.setAttribute("aria-label", "Show password");
    btn.setAttribute("title", "Show password");
    btn.setAttribute("aria-pressed", "false");
  });
}

async function handleLogout() {
  // 0. Best-effort: abandon the student's in_progress quiz session(s) on the
  //    backend BEFORE the auth token is revoked. Wrapped in try/catch with a
  //    short timeout so logout never hangs even if the backend is unreachable.
  try {
    await abandonActiveQuizOnServer();
  } catch (e) {
    console.warn("[AUTH] Quiz abandonment on logout failed (non-fatal):", e);
  }
  generationScreenOwner = null;

  try {
    const headers = {};
    if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
    await fetch(apiUrl("/api/auth/logout"), {
      method: "POST",
      headers: headers,
      credentials: "include"
    });
  } catch (e) {
    console.warn("[AUTH] Logout request failed:", e);
  }

  // 1. Reset forms and clear input fields immediately
  const formIds = ["loginForm", "registerForm", "forgotPasswordForm", "resetPasswordForm"];
  formIds.forEach(fId => {
    const f = $(fId);
    if (f && typeof f.reset === "function") {
      try { f.reset(); } catch (e) {}
    }
  });

  const authFieldIds = [
    "loginEmail", "loginPassword",
    "registerName", "registerEmail", "registerPassword", "registerConfirmPassword",
    "forgotEmail", "resetNewPassword", "resetConfirmPassword"
  ];
  authFieldIds.forEach(id => {
    const el = $(id);
    if (el) {
      el.value = "";
      el.defaultValue = "";
      try { el.setAttribute("value", ""); } catch (e) {}
    }
  });

  // 2. Reset password visibility toggles back to masked state
  resetPasswordToggles();

  // 3. Clear auth messages
  clearAuthMessage("loginMsg");
  clearAuthMessage("registerMsg");
  clearAuthMessage("forgotMsg");
  clearAuthMessage("resetMsg");

  // 4. Invalidate application and session state
  currentUser = null;
  authToken = null;
  studentId = null;
  lastCompletedResult = null;

  if (typeof localStorage !== "undefined" && localStorage.removeItem) {
    localStorage.removeItem("learnai_auth_token");
    localStorage.removeItem("learnai_session_active");
    localStorage.removeItem("learnai_student_id");
    localStorage.removeItem("learnai_user_name");
    localStorage.removeItem("learnai_active_quiz");
    localStorage.removeItem("learnai_session_state");
    localStorage.removeItem("learnai_result_sync");
  }

  clearSessionState();
  if (typeof document !== "undefined" && document.documentElement) {
    document.documentElement.classList.add("unauthenticated");
  }

  showScreen("loginSection");
  showAuthMessage("loginMsg", "You have been logged out.", "success");
}

// Best-effort server-side abandonment of in_progress quiz sessions on logout.
// Never throws: logout must always complete locally.
async function abandonActiveQuizOnServer() {
  const ids = new Set();
  if (currentQuiz && (currentQuiz.quiz_id || currentQuiz.session_id)) {
    ids.add(currentQuiz.quiz_id || currentQuiz.session_id);
  }
  // Discover any server-side active session not present in memory.
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 5000);
    const res = await fetch(apiUrl("/api/quiz/active"), {
      method: "GET",
      headers: authToken ? { "Authorization": `Bearer ${authToken}` } : {},
      credentials: "include",
      signal: ctrl.signal
    });
    clearTimeout(t);
    if (res.ok) {
      const data = await res.json();
      if (data && data.session_id) ids.add(data.session_id);
    }
  } catch (e) {
    console.warn("[AUTH] Could not query active quiz on logout:", e);
  }
  for (const id of ids) {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 5000);
      await fetch(apiUrl("/api/quiz/abandon"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(authToken ? { "Authorization": `Bearer ${authToken}` } : {})
        },
        credentials: "include",
        body: JSON.stringify({ session_id: id }),
        signal: ctrl.signal
      });
      clearTimeout(t);
    } catch (e) {
      console.warn("[AUTH] Could not abandon quiz session", id, "on logout:", e);
    }
  }
}

function initAuthEventListeners() {
  initPasswordToggles();
  $("loginForm")?.addEventListener("submit", handleLoginSubmit);
  $("registerForm")?.addEventListener("submit", handleRegisterSubmit);
  $("forgotPasswordForm")?.addEventListener("submit", handleForgotPasswordSubmit);
  $("resetPasswordForm")?.addEventListener("submit", handleResetPasswordSubmit);

  $("forgotPasswordBtn")?.addEventListener("click", () => {
    clearAuthMessage("forgotMsg");
    $("devResetNotice")?.classList.add("hidden");
    const emailVal = $("loginEmail")?.value || "";
    if (emailVal && $("forgotEmail")) $("forgotEmail").value = emailVal;
    showScreen("forgotPasswordSection");
  });

  $("goToRegisterBtn")?.addEventListener("click", () => {
    clearAuthMessage("registerMsg");
    showScreen("registerSection");
  });

  $("goToLoginBtn")?.addEventListener("click", () => {
    clearAuthMessage("loginMsg");
    showScreen("loginSection");
  });

  $("forgotBackToLoginBtn")?.addEventListener("click", () => {
    clearAuthMessage("loginMsg");
    showScreen("loginSection");
  });

  $("resetBackToLoginBtn")?.addEventListener("click", () => {
    clearAuthMessage("loginMsg");
    showScreen("loginSection");
  });

  $("devUseTokenBtn")?.addEventListener("click", () => {
    const tokenVal = $("devResetTokenCode")?.textContent || "";
    showScreen("resetPasswordSection");
    if ($("resetTokenInput")) $("resetTokenInput").value = tokenVal;
  });

  $("logoutBtn")?.addEventListener("click", handleLogout);
}


// ── App Initialization ──────────────────────────────────────────────────────
async function initApp() {
  initAuthEventListeners();
  initNotesUploaderListeners();
  initStudyMaterialsListeners();

  const retryBtn = $("authCheckingRetryBtn");
  const loginBtn = $("authCheckingLoginBtn");
  if (retryBtn && !retryBtn._bound) {
    retryBtn._bound = true;
    retryBtn.addEventListener("click", () => {
      initApp();
    });
  }
  if (loginBtn && !loginBtn._bound) {
    loginBtn._bound = true;
    loginBtn.addEventListener("click", () => {
      if (typeof localStorage !== "undefined") {
        localStorage.removeItem("learnai_auth_token");
        localStorage.removeItem("learnai_student_id");
      }
      authToken = null;
      studentId = null;
      showScreen("loginSection");
    });
  }

  // Session present or checking: display session verification state
  showScreen("authCheckingSection");
  $("authCheckingSpinner")?.classList.remove("hidden");
  $("authCheckingErrorBox")?.classList.add("hidden");

  const sessionResult = await checkSessionStatus();

  if (sessionResult.status === "authenticated") {
    await loadCourses();
    const hasActive = await loadActiveQuizState();

    if (hasActive) {
      resumeActiveQuiz();
    } else {
      // Never fall back to a completed or latest result on init/refresh. Always
      // show topic selection unless there is an in-progress quiz to resume.
      lastCompletedResult = null;
      showScreen("setup");
    }

    // Initialize base history state if not already present
    if (typeof window !== "undefined" && window.history && !window.history.state) {
      try {
        window.history.replaceState({ screen: hasActive ? "quizSection" : "setup" }, "");
      } catch (e) {
        // Ignore if sandbox restricts replaceState
      }
    }

    // If no active quiz was restored, check URL parameters
    if (!hasActive && typeof window !== "undefined" && window.location && window.location.search) {
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
              populateTopicDropdown(urlCourse);
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
    return;
  }

  if (sessionResult.status === "network_error") {
    // Show network error box with retry and login buttons; do NOT erase token
    $("authCheckingSpinner")?.classList.add("hidden");
    $("authCheckingErrorBox")?.classList.remove("hidden");
    return;
  }

  // Unauthenticated (token invalid or revoked)
  showScreen("loginSection");
  loadCourses().catch(() => {});
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
      // Leaving the generation flow via Back/Forward: clear owner so a finishing
      // generation never auto-shows the quiz on an unrelated screen.
      generationScreenOwner = null;
    }
    if (targetScreen === "quizSection") {
      // In-memory resume only: never restore from storage on popstate.
      if ((quizStatus === "displayed" || quizStatus === "in_progress") && currentQuiz && Array.isArray(currentQuiz.questions) && currentQuiz.questions.length > 0) {
        quizStatus = "in_progress";
        renderQuizHeader(currentQuiz);
        renderQuestion();
        showScreen("quizSection", false);
        saveSessionState();
      } else {
        showScreen("setup", false);
      }
    } else if (targetScreen === "resultSection") {
      // Never show a completed result on popstate; always topic selection.
      lastCompletedResult = null;
      showScreen("setup", false);
    } else {
      showScreen(targetScreen, false);
      if (targetScreen === "dashboardSection") {
        loadDashboard();
      }
    }
  });
}

if (typeof window !== "undefined" && typeof document !== "undefined" && typeof require === "undefined") {
  initApp();
}

const _learnAiExports = {
  getApiBaseUrl,
  apiUrl,
  setLastCompletedResult: (r) => { lastCompletedResult = r; },
  getLastCompletedResult: () => lastCompletedResult,
  isTokenExpired,
  escapeHtml,
  LearnAICodeRenderer,
  LearnAIMathRenderer,
  populateTopicDropdown,
  setCurriculum: (c) => { curriculum = c; },
  getCurriculum: () => curriculum,
  setLastExtractionResult: (r) => { lastExtractionResult = r; },
  getLastExtractionResult: () => lastExtractionResult,
  checkSessionStatus,
  hasActiveQuiz,
  loadActiveQuizState,
  resumeActiveQuiz,
  restoreSessionState,
  saveSessionState,
  clearSessionState,
  handlePracticeNav,
  initPasswordToggles,
  resetPasswordToggles,
  initApp,
  abandonActiveQuizOnServer
};

if (typeof window !== "undefined") {
  window.LearnAIApp = _learnAiExports;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = _learnAiExports;
}
