# LIN3S — Design System

> **Humans leading data.**
> The brand & design system for **LIN3S**, the digital company that *unlocks your potential — powered by data, led by humans.* A confident **monochrome editorial** system: near-black ink on white paper, a high-contrast Besley + Inter type pairing, and colour held strictly in reserve.

This project is a living, machine-readable design system. `styles.css` is the single entry point consumers link; everything else is organised by concern. The Design System tab renders every specimen and component card.

---

## 1. Context

**LIN3S** is a data-driven digital consultancy. It works *with* clients (not just *for* them), sharing knowledge so they grasp the potential of their business, then designing and deploying high-impact digital strategy through talent **hubs** specialised by sector (Retail, B2B, Sports — plus Leisure, Leads, Ideas, Lab, IA).

The visual system translates that positioning into a rigorous, analytical interface that still feels warm and human — "a well-set report, not a saturated SaaS landing page." Colour is an *event*, not a default: three quarantined colour roles (Primary / Communication / Graphics) never bleed into each other.

### Sources

This system was derived from the official LIN3S brand & design repository. Explore it for deeper context — `DESIGN.md` (canonical visual spec), `BRAND.md` (strategy, voice, messaging), `tokens.css/json`, `styleguide.html`, and the official `-move Branding` logo vectors:

- **GitHub:** `inakigorostiza/lin3s-design-system` — https://github.com/inakigorostiza/lin3s-design-system
  - `lin3s/DESIGN.md` · `lin3s/BRAND.md` · `lin3s/tokens.css` · `lin3s/styleguide.html` · `lin3s/logo_lin3s/`
  - `design-rag/` — a 74-system reference corpus used to derive the system's structure.

Brand created by **-move Branding** (November 2025). Logo assets © 2025 -move Branding.

> **Fonts:** Besley + Inter are both free on **Google Fonts** and load via `@import` in `tokens/fonts.css` — no local font files or substitutes are needed. If you ever need offline substitutes: Besley → Roslindale / Zilla Slab; Inter → Geist / system UI sans (re-check display tracking after any swap).

---

## 2. Content fundamentals

**Voice:** clear, close and accessible — transparent, **no jargon ("sin palabros")**, honest. Deeply data-driven, but *profoundly human*. LIN3S speaks in **results, not promises**.

- **Person & tone.** Speak *with* the reader: "we work for you, but above all, *with* you." Warm and direct — there are people behind every metric. First-person plural ("we"), second-person address ("you / your business").
- **Casing.** Sentence case for headlines and body. UPPERCASE only for the small eyebrow/taxonomy labels (Inter, +1.2px tracking). The claim "Humans leading data." is set in Besley, sentence case.
- **Quantify everything.** Pair a claim with a measurable outcome: *"what isn't measured doesn't exist; what doesn't create impact doesn't interest us."* Lead with the benefit, then prove it with data — never bury the result.
- **No emoji.** The brand is editorial and analytical; emoji are off-brand. No exclamatory hype, no vague superlatives without evidence, no buzzwords/acronyms ("palabros").
- **The two-message rule:**
  - To **inform / sell / describe** → *"Digital experts unlocking your potential. Powered by data. Driven by humans."* (the value proposition).
  - To **inspire / move / differentiate / reinforce culture** → *"HUMANS LEADING DATA"* (the claim).

**The four personality values** (every line should sound like at least one):
*Digital Mentors* ("sharing is vibing") · *Authentic People* ("profoundly humans") · *Outcome Deliverers* ("your business has been just boosted") · *Challenge Seekers* ("at the forefront of what if…").

**Examples (on-brand):** "Conversion, rebuilt around the shopper." · "82% of clients renew within the first year." · "Data means nothing until it is interpreted, understood and applied with purpose."

---

## 3. Visual foundations

A monochrome editorial system. The chrome — navigation, body, primary CTA, footer — is **black-and-white only**; colour appears solely on temporary campaign surfaces and in data viz.

