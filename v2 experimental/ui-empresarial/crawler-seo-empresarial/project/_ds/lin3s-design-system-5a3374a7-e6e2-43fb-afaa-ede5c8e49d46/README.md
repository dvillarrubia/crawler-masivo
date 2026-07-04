# LIN3S Design System

> Digital experts unlocking your potential. **Powered by data. Driven by humans.**

LIN3S is a digital business consultancy. This design system encodes how LIN3S looks, sounds and behaves across decks, social, internal docs, and product UI. Everything here is meant to be **copied into real artifacts** — fonts, tokens, components, slides.

---

## 1. Sources & inputs

This system was built from materials supplied directly in the brief. The source list:

- **Brand brief** (in-conversation) — color palette, typography rules, slide types, brand rule about the LIN3S wordmark, ORIA methodology.
- **Font files** (`uploads/`) — Inter 18pt Bold, Light, Regular, SemiBold.
- **Inter family** (`Inter/` mounted folder) — full static + variable Inter family. Black, Medium, Italic copied in to complete the weight ladder.
- **Besley** — display accent font. Not supplied as a file; loaded from Google Fonts. ⚠ **See "Font substitutions" below.**

No Figma, no codebase, no slide deck was attached, so the recreations (UI kit + sample slides) are built from the written brand brief, not from product source. Treat them as opinionated interpretations; please point us at the real artifacts to align.

---

## 2. Brand at a glance

| | |
|---|---|
| **Name** | LIN3S — always full uppercase. Never rotate, tilt, distort, or animate-distort. |
| **Pronunciation** | "lines" |
| **Promise** | Powered by data. Driven by humans. |
| **Tone** | Expert, direct, warm. No jargon. Results-focused. |
| **Methodology** | **ORIA** — Objetivos, Retos, Iniciativas, Acciones (Objectives, Challenges, Initiatives, Actions) |
| **Diagnostic frame** | Where you are → Where you want to be → What's stopping you → How you achieve that |

---

## 3. Content fundamentals

### Voice
- **Expert without being academic.** Specialist vocabulary is fine when it's load-bearing ("audit", "roadmap", "KPI", "discovery") — but never as filler.
- **Direct.** Lead with the outcome. Cut adverbs. Cut hedges. Say "we will" not "we aim to try and".
- **Warm.** First-person plural ("we", "our team") for LIN3S; second-person ("you", "your business") for the client. Never "the user" when "you" works.
- **Results-focused.** Every claim earns its place with either a number, a name, or a verb that produces something.

### Casing
- **Sentence case** for headings, navigation, buttons, slide titles. Title Case is avoided.
- **UPPERCASE** is reserved for: the LIN3S wordmark, eyebrows/labels above content, the ORIA acronym, and short editorial moments on cover or section slides (one short phrase, never a paragraph).
- **No ALL CAPS paragraphs.** Ever.

### Punctuation & rhythm
- Periods at the end of full sentences. Headings get no terminal period.
- Em-dashes — used freely — for parenthetical asides; this is a brand rhythm.
- Numerals as digits from 2 onward. "One client" / "12 markets".
- Percentages always with the symbol: `+28%`, never "28 percent".
- Spell out "and" — avoid `&` outside legal copy.

### Words to prefer / avoid
| Prefer | Avoid |
|---|---|
| unlock, ship, build, decide | leverage, synergize, ideate |
| outcome, result, KPI | impact (overused), holistic |
| team, humans, people | resources, headcount |
| audit, roadmap, discovery | deep-dive, journey (the word, not the noun) |
| you, your business | the user, the customer base |

### I vs you
- LIN3S → **we / our**.
- Client → **you / your**.
- Never the third person ("the company will…") in client-facing copy.

### Emoji
- **Not used** in client-facing material (decks, proposals, web copy).
- Tolerated in internal Slack/notes only. The brand identity carries no emoji.

### Example copy (the vibe)

> **Where you are.**
> Your stack ships fast, but every team measures success differently. There's no single source of truth.
>
> **What's stopping you.**
> Three years of well-meant tooling decisions, now contradicting each other.
>
> **How we get you there.**
> A 6-week audit. One shared metric tree. Three initiatives, prioritised by the team that owns them.

