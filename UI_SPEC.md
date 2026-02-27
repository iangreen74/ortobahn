# Ortobahn Dashboard UI Specification

> This document is the single source of truth for the tenant dashboard design.
> All UI changes must stay consistent with these patterns. Do not drift.

## Layout Architecture

- **Framework**: PicoCSS v2 dark theme (`data-theme="dark"`)
- **Fonts**: Inter (body), JetBrains Mono (code)
- **Sidebar**: Fixed `240px`, dark `#13131a`, full viewport height
- **Main content**: Left margin `240px`, max-width `1200px`, centered, padding `2rem`
- **Mobile**: Sidebar collapses at `768px` breakpoint to slide-in drawer

## Color Palette

### Accent Colors

| Token | Hex | Usage |
|-------|-----|-------|
| `--accent` | `#6366f1` | Indigo primary — buttons, active states, borders |
| `--accent-light` | `#818cf8` | KPI values, hover text |
| `--teal` | `#00d4aa` | Secondary accent — Glass posts, contrast CTA |
| `--accent-gradient` | `135deg #6366f1 → #8b5cf6` | Primary button, logo |

### Status Colors

| Status | Color | Background |
|--------|-------|------------|
| Published / Completed | `#22c55e` green | `rgba(34, 197, 94, 0.1)` |
| Draft / Running | `#f59e0b` orange | `rgba(245, 158, 11, 0.1)` |
| Failed / Rejected | `#ef4444` red | `rgba(239, 68, 68, 0.1)` |
| Info | `#3b82f6` blue | `rgba(59, 130, 246, 0.1)` |

### Backgrounds

| Layer | Color |
|-------|-------|
| Page | `#0d0d12` |
| Sidebar / Elevated | `#13131a` |
| Card (glass) | `rgba(255, 255, 255, 0.03)` |
| Card hover | `rgba(255, 255, 255, 0.06)` |
| Input | `rgba(255, 255, 255, 0.05)` |

### Text

| Type | Color |
|------|-------|
| Primary | `#f0f0f5` |
| Secondary | `rgba(240, 240, 245, 0.6)` |
| Tertiary | `rgba(240, 240, 245, 0.4)` |

## Badge Classes

```
.badge               — pill shape, 0.75rem, weight 600
.badge.completed     — green (published, approved)
.badge.draft         — orange (draft, running)
.badge.failed        — red (failed, rejected)
.badge.skipped       — gray
.badge.bluesky       — blue
.badge.twitter       — twitter blue
.badge.linkedin      — linkedin blue
```

## Card Patterns

| Class | Purpose | Styling |
|-------|---------|---------|
| `.kpi-card` | Dashboard KPI metrics | 3px indigo top border, centered values |
| `.draft-card` | Review queue items | Subtle border, 12px radius, hover border glow |
| `.glass-status-card` | Pipeline status | Indigo-tinted bg, 1rem padding |
| `.glass-agent-card` | Activity agent actions | 3px indigo left border |
| `.glass-post-card` | Activity post actions | 3px teal left border |
| `.analytics-post-card` | Top posts | 3px indigo left border |
| `.voice-confidence-card` | Voice match meter | Indigo+teal gradient bg |
| `.best-post-card` | Top performing post | Indigo+teal gradient bg |

## KPI Card Format

```html
<article class="kpi-card">
  <div class="kpi-value">{value}</div>
  <div class="kpi-label">LABEL</div>
  <div class="kpi-sublabel">optional detail</div>
</article>
```
- `.kpi-value`: 2.5rem, weight 700, `--accent-light`
- `.kpi-label`: 0.8rem, uppercase, letter-spacing 0.04em
- `.kpi-sublabel`: 0.7rem, `--text-tertiary`

## Sidebar Navigation

### Structure
1. **Brand** — Logo link to `/my/dashboard` with gradient text
2. **Pipeline pulse** — HTMX `every 5s`, animated dot indicator
3. **Search** — Live search with `delay:300ms`, overlay modal
4. **Main nav**: Home, Review Queue (with badge count)
5. **CONTENT section**: Posts, Articles, Calendar
6. **INSIGHTS section**: Performance, Activity
7. **Footer**: Settings, Logout

### Nav Item States
- Normal: `--text-secondary`, transparent bg
- Hover: `--text-primary`, `rgba(255, 255, 255, 0.06)` bg
- Active: White text, indigo bg with glow shadow

## Page Specifications