- **Colour.** `--lin3s-primary` near-black `#050A0E` (not pure `#000`, so large ink fields read paper-printed) on `--lin3s-canvas` white. Surfaces step through `canvas → canvas-muted #F3F3F1 → surface-soft #ECEBE7`; text steps `ink → ink-soft #3D3D3D → ink-muted #717171`. **Three quarantined roles:** Primary (corporate/permanent), **Communication** `--comm-*` (campaign-only colour-blocks), **Graphics** `--chart-*` (charts only). Mixing roles is the fastest way to break the brand.
- **Type.** **Besley** slab-serif for every display moment; **Inter** for everything functional. The split *is* the voice — a sans headline reads off-brand, a serif button reads decorative. Negative tracking scales with display size (−2.4px on the 120px claim → 0 on body). Hierarchy comes from **size + face, not colour** — ink stays near-black; emphasis is bigger Besley before any colour change.
- **Alignment.** Left or centre, **never right** (a hard brand rule).
- **Layout.** 8px base grid; explicit *reticula* (baseline + 12-col grid) every element snaps to. Max content ~1240px centred; 96px (`section`) between major bands. Hero often 6/6 (headline left, image/stat right); card grids 3→2→1-up.
- **Backgrounds.** Mostly white paper and muted-grey bands — **no gradients** (forbidden on brand elements), no repeating textures in UI. Imagery follows four themes (Business & Sectors / Analogic / Offices & Team / Nature), skews **analogue, grainy, textured, human**, often black-and-white. The signature **"pixel / data-disintegration"** treatment dissolves a subject into pixels on pure white.
- **Elevation — shadow-light.** Depth comes from *surface contrast* (white vs muted-grey vs inverse-black) and colour-block panels, **not** drop shadows. Level 0 flat (color-blocks, hero, footer) · Level 1 hairline `#E2E1DD` (inputs, cards, dividers) · Level 2 muted-fill surface (cards, media) · Level 3 soft shadow + 60% black scrim (modals only).
- **Corners.** `xs 2px` (isotype squares, chart cells) · `sm 6px` (chips) · `md 10px` (inputs) · `lg 16px` (cards, colour-blocks, media) · `xl 24px` (hero panels) · `pill 999px` (**all buttons**).
- **Cards.** Muted-paper fill (`canvas-muted`), 16px corners, 32px padding, **no shadow, no coloured left border.** Media cards use `surface-soft`, padding 0, image clipped to the frame.
- **Buttons.** Always pills. Solid black `primary` is the page's main action; white `secondary` has a 1px ink border; `outline` is a lighter hairline tertiary; `onInverse` is the white pill for dark/colour-block grounds.
- **Motion & states.** The brand guide does not specify motion — keep it restrained: short ease (~160–200ms) on colour/border, no bounces, no infinite loops. **Hover/press are communicated in ink, not colour:** primary darkens to `ink-soft` on press; inputs thicken the border to ink on focus (no colour shift). Honour `prefers-reduced-motion`.
- **Transparency & blur.** Used sparingly — only the modal scrim (black ~60%). No frosted-glass chrome.
- **Signatures.** ▮ ▮ ▮ isotype marker · oversized Besley **stat numbers** · **dotted trend lines** · bubble/fan charts from `--chart-*` · the pixel/data-disintegration image.

---

## 4. Iconography

LIN3S has **no proprietary icon font** in the brand guide. Its only true brand glyph is the **▮ ▮ ▮ isotype** (three hard squares) — used as a marker, corner tick, or list bullet via the `IsotypeMarker` component or `assets/isotype.svg`.

- **No emoji**, ever — off-brand for an editorial/analytical system.
- **No decorative unicode** as iconography. The chevron in `Select` and arrows in `IconButton` are simple CSS/glyph shapes kept monochrome (ink).
- **Recommended icon set (substitution — FLAGGED):** the brand guide ships no UI icon library, so for product UI use a **thin, monochrome line set** that matches the editorial restraint — **[Lucide](https://lucide.dev)** (1.5–2px stroke, square caps) rendered in `--lin3s-ink`, loaded from CDN. This is a *recommended substitute*, not an official LIN3S asset — confirm with the brand team before shipping production UI, and never recolour icons into Communication/Graphics palettes.
- Icons are always **single-colour ink** (or inverse-white on dark). No duotone, no fills, no gradients.

---

## 5. Index / manifest

**Root**
- `styles.css` — global entry point (consumers link this). `@import`s the four token files.
- `tokens/` — `fonts.css` (Google Fonts) · `colors.css` · `typography.css` · `spacing.css` (spacing + radius + layout).
- `assets/` — official `-move Branding` logo vectors: `wordmark.svg/.png` (+ `-inverse`), `isotype.svg/.png` (+ `-inverse`), `hub-lockup.svg`.
- `SKILL.md` — Agent-Skills-compatible entry for using this system in Claude Code.

**Components** (`components/<group>/` — React primitives, namespace `window.LIN3SDesignSystem_a9fd2b`)
- `buttons/` — **Button**, **IconButton**
- `forms/` — **Input**, **Select**, **Checkbox**, **Switch**
- `content/` — **Card**, **Badge**, **Eyebrow**, **StatNumber**
- `brand/` — **Wordmark**, **IsotypeMarker**, **ColorBlock**

**Foundations** (`guidelines/*.card.html`) — Colors (6), Type (4), Spacing (2), Brand (4), Imagery (2 — four themes + the pixel/data-disintegration signature) specimen cards.

**UI kits** (`ui_kits/<product>/`)
- `website/` — the LIN3S marketing site: home hero, services, case study, contact.

**Templates** (`templates/<slug>/`) — copy-and-fill starting points for consuming projects.
- `landing-page/` — a LIN3S marketing-page scaffold.

**Slides** (`slides/`) — the LIN3S deck pattern (red cover → index → team → data → close).

---

*Derived items (not in the brand guide): full spacing/radius scales, all type pixel metrics, the muted/soft companion tones, semantic mapping, and all web-component specs — best-practice inferences matched to the guide's aesthetic. LIN3S Black is rendered `#050A0E`. Motion is out of scope in the guide. See `DESIGN.md` "Known Gaps" in the source repo.*