Short sentences. Real numbers. No filler. No "journey".

---

## 4. Visual foundations

### Colors
- **Primary** — Black `#101010`, White `#FFFFFF`, Dark `#1A1A1A`. The system is fundamentally **black on white** or **white on black**.
- **Neutrals** — `#F0F0F0` (light card / divider backgrounds), `#999999` (captions, metadata).
- **Accent (RRSS / social only — NOT slides)** — Blue `#415986`, Red `#D65040`, Orange `#E29F37`. These animate the social grid; they never appear in client decks.
- **Data viz only** — `#6E120B`, `#B12B29`, `#102F47`, `#729AB9`. Reserved for charts. Don't repurpose for UI accents.
- **No gradients.** Anywhere. Backgrounds, buttons, text, charts — flat fills only.

### Typography
- **Inter** is the single typeface for body and UI. Light · Regular · Medium · SemiBold · Bold · Black are loaded. The 18pt optical size is used for body; 24pt/28pt cuts may be substituted at large display sizes.
- **Besley** is the editorial accent. Reserved for covers and one-off display moments (large italic phrases on a black slide). Never use Besley for body or UI.
- **Left-aligned everywhere.** No centred body copy. Centred type is reserved for the slide-counter overlay and similar UI chrome.
- Tracking: tight (-0.02 to -0.04em) on display sizes; wider (+0.08em) on uppercase eyebrows.

### Imagery & backgrounds
- **No gradients.** No decorative bars.
- Backgrounds are either **solid black** (`#101010`), **solid white**, or **`#F0F0F0`** for soft cards. Three options, total.
- Photography (when used) tends **cool, neutral, grainless** — humans at work, real office light, no filter overlays. Black-and-white treatments are acceptable; warm-tinted "lifestyle" stock is not.
- **No repeating patterns or textures.** No hand-drawn illustration.
- **No protection gradients** behind text over images — use full-bleed dark photography only when the image already has enough contrast, otherwise place text on a solid black panel adjacent to the image.

### Layout
- **16:9** is the canonical slide ratio. The slide-deck grid is 12 columns with a 96px outer gutter and 24px column gap on a 1920×1080 canvas.
- Web layout uses the same 12-col system on a 1440 design width with 24px gap.
- **Always left-aligned.** This is the single strongest layout rule.
- Wide use of negative space. Cards and content panels never feel cramped.
- No fixed/sticky decorative chrome. Headers may stick; nothing else.

### Borders, radii, shadows
- **Radii** default to **0**. Buttons, cards, inputs, slide content blocks — all square corners.
  - Exception: small chips/tags may use 2–4px. Avatars are circular. Never use pill-shaped buttons.
- **Borders** are 1px hairline (`#E5E5E5` on light, `#2A2A2A` on dark). Thicker borders (2px) are used only for active states.
- **Shadows** are used sparingly — the system prefers contrast and tight borders to elevation. Reserved for menus, toasts, modals.

### Motion
- **Fades and translates only.** No bounces, no spring overshoots, no rotation.
- Easing: `cubic-bezier(0.2, 0.6, 0.2, 1)` for the vast majority of UI.
- Durations: 120ms (hover), 220ms (panel open), 360ms (page transition).
- Hover transitions: opacity-down (to 0.6) on links and icons, or background swap on buttons. No scale, no shadow lift.
- Press: subtle background-darken on dark buttons, lighten on light. Never `transform: scale(0.95)` — feels app-y, not editorial.

### Transparency & blur
- **No backdrop blur.** Frosted glass is not a LIN3S motif.
- Use solid panels with hairline borders instead.
- The only acceptable transparency is rgba shadows.

### Cards
- White card on `#F0F0F0` background, OR `#F0F0F0` card on white background.
- 1px hairline border (light), or no border at all.
- 0px radius. 32px internal padding. No shadow by default.
- Headings inside cards use the same scale as the rest of the page.

### LIN3S logo / wordmark rules
- LIN3S is always rendered in full uppercase. The "3" is part of the wordmark — don't replace it with an E.
- Don't rotate, skew, italicize, gradient, outline, or animate-distort the wordmark.
- Minimum size: 48px wide on screen, 12mm wide in print.
- Clearspace: the height of the "L" on all sides.