### Dashboard (`/my/dashboard`)

Section order:
1. Alert banners (credential issues, subscription)
2. Hero greeting (dynamic morning/afternoon/evening)
3. KPI grid (4 cards: Published, Engagement, Platforms, Weekly Trend)
4. Voice Confidence card (if reviews > 0)
5. Best Post highlight (if engagement > 0)
6. Create Content CTA with feature flags (Auto-publish, AI Images, Articles)
7. Pipeline status bar (HTMX every 5s)
8. Review nudge (if drafts > 0)

### Review Queue (`/my/review`)

Section order:
1. Page header with subtitle
2. Action bar (draft count + Publish All button)
3. Draft cards (platform badge, confidence, text, Edit/Publish/Reject)
4. Empty state (checkmark + "All caught up" message)
5. Engagement replies section (if any)

### Performance (`/my/performance`)

Section order:
1. Page header
2. KPI grid (4 cards: Total Posts, Engagement, Avg Engagement, Best Platform)
3. Engagement Trend bar chart (7 days)
4. Platform Breakdown table
5. Top Posts (5, with `.analytics-post-card`)
6. Recent Performance table (last 10 posts)

### Calendar (`/my/calendar`)

Section order:
1. Page header
2. Calendar grid (HTMX load once, 7-column CSS grid)
3. Legend (collapsible details element)

Calendar cell: min-height 80px, day number + color-coded badge dots, today outlined in indigo.

### Articles (`/my/articles`)

Section order:
1. Flash messages
2. Page header
3. Warning banner (if no article platforms configured)
4. Action bar (count + Generate Article button)
5. Articles table (Title, Status, Words, Confidence, Actions)
6. Empty state

### Activity (`/my/activity`)

Section order:
1. Page header
2. Pipeline Runs table (Run ID, Status, Posts, Tokens, Started, Completed)
3. Recent Events feed (activity dots with status colors)
4. Empty states for each section

### Settings (`/my/settings`)

Section order:
1. Subscription (non-internal only)
2. Brand Profile form
3. Content Sources
4. Automation (auto-publish, image gen, target platforms, posting frequency)
5. Article Publishing (enable, auto-publish, platforms, frequency, topics, voice, length)
6. Platform Credentials (Bluesky, Twitter, LinkedIn, Reddit, Medium, Substack)
7. API Keys table

## HTMX Polling Intervals

| Interval | Endpoints | Purpose |
|----------|-----------|---------|
| Every 5s | Pipeline pulse, pipeline status bar | Live pipeline monitoring |
| Every 10s | Toasts | Notification delivery |
| Every 15s | Review count badge | New draft detection |
| Every 30s | KPI cards (dashboard, analytics) | Metric refresh |
| Load once | Calendar grid | Initial render, no polling |

## Button Styles

| Class | Background | Usage |
|-------|------------|-------|
| (default) | Indigo gradient | Primary actions |
| `.outline` | Transparent + border | Secondary actions |
| `.secondary` | `rgba(255, 255, 255, 0.06)` | Tertiary actions |
| `.contrast` | Teal `#00d4aa` | Highlighted CTA |

## Empty State Pattern

```html
<article>
  <div class="empty-state">
    <div class="empty-state-icon">{emoji}</div>
    <p>{message}</p>
    <p><a href="...">{CTA link}</a></p>
  </div>
</article>
```

## Glass Morphism

- **Backdrop**: `blur(12px)` with `-webkit-` prefix
- **Border**: 1px subtle rgba
- **Background**: Very low opacity rgba
- **Radius**: 8px (small) to 16px (large)

## Key Design Principles

1. **Status badges always use the same color scheme** — green=published, orange=draft, red=failed
2. **Cards use `.article` or `.*-card` classes** — never bare containers
3. **Typography hierarchy**: h1 (hero 2.5rem) → h2 (section 1.25rem) → h4 (subsection 0.95rem)
4. **Consistent spacing**: 0.5rem, 0.75rem, 1rem, 1.5rem gaps
5. **No arbitrary shadows** — only design token shadows (`--shadow-sm/md/lg/glow`)
6. **Forms**: PicoCSS base + dark input override, consistent label styling
7. **Empty states**: Always include icon + message + CTA link
8. **Flash messages**: `.flash-msg` with `.success/.info/.warning/.error`
9. **Feature flags**: Shown as badge row in Create Content section
10. **Animations**: `0.2s ease` transitions, `pulse-glow` for live status
