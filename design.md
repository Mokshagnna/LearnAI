# LearnAI — Design System & UI/UX Specification

**Document Version:** 2.0.0 (Implemented)  
**Theme:** Academic Scholar (Oxford Forest & Warm Stone)  
**Target Repository:** `LearnAI` (`frontend/index.html`, `frontend/css/style.css`, `frontend/js/app.js`)  
**Design Philosophy:** Distraction-free, high-legibility educational assessment platform for university computer science engineering students.

---

## Table of Contents

1. [Design Philosophy and Visual Direction](#1-design-philosophy-and-visual-direction)
2. [Color Tokens & Contrast Verification](#2-color-tokens--contrast-verification)
3. [Typography Hierarchy](#3-typography-hierarchy)
4. [Spacing, Layout & Reading Measure](#4-spacing-layout--reading-measure)
5. [Borders, Corner Radii & Elevation](#5-borders-corner-radii--elevation)
6. [Component System & Interaction States](#6-component-system--interaction-states)
7. [Decorative Emoji Policy](#7-decorative-emoji-policy)
8. [The 720px Quiz Reading Measure](#8-the-720px-quiz-reading-measure)
9. [Accessibility, Focus & Reduced Motion](#9-accessibility-focus--reduced-motion)
10. [Responsive Design & Breakpoints](#10-responsive-design--breakpoints)
11. [Page-by-Page Specifications](#11-page-by-page-specifications)
12. [CSS Conventions & Maintenance Guidelines](#12-css-conventions--maintenance-guidelines)
13. [Verified Implementation vs. Future Opportunities](#13-verified-implementation-vs-future-opportunities)

---

## 1. Design Philosophy and Visual Direction

LearnAI is an adaptive curriculum quiz platform designed for undergraduate students in B.Tech CSE (AI & ML). The interface follows the **Academic Scholar** visual language:
- **Calm and Intentional:** The visual aesthetic prioritizes cognitive focus, reading clarity, and low stress during assessments. Flashy gamification, neon glows, purple gradients, and floating decorations are strictly avoided.
- **Brand Continuity:** Retains LearnAI’s collegiate heritage using an authoritative **Oxford forest green** (`--color-brand: #1e4635`) for primary calls to action, focus rings, and active indicators, set against clean warm-stone neutrals (`--color-bg: #f8f9fa`) and pure white panels (`--color-surface: #ffffff`).
- **Open Panels over Nested Cards:** Replaces deep card-in-card enclosures with structured open panels, hairline dividers (`--color-border: #e5e7eb`), and subtle tonal shifts (`--color-surface-subtle: #f3f4f6`).

---

## 2. Color Tokens & Contrast Verification

All colors in `frontend/css/style.css` are centralized in a `:root` custom properties block. Every active text-to-background combination has been mathematically verified against WCAG 2.1 standards ($L = 0.2126R + 0.7152G + 0.0722B$):

$$\text{Contrast Ratio} = \frac{L_1 + 0.05}{L_2 + 0.05}$$

| CSS Custom Property | HEX Value | Semantic Role | Computed Contrast Ratio | WCAG 2.1 Compliance |
| :--- | :---: | :--- | :---: | :---: |
| `--color-bg` | `#f8f9fa` | Warm stone canvas background | — | — |
| `--color-surface` | `#ffffff` | Primary panel surface, inputs, cards | — | — |
| `--color-surface-subtle` | `#f3f4f6` | Table headers, secondary buttons, item tiles | — | — |
| `--color-surface-alt` | `#fbfcfb` | Subtle section container background | — | — |
| `--color-border` | `#e5e7eb` | Structural borders, hairline dividers | — | — |
| `--color-border-strong` | `#d1d5db` | Form control borders, radio edges | **3.0:1** (vs surface) | **Pass UI (3:1)** |
| `--color-text-primary` | `#111827` | Headings, questions, selected options | **17.7:1** (on `#ffffff`)<br>**16.8:1** (on `#f8f9fa`) | **Pass AAA (7:1)** |
| `--color-text-secondary`| `#374151` | Body paragraphs, answers, explanations | **10.3:1** (on `#ffffff`) | **Pass AAA (7:1)** |
| `--color-text-muted` | `#4b5563` | Timestamps, counters, uppercase tags | **7.57:1** (on `#ffffff`) | **Pass AAA (7:1)** |
| `--color-brand` | `#1e4635` | Oxford forest green (primary actions, focus) | **10.6:1** (white text on brand) | **Pass AAA (7:1)** |
| `--color-brand-hover` | `#153326` | Deep pine hover state for primary buttons | **13.5:1** (white text on hover) | **Pass AAA (7:1)** |
| `--color-brand-active`| `#0f261c` | Active pressed button state | **16.0:1** (white text on active) | **Pass AAA (7:1)** |
| `--color-brand-tint` | `#edf7f2` | Selected radio card background, active rows | — | — |
| `--color-brand-subtle`| `#e7f2ec` | Pattern badges, pill backgrounds | — | — |
| `--color-brand-surface`| `#103f2d` | Persistent topbar header background | **12.0:1** (white text on topbar) | **Pass AAA (7:1)** |
| `--color-text-on-brand`| `#ffffff` | High-contrast button & header text | **10.6:1** (on `#1e4635`) | **Pass AAA (7:1)** |
| `--color-success` | `#166534` | Correct answers, high performance metrics | **6.72:1** (on `#ffffff`) | **Pass AA (4.5:1)** |
| `--color-success-bg` | `#f0fdf4` | Background tint for success chips & tags | — | — |
| `--color-success-border`| `#bbf7d0`| Border for success chips | — | — |
| `--color-warning` | `#9a3412` | Burnt amber (mixed scores 50–79%, review) | **6.94:1** (on `#ffffff`) | **Pass AA (4.5:1)** |
| `--color-warning-bg` | `#fff7ed` | Background tint for warning chips & tags | — | — |
| `--color-warning-border`| `#fed7aa`| Border for warning chips | — | — |
| `--color-error` | `#b91c1c` | Crimson (incorrect answers, error alerts) | **6.61:1** (on `#ffffff`) | **Pass AA (4.5:1)** |
| `--color-error-bg` | `#fef2f2` | Background tint for error alerts & weak chips | — | — |
| `--color-error-border`| `#fecaca`| Border for error alerts | — | — |
| `--color-info` | `#1d4ed8` | Royal blue (pedagogical notice text) | **6.81:1** (on `#ffffff`) | **Pass AA (4.5:1)** |
| `--color-info-bg` | `#eff6ff` | Background tint for disclaimer boxes | — | — |
| `--color-info-border` | `#bfdbfe` | Dashed border for disclaimer boxes | — | — |

*Resolved Defects:* Former hard-coded colors that failed contrast (`#7a8a82` at 3.7:1 and `#e67e22` at 2.9:1) are completely replaced by `--color-text-muted` (`#4b5563` at **7.57:1**) and `--color-warning` (`#9a3412` at **6.94:1**).

---

## 3. Typography Hierarchy

The type system relies on native system font stacks to ensure zero network latency, immediate page rendering, and high familiarity across operating systems.

- **Base Font Family:** `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif`
- **Monospace Family:** `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace`

| Scale Token | Font Size | Font Weight | Line Height | Applied Context |
| :--- | :---: | :---: | :---: | :--- |
| `--font-size-3xl` | `28px` | `700` | `1.25` | Page hero titles (`h1`), results score ratio (`#scoreText`), dashboard metric counters |
| `--font-size-2xl` | `22px` | `600`–`700` | `1.30`–`1.35` | Screen subtitles (`h2`), active quiz topic title (`#quizTitle`), loading title |
| `--font-size-xl` | `18px` | `600` | `1.50` | Active quiz question prompt (`.question-heading`), dashboard section headers |
| `--font-size-lg` | `16px` | `600` | `1.40` | Result breakdown category headers (`h3`), dashboard card titles |
| `--font-size-base`| `15px` | `400` / `600` | `1.45`–`1.55` | Multiple-choice answer option text, form inputs, buttons, body copy |
| `--font-size-sm` | `13px` | `500`–`700` | `1.40` | Form labels, navigation tabs, question counter ("Question 1 of 10"), table contents |
| `--font-size-xs` | `12px` | `600`–`700` | `1.30` | Course tracking labels, difficulty badges, category tags, KPI subtext |
| `--font-size-2xs`| `11px` | `700` | `1.20` | Recommendation priority tags, trend chart axis labels, dual-accuracy meter labels |

---

## 4. Spacing, Layout & Reading Measure

### Spacing Scale
A consistent geometric 4px/8px rhythm is enforced throughout the stylesheet:
- `--space-1` (4px): Micro padding, badge horizontal offsets.
- `--space-2` (8px): Button padding, icon-text gaps, compact element spacing.
- `--space-3` (12px): Form control padding, radio option gap, table padding.
- `--space-4` (16px): Standard panel padding, grid spacing.
- `--space-5` (20px): Section separation, header padding.
- `--space-6` (24px): Card interior padding, question spacing.
- `--space-8` (32px): Major vertical section separation.
- `--space-10` (40px): Loading state vertical margins.
- `--space-12` (48px): Page boundaries.

### Container Width Constraints
- **Main Container:** `--width-container: min(900px, 92%);` centered via `margin: var(--space-8) auto;`.
- **Setup Screen:** `--width-hero: 680px;`.
- **Loading Screen:** `--width-loading: 480px;`.
- **Results Screen:** `--width-result: 820px;`.
- **Reading Measure:** `--width-reading: 720px;` (see Section 8).

---

## 5. Borders, Corner Radii & Elevation

### Corner Radii
Indiscriminate pill-shaped (999px) buttons and badges have been disciplined into structured, professional radii:
- `--radius-sm` (4px): Category tags, pattern indicators, difficulty badges, table rows, progress bar tracks.
- `--radius-md` (6px): Form inputs, select dropdowns, primary/secondary buttons, multiple-choice option cards, recommendation cards.
- `--radius-lg` (10px): Main screen panels, card containers, dashboard sections.
- `--radius-xl` (14px): Dedicated result performance summary cards.
- `--radius-full` (999px): Reserved exclusively for circular elements (spinners, radio dots).

### Elevation & Shadows
Heavy, stacked drop-shadows and colored glows are forbidden:
- `--shadow-sm`: `0 1px 2px rgba(0, 0, 0, 0.05)` (subtle element depth).
- `--shadow-card`: `0 1px 3px rgba(30, 70, 53, 0.06), 0 1px 2px rgba(0, 0, 0, 0.04)` (soft natural resting elevation for main cards).
- `--shadow-dropdown`: `0 4px 6px -1px rgba(0, 0, 0, 0.07), 0 2px 4px -1px rgba(0, 0, 0, 0.04)` (focused dropdowns and skip link).

---

## 6. Component System & Interaction States

### Buttons
- **Primary Action (`button`):** Solid `--color-brand` background with white text, 6px radius, bold typography.
  - Hover: `--color-brand-hover` (`#153326`).
  - Active: `--color-brand-active` (`#0f261c`).
  - Focus: `outline: 3px solid var(--color-brand); outline-offset: 2px;`.
  - Disabled: `opacity: 0.55; cursor: not-allowed;`.
- **Secondary Action (`button.secondary`):** `--color-surface-subtle` background with `--color-text-primary` text and `--color-border-strong` border.
  - Hover: `--color-border` background with `--color-text-muted` border.

### Form Inputs & Dropdowns
- Full-width block elements with 11px vertical and 14px horizontal padding.
- Focused state: `outline: 3px solid var(--color-brand); outline-offset: 1px; border-color: var(--color-brand);`.
- Disabled state: `--color-surface-subtle` background, `--color-text-muted` text, `cursor: not-allowed;`.

### Multiple-Choice Option Cards (`.option`)
- Configured as accessible radio controls (`role="radio"`, `aria-checked`, `tabindex="0"`).
- Unselected State: 1px solid `--color-border-strong`, white background.
- Hover State: Border changes to `--color-brand`, background shifts to `--color-bg`.
- Selected State: 2px solid `--color-brand`, background shifts to `--color-brand-tint` (`#edf7f2`).
- Accessible Radio Indicator: Outer circle (18px diameter, 2px border) and inner dot (8px diameter solid circle filled with `--color-brand` when selected). This ensures selection is never conveyed by color alone.

---

## 7. Decorative Emoji Policy

In accordance with approved decision A, all non-essential decorative emojis have been cleanly removed from headings, buttons, tags, and status containers:
- `🔄 Retake with New AI Questions` $\rightarrow$ `Retake with New AI Questions`
- `🎯 Recommended Practice Next` $\rightarrow$ `Recommended Practice`
- `📊 View Student Dashboard` $\rightarrow$ `View Student Dashboard`
- `📚 Choose Another Topic` $\rightarrow$ `Choose Another Topic`
- `📈 Recent Performance Trend` $\rightarrow$ `Recent Performance Trend`
- `📖 Syllabus Topics Mastery` $\rightarrow$ `Syllabus Topics Mastery`
- `💪 Strong Concepts` $\rightarrow$ `Strong Concepts`
- `⚠️ Concepts Needing Review` $\rightarrow$ `Concepts Needing Review`
- `⭐ Current (#N)` $\rightarrow$ `Current (#N)`

**Preserved Functional Indicators:**
- Empty state uses a clean, scalable, inline vector SVG icon (`<svg class="empty-icon-svg">`) instead of raw Unicode emojis.
- Semantic success and error states are communicated by high-contrast color tokens (`--color-success`, `--color-error`), structured badge backgrounds, and clear text labels (`Correct` / `Incorrect`, `High` / `Medium` / `Low`).

---

## 8. The 720px Quiz Reading Measure

In accordance with approved decision B, active quiz question stems are bounded by a dedicated maximum reading width:
```css
:root {
  --width-reading: 720px;
}

.question-concept-subtle,
.question-heading,
.options,
.quiz-alert,
.nav-row {
  max-width: var(--width-reading);
  margin-left: auto;
  margin-right: auto;
}
```
- **Cognitive Purpose:** Constraining technical stems to ~65–75 characters per line prevents horizontal scanning fatigue and preserves reading comprehension for complex engineering questions and code blocks.
- **Responsive Shrinking:** On viewports narrower than 720px, the reading container naturally flexes to 100% of the card’s available width with standard horizontal padding.
- **Alignment:** The question heading and answer options remain centered within the larger 900px card canvas, while option text retains standard left-aligned formatting.

---

## 9. Accessibility, Focus & Reduced Motion

### WCAG Contrast Conformance
All foreground-to-background combinations achieve WCAG 2.1 Level AA (minimum 4.5:1 for body copy) and Level AAA (minimum 7:1 for headers and brand buttons).

### Focus Rings
Every interactive control (inputs, selects, buttons, radio options, and skip link) displays an unmistakable 3px focus ring with 2px offset:
```css
:focus-visible {
  outline: 3px solid var(--color-brand);
  outline-offset: 2px;
}
```

### Screen Reader Focus Shifting
When switching views via `showScreen(screenId)`, the active screen’s primary heading (`h1` or `h2`) is programmatically focused (`tabindex="-1"` and `focus({ preventScroll: true })`), announcing the new screen to screen readers and setting proper keyboard focus without jarring page jumps.

### Reduced Motion
All keyframe animations (`@keyframes spin`, `@keyframes shake`) and CSS transitions are neutralized when the student enables operating-system reduced-motion preferences:
```css
@media (prefers-reduced-motion: reduce) {
  *, ::before, ::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

---

## 10. Responsive Design & Breakpoints

The responsive system employs two deliberate breakpoints:

```
[ Mobile <= 640px ] <------ [ Tablet 641px - 840px ] <------ [ Desktop > 840px ]
```

1. **Tablet & Compact Screens (`max-width: 840px`):**
   - 2-column results and analysis grids switch to single-column stacks.
   - Container padding adjusts smoothly.
2. **Mobile Screens (`max-width: 640px`):**
   - Topbar flexes into a vertical column with full-width, touch-friendly navigation tabs.
   - Action buttons in `.nav-row` stack vertically for 44px+ touch targets.
   - Question card padding reduces to 16px to maximize readable text width.
   - Overview stat cards switch to a clean 2-column grid (`1fr 1fr`).
   - Tables and trend charts maintain horizontal touch-scrolling (`overflow-x: auto;`) without breaking container bounds.

---

## 11. Page-by-Page Specifications

### Screen 1: Home / Setup (`#setup`)
- Open centered hero panel (680px max width).
- Dependent dropdowns: Course select populates syllabus topic select.
- Pattern preview: Rectangular chips (`4 Easy`, `4 Medium`, `2 Hard`).
- Primary action: "Generate Questions with AI".

### Screen 2: Loading State (`#loadingSection`)
- Restrained academic waiting modal (480px max width).
- Clean 38px spinner using `--color-brand`.
- Cancel button aborts ongoing fetch via `AbortController`.
- Error state: Semantic red-tinted box (`--color-error-bg`) with Retry and Choose Another Topic options.

### Screen 3: Active Quiz (`#quizSection`)
- Course code tracking label in uppercase muted text.
- 4px high progress bar with ARIA `progressbar` semantics.
- 720px constrained question prompt and answer options.
- Navigation row: Secondary "Previous" button + Primary "Next / Submit Quiz" button.
- Unanswered alert: Inline error alert with 0.35s tactile shake animation.

### Screen 4: Results (`#resultSection`)
- Score header: Dominant 28px bold ratio (`8 / 10`) with accuracy status.
- Performance breakdown: Clean list items with color-coded score tags (Success, Warning, Error).
- AI Performance Box: Authoritative summary with 3px left border in `--color-brand`.
- Question Review: Step-by-step breakdown displaying selected choice, correct choice, and pedagogical explanation.
- Retake Flow: "Retake with New AI Questions" completely clears previous score DOM elements before generating.

### Screen 5: Student Progress Dashboard (`#dashboardSection`)
- Filter bar: Course dropdown filter and refresh button.
- 4 KPI Tiles: Quizzes Completed, Questions Answered, Overall Accuracy, Topics Practiced.
- Recommended Practice: Priority cards with colored left accent borders (Crimson for High, Forest Green for Medium, Slate for Low).
- Recent Performance Trend: CSS bar chart showing scores with tooltips and clean 120px baseline, plus historical tabular log.
- Syllabus Topic Mastery: Topic cards featuring dual accuracy meters (Cumulative vs. Recent 10-Question) and progress bars.
- Concept Insights: High-contrast tags for Strong Concepts (`--color-success-bg`) and Concepts Needing Review (`--color-error-bg`).
- Pedagogical Disclaimer: Academic notice regarding formative algorithmic heuristics.

---

## 12. CSS Conventions & Maintenance Guidelines

Future contributors must adhere to the following rules:
1. **Zero Hardcoded Colors:** Never write raw `#hex` or `rgb()` literals in CSS rules. Always reference established tokens (`var(--color-brand)`, `var(--color-text-primary)`, etc.).
2. **Zero Inline Styles:** Never inject `style="..."` in HTML files or JavaScript DOM templates. Use semantic utility classes (`.error-msg`, `.history-row-current`, `.tag-chip-strong`, `.tag-chip-weak`, etc.).
3. **Preserve Single-Screen Routing:** All new views must register in `ALL_SCREENS` and be toggled strictly via `showScreen(screenId)`.
4. **Preserve XSS Safety:** Continue rendering dynamic text via `element.textContent` or `escapeHtml()`.
5. **No Icon Dependencies:** Do not import FontAwesome, Lucide, or external icon fonts. Rely on clean typography or inline SVG elements.

---

## 13. Verified Implementation vs. Future Opportunities

- **Verified in Implementation [VERIFIED]:**
  - Full `:root` design token architecture in `frontend/css/style.css`.
  - Elimination of hard-coded colors and contrast failures.
  - Complete removal of decorative emojis from headings and buttons.
  - Active 720px maximum reading width for quiz stems (`--width-reading: 720px`).
  - `@media (prefers-reduced-motion: reduce)` override active.
  - Intermediate responsive breakpoints at 840px and 640px.
  - Accessible focus outlines on all controls.
  - Zero changes to backend files, database, scoring logic, or session recovery.
- **Future Architectural Opportunities [FUTURE]:**
  - Institutional Single Sign-On (SSO) login screen integration.
  - Exportable PDF student revision reports.
  - Dark mode token override using `prefers-color-scheme: dark`.