---

## 5. Iconography

LIN3S's brand brief does not bundle a proprietary icon set. The convention used in this design system:

- **Lucide icons** (`https://lucide.dev`) are the default icon system. Stroke-only, 1.5–2px stroke, square line-caps, geometric — a strong match for the LIN3S grid-and-line aesthetic.
- Loaded from CDN: `https://unpkg.com/lucide@latest`.
- Icon size scale: 16px (inline), 20px (UI default), 24px (nav), 32px (feature card), 48px+ (illustrative).
- Color: `currentColor` always — icons inherit text color. No multi-color icons.

⚠ **Substitution flag.** Lucide is a substitution. If LIN3S maintains its own icon library (typical for an agency at this scale), please send it and we'll swap.

### Other "icon-like" assets

- **The three lines mark** (`assets/three-lines.svg`) is a brand-native graphic device — three horizontal lines, evoking the "LIN3S" name. Use it as a section-opener glyph, a list separator, or a watermark on cover slides. Always rendered in `currentColor`.
- **No emoji.** Anywhere.
- **No unicode glyphs as icons.** The single exception is the bullet `•` in lists.
- **No PNG icons.** SVG only.

---

## 6. Font substitutions (flags)

| Asset | Status | What we did | Action needed |
|---|---|---|---|
| Inter (all weights) | ✅ supplied | Bold/Light/Regular/SemiBold from `uploads/`. Medium/Black/Italic pulled from the mounted `Inter/` folder. | None. |
| **Besley** | ⚠ substituted | Loaded from Google Fonts `https://fonts.googleapis.com/css2?family=Besley`. | If you have a licensed local TTF/WOFF2, drop it into `fonts/` and we'll update `@font-face`. |
| Icon system | ⚠ substituted | Lucide CDN. | Share the LIN3S icon set if one exists. |

---

## 7. Index of files

```
/
├── README.md                  ← you are here
├── SKILL.md                   ← agent-skill manifest for downstream use
├── colors_and_type.css        ← canonical CSS tokens — import on every artifact
├── fonts/                     ← Inter TTFs, full weight ladder
│   ├── Inter_18pt-Light.ttf
│   ├── Inter_18pt-Regular.ttf
│   ├── Inter_18pt-Italic.ttf
│   ├── Inter_18pt-Medium.ttf
│   ├── Inter_18pt-SemiBold.ttf
│   ├── Inter_18pt-Bold.ttf
│   └── Inter_18pt-Black.ttf
├── assets/                    ← logos, marks, brand graphics
│   ├── lin3s-wordmark.svg     ← primary wordmark, monochrome (currentColor)
│   ├── lin3s-mark.svg         ← square mark — the "3" on black
│   └── three-lines.svg        ← brand glyph
├── preview/                   ← Design System tab cards (small specimens)
├── slides/                    ← sample 16:9 slides per the slide-type taxonomy
│   ├── index.html             ← runnable deck of all sample slides
│   ├── CoverSlide.jsx
│   ├── SectionDividerSlide.jsx
│   ├── IndexSlide.jsx
│   ├── ContentSlide.jsx
│   ├── ThreeColumnSlide.jsx
│   ├── BigStatementSlide.jsx
│   ├── DataSlide.jsx
│   └── ClosingSlide.jsx
└── ui_kits/
    └── lin3s-web/             ← the lin3s.com marketing site UI kit
        ├── README.md
        ├── index.html
        └── *.jsx              ← Header, Hero, ServiceCard, CaseStudyRow, Footer, ...
```

---

## 8. Caveats & open questions

1. **No codebase or Figma was attached.** All UI-kit components are interpretations of the brand brief. If a real `lin3s.com` codebase exists, attach it and we'll align to it pixel-for-pixel.
2. **Besley font file** is needed; currently loaded from Google Fonts.
3. **Icon set** is substituted to Lucide; please confirm or supply.
4. **No real photography** is bundled — the visual rules describe what's acceptable; please drop a few hero photos into `assets/` if you have them.
5. **Spanish localisation** isn't covered here. The ORIA acronym is Spanish; copy patterns and slide samples are written in English. Confirm primary language and we'll mirror.
