/* @ds-bundle: {"format":3,"namespace":"LIN3SDesignSystem_a9fd2b","components":[{"name":"ColorBlock","sourcePath":"components/brand/ColorBlock.jsx"},{"name":"IsotypeMarker","sourcePath":"components/brand/IsotypeMarker.jsx"},{"name":"Wordmark","sourcePath":"components/brand/Wordmark.jsx"},{"name":"Button","sourcePath":"components/buttons/Button.jsx"},{"name":"IconButton","sourcePath":"components/buttons/IconButton.jsx"},{"name":"Badge","sourcePath":"components/content/Badge.jsx"},{"name":"Card","sourcePath":"components/content/Card.jsx"},{"name":"Eyebrow","sourcePath":"components/content/Eyebrow.jsx"},{"name":"StatNumber","sourcePath":"components/content/StatNumber.jsx"},{"name":"Checkbox","sourcePath":"components/forms/Checkbox.jsx"},{"name":"Input","sourcePath":"components/forms/Input.jsx"},{"name":"Select","sourcePath":"components/forms/Select.jsx"},{"name":"Switch","sourcePath":"components/forms/Switch.jsx"}],"sourceHashes":{"components/brand/ColorBlock.jsx":"ef9dddc7a782","components/brand/IsotypeMarker.jsx":"dbbc002367fc","components/brand/Wordmark.jsx":"84bbdec88d6b","components/buttons/Button.jsx":"03475882d35b","components/buttons/IconButton.jsx":"7018dddbc90e","components/content/Badge.jsx":"95a7aba37957","components/content/Card.jsx":"5bb47b24e08c","components/content/Eyebrow.jsx":"a4b4cb1eb88d","components/content/StatNumber.jsx":"fb12a18b84da","components/forms/Checkbox.jsx":"643bdeaf61f3","components/forms/Input.jsx":"fa60fd385f5f","components/forms/Select.jsx":"a391c40bbf0e","components/forms/Switch.jsx":"073b4684cc15","ui_kits/website/site-chrome.jsx":"9960df3e8024","ui_kits/website/site-contact.jsx":"b7db183e5e99","ui_kits/website/site-home.jsx":"887af7d55e2b","ui_kits/website/site-work.jsx":"243f4f746acb"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.LIN3SDesignSystem_a9fd2b = window.LIN3SDesignSystem_a9fd2b || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// components/brand/ColorBlock.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * LIN3S colour-block section — the signature campaign storytelling panel. A full content-width
 * block in a Communication colour (red / blue / brown), 16px corners, 48px padding, carrying an
 * eyebrow + Besley headline + body + optional action. CAMPAIGN/TEMPORARY surfaces only —
 * never a permanent corporate accent, and never let two colour-blocks touch (return to white).
 */
function ColorBlock({
  children,
  color = "red",
  eyebrow,
  headline,
  body,
  action,
  style,
  ...rest
}) {
  const grounds = {
    red: {
      background: "var(--lin3s-comm-red)",
      color: "var(--lin3s-on-primary)"
    },
    blue: {
      background: "var(--lin3s-comm-blue)",
      color: "var(--lin3s-on-primary)"
    },
    brown: {
      background: "var(--lin3s-comm-brown)",
      color: "var(--lin3s-ink)"
    },
    orange: {
      background: "var(--lin3s-comm-orange)",
      color: "var(--lin3s-ink)"
    }
  };
  const g = grounds[color] || grounds.red;
  const muted = g.color === "var(--lin3s-ink)" ? "var(--lin3s-ink-soft)" : "rgba(255,255,255,.82)";
  return /*#__PURE__*/React.createElement("section", _extends({
    style: {
      background: g.background,
      color: g.color,
      borderRadius: "var(--lin3s-rounded-lg)",
      padding: "var(--lin3s-space-xxl)",
      fontFamily: "var(--lin3s-font-body)",
      ...style
    }
  }, rest), children || /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: 640,
      display: "flex",
      flexDirection: "column",
      gap: 16
    }
  }, eyebrow && /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      fontWeight: 500,
      letterSpacing: 1.2,
      textTransform: "uppercase",
      color: muted
    }
  }, eyebrow), headline && /*#__PURE__*/React.createElement("h2", {
    style: {
      margin: 0,
      fontFamily: "var(--lin3s-font-display)",
      fontSize: "var(--lin3s-headline-size)",
      fontWeight: 500,
      lineHeight: 1.18,
      letterSpacing: "var(--lin3s-headline-ls)"
    }
  }, headline), body && /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      fontSize: "var(--lin3s-body-lg-size)",
      lineHeight: 1.5,
      color: muted
    }
  }, body), action && /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 8
    }
  }, action)));
}
Object.assign(__ds_scope, { ColorBlock });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/brand/ColorBlock.jsx", error: String((e && e.message) || e) }); }

// components/brand/IsotypeMarker.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * LIN3S isotype marker — the three-square glyph (▮ ▮ ▮), each block slightly taller than
 * wide (official 176.19 × 204.49). A first-class brand tick: section marker, corner tick,
 * list bullet. Never placed directly beside the wordmark. `tone="inverse"` for dark grounds.
 */
function IsotypeMarker({
  size = 16,
  tone = "ink",
  title = "LIN3S",
  style,
  ...rest
}) {
  const color = tone === "inverse" ? "var(--lin3s-on-primary)" : "var(--lin3s-ink)";
  return /*#__PURE__*/React.createElement("svg", _extends({
    viewBox: "0 0 771.84 204.49",
    role: "img",
    "aria-label": title,
    style: {
      height: size,
      width: "auto",
      display: "block",
      color,
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("rect", {
    width: "176.19",
    height: "204.49",
    rx: "0.14",
    ry: "0.14",
    fill: "currentColor"
  }), /*#__PURE__*/React.createElement("rect", {
    x: "297.82",
    width: "176.19",
    height: "204.49",
    rx: "0.14",
    ry: "0.14",
    fill: "currentColor"
  }), /*#__PURE__*/React.createElement("rect", {
    x: "595.65",
    width: "176.19",
    height: "204.49",
    rx: "0.14",
    ry: "0.14",
    fill: "currentColor"
  }));
}
Object.assign(__ds_scope, { IsotypeMarker });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/brand/IsotypeMarker.jsx", error: String((e && e.message) || e) }); }

// components/brand/Wordmark.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const WORDMARK_PATHS = ["M51.48,7.85v155.89c0,1.81,1.47,3.28,3.28,3.28h84.38c1.81,0,3.28,1.47,3.28,3.28v35.47c0,1.81-1.47,3.28-3.28,3.28H3.28c-1.81,0-3.28-1.47-3.28-3.28V7.85c0-1.81,1.47-3.28,3.28-3.28h44.91c1.81,0,3.28,1.47,3.28,3.28Z", "M213.43,7.85v197.92c0,1.81-1.47,3.28-3.28,3.28h-44.91c-1.81,0-3.28-1.47-3.28-3.28V7.85c0-1.81,1.47-3.28,3.28-3.28h44.91c1.81,0,3.28,1.47,3.28,3.28Z", "M244.8,4.56h46.32c1.16,0,2.24.62,2.83,1.62l70.54,119.94h.58V7.85c0-1.81,1.47-3.28,3.28-3.28h44.91c1.81,0,3.28,1.47,3.28,3.28v197.92c0,1.81-1.47,3.28-3.28,3.28h-46.32c-1.16,0-2.24-.62-2.83-1.62l-70.55-119.92h-.57v118.25c0,1.81-1.47,3.28-3.28,3.28h-44.91c-1.81,0-3.28-1.47-3.28-3.28V7.85c0-1.81,1.47-3.28,3.28-3.28Z", "M441.37,67.39c-1.89,0-3.47-1.6-3.39-3.49,1.68-37.21,30.45-63.9,81.18-63.9,38.11,0,72.98,20.11,72.98,56.15,0,22.16-13,36.04-29.56,44.6v.6c25.12,7.96,35.46,25.12,35.46,50.23,0,40.18-32.79,62.05-78,62.05-56.21,0-86.55-26.45-88-73.26-.06-1.92,1.54-3.56,3.46-3.56h40.65c1.73,0,3.1,1.41,3.17,3.14.95,21.61,12.06,33.18,37.16,33.18,21.27,0,31.31-9.17,31.31-24.52,0-19.8-13.88-27.19-37.52-27.19h-10.35v-34.57h9.17c21.86,0,32.79-7.1,32.79-22.45,0-17.44-10.04-23.94-26.6-23.94-14.78,0-28.53,6.59-30,23.77-.15,1.73-1.49,3.13-3.23,3.13h-40.71Z", "M780.24,148.99c0,39.19-29.74,64.65-80.94,64.65s-91.15-24.96-93.16-68.28c-.09-1.89,1.5-3.51,3.4-3.51h44.92c1.73,0,3.07,1.39,3.23,3.11,1.73,19.4,19.87,26.62,41.62,26.62,19.15,0,29.46-7.43,29.46-17.72,0-16.3-22.03-20.03-50.91-28.6-35.19-10.29-66.07-25.45-66.07-63.2,0-44.62,34.88-62.06,78.93-62.06,46.35,0,83.01,23.69,86.43,63,.17,1.94-1.44,3.63-3.39,3.63h-45.29c-1.5,0-2.72-1.09-3.07-2.55-3.3-13.89-16.06-22.04-34.69-22.04-15.44,0-27.46,4.87-27.46,16.3,0,12.58,12.87,16.58,38.61,23.73,38.61,10.6,78.36,23.17,78.36,66.93Z"];

/**
 * LIN3S wordmark — the official `LIN3S` logotype (the `3` substitutes the `E`), rendered as
 * an inline vector that inherits `color`. It is a FIXED logo asset: never re-typeset, deformed,
 * recoloured to a non-permitted colour, or locked up beside the isotype.
 * Use `tone="inverse"` (white) over dark grounds.
 */
function Wordmark({
  height = 28,
  tone = "ink",
  title = "LIN3S",
  style,
  ...rest
}) {
  const color = tone === "inverse" ? "var(--lin3s-on-primary)" : "var(--lin3s-ink)";
  return /*#__PURE__*/React.createElement("svg", _extends({
    viewBox: "0 0 780.24 213.64",
    role: "img",
    "aria-label": title,
    style: {
      height,
      width: "auto",
      display: "block",
      color,
      ...style
    }
  }, rest), WORDMARK_PATHS.map((d, i) => /*#__PURE__*/React.createElement("path", {
    key: i,
    d: d,
    fill: "currentColor"
  })));
}
Object.assign(__ds_scope, { Wordmark });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/brand/Wordmark.jsx", error: String((e && e.message) || e) }); }

// components/buttons/Button.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * LIN3S Button — the brand's pill action. Solid black `primary` is the single most
 * important action on any surface; `secondary` is the ink-outlined white counterpart;
 * `outline` is the lighter hairline tertiary; `onInverse` is the white pill for dark grounds.
 */
function Button({
  children,
  variant = "primary",
  size = "md",
  disabled = false,
  type = "button",
  onClick,
  style,
  ...rest
}) {
  const pad = size === "sm" ? "10px 20px" : size === "lg" ? "16px 34px" : "14px 28px";
  const fontSize = size === "sm" ? 13 : size === "lg" ? 16 : "var(--lin3s-button-size)";
  const base = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    fontFamily: "var(--lin3s-font-body)",
    fontSize,
    fontWeight: 500,
    lineHeight: 1,
    letterSpacing: 0,
    borderRadius: "var(--lin3s-rounded-pill)",
    padding: pad,
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.4 : 1,
    transition: "background-color .18s ease, color .18s ease, border-color .18s ease",
    whiteSpace: "nowrap",
    border: "1px solid transparent",
    textDecoration: "none"
  };
  const variants = {
    primary: {
      background: "var(--lin3s-primary)",
      color: "var(--lin3s-on-primary)",
      borderColor: "var(--lin3s-primary)"
    },
    secondary: {
      background: "var(--lin3s-canvas)",
      color: "var(--lin3s-ink)",
      borderColor: "var(--lin3s-ink)"
    },
    outline: {
      background: "var(--lin3s-canvas)",
      color: "var(--lin3s-ink)",
      borderColor: "var(--lin3s-hairline)"
    },
    onInverse: {
      background: "var(--lin3s-on-primary)",
      color: "var(--lin3s-ink)",
      borderColor: "var(--lin3s-on-primary)"
    }
  };
  return /*#__PURE__*/React.createElement("button", _extends({
    type: type,
    disabled: disabled,
    onClick: onClick,
    style: {
      ...base,
      ...variants[variant],
      ...style
    }
  }, rest), children);
}
Object.assign(__ds_scope, { Button });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/buttons/Button.jsx", error: String((e && e.message) || e) }); }

// components/buttons/IconButton.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * LIN3S IconButton — a circular icon-only action. `solid` is the black-fill pill;
 * `ghost` is borderless; `outline` carries a hairline. Pass an SVG/glyph as children.
 */
function IconButton({
  children,
  variant = "outline",
  size = 44,
  disabled = false,
  ariaLabel,
  onClick,
  style,
  ...rest
}) {
  const base = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: size,
    height: size,
    borderRadius: "var(--lin3s-rounded-full)",
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.4 : 1,
    border: "1px solid transparent",
    color: "var(--lin3s-ink)",
    background: "transparent",
    transition: "background-color .18s ease, border-color .18s ease, color .18s ease",
    padding: 0
  };
  const variants = {
    solid: {
      background: "var(--lin3s-primary)",
      color: "var(--lin3s-on-primary)",
      borderColor: "var(--lin3s-primary)"
    },
    ghost: {
      background: "transparent",
      borderColor: "transparent"
    },
    outline: {
      background: "var(--lin3s-canvas)",
      borderColor: "var(--lin3s-hairline)"
    }
  };
  return /*#__PURE__*/React.createElement("button", _extends({
    type: "button",
    "aria-label": ariaLabel,
    disabled: disabled,
    onClick: onClick,
    style: {
      ...base,
      ...variants[variant],
      ...style
    }
  }, rest), children);
}
Object.assign(__ds_scope, { IconButton });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/buttons/IconButton.jsx", error: String((e && e.message) || e) }); }

// components/content/Badge.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * LIN3S badge — a small status/label pill. `solid` ink fill, `outline` hairline, or a
 * monochrome `subtle` grey. Semantic tones (success/warning/error) map onto the palette
 * and should stay rare — colour is an event.
 */
function Badge({
  children,
  variant = "subtle",
  tone = "neutral",
  style,
  ...rest
}) {
  const tones = {
    neutral: "var(--lin3s-ink)",
    success: "var(--lin3s-success)",
    warning: "var(--lin3s-warning)",
    error: "var(--lin3s-error)"
  };
  const c = tones[tone];
  const variants = {
    solid: {
      background: c,
      color: tone === "warning" ? "var(--lin3s-ink)" : "var(--lin3s-on-primary)",
      border: "1px solid transparent"
    },
    outline: {
      background: "transparent",
      color: c,
      border: `1px solid ${c}`
    },
    subtle: {
      background: "var(--lin3s-canvas-muted)",
      color: "var(--lin3s-ink)",
      border: "1px solid var(--lin3s-hairline)"
    }
  };
  return /*#__PURE__*/React.createElement("span", _extends({
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: 6,
      fontFamily: "var(--lin3s-font-body)",
      fontSize: 12,
      fontWeight: 500,
      lineHeight: 1,
      letterSpacing: 0.2,
      padding: "5px 10px",
      borderRadius: "var(--lin3s-rounded-pill)",
      ...variants[variant],
      ...style
    }
  }, rest), children);
}
Object.assign(__ds_scope, { Badge });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/content/Badge.jsx", error: String((e && e.message) || e) }); }

// components/content/Card.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * LIN3S card — muted-paper content container. `default` is the canvas-muted card with
 * 32px padding; `media` is the surface-soft image frame (padding 0); `inverse` is the
 * near-black feature card. Shadow-light: depth from surface contrast, never drop shadow.
 */
function Card({
  children,
  variant = "default",
  style,
  ...rest
}) {
  const variants = {
    default: {
      background: "var(--lin3s-canvas-muted)",
      color: "var(--lin3s-ink)",
      padding: "var(--lin3s-space-xl)"
    },
    media: {
      background: "var(--lin3s-surface-soft)",
      color: "var(--lin3s-ink)",
      padding: 0,
      overflow: "hidden"
    },
    inverse: {
      background: "var(--lin3s-inverse-canvas)",
      color: "var(--lin3s-inverse-ink)",
      padding: "var(--lin3s-space-xl)"
    }
  };
  return /*#__PURE__*/React.createElement("div", _extends({
    style: {
      borderRadius: "var(--lin3s-rounded-lg)",
      fontFamily: "var(--lin3s-font-body)",
      ...variants[variant],
      ...style
    }
  }, rest), children);
}
Object.assign(__ds_scope, { Card });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/content/Card.jsx", error: String((e && e.message) || e) }); }

// components/content/Eyebrow.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * LIN3S eyebrow — the uppercase taxonomy label that sits above headlines (Inter, 12px,
 * +1.2px tracking, ink-muted). Optionally prefixed with the ■ ■ ■ isotype marker.
 */
function Eyebrow({
  children,
  marker = false,
  style,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("span", _extends({
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: 10,
      fontFamily: "var(--lin3s-font-body)",
      fontSize: "var(--lin3s-eyebrow-size)",
      fontWeight: 500,
      lineHeight: 1.3,
      letterSpacing: "var(--lin3s-eyebrow-ls)",
      textTransform: "uppercase",
      color: "var(--lin3s-ink-muted)",
      ...style
    }
  }, rest), marker && /*#__PURE__*/React.createElement("span", {
    "aria-hidden": "true",
    style: {
      display: "inline-flex",
      gap: 2.5
    }
  }, /*#__PURE__*/React.createElement("i", {
    style: {
      width: 5,
      height: 6,
      background: "var(--lin3s-ink)",
      borderRadius: 1,
      display: "block"
    }
  }), /*#__PURE__*/React.createElement("i", {
    style: {
      width: 5,
      height: 6,
      background: "var(--lin3s-ink)",
      borderRadius: 1,
      display: "block"
    }
  }), /*#__PURE__*/React.createElement("i", {
    style: {
      width: 5,
      height: 6,
      background: "var(--lin3s-ink)",
      borderRadius: 1,
      display: "block"
    }
  })), children);
}
Object.assign(__ds_scope, { Eyebrow });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/content/Eyebrow.jsx", error: String((e && e.message) || e) }); }

// components/content/StatNumber.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * LIN3S stat number — an oversized Besley figure used as a hero data element (e.g. 82%).
 * Default ink; `accent` uses comm-red sparingly. Set `tone="inverse"` on dark grounds.
 * Pair with a caption beneath.
 */
function StatNumber({
  value,
  caption,
  accent = false,
  tone = "ink",
  size = "var(--lin3s-stat-size)",
  align = "left",
  style,
  ...rest
}) {
  const valueColor = accent ? "var(--lin3s-comm-red)" : tone === "inverse" ? "var(--lin3s-inverse-ink)" : "var(--lin3s-ink)";
  const captionColor = tone === "inverse" ? "var(--lin3s-inverse-ink-soft)" : "var(--lin3s-ink-muted)";
  return /*#__PURE__*/React.createElement("div", _extends({
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 8,
      textAlign: align,
      alignItems: align === "center" ? "center" : "flex-start",
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--lin3s-font-display)",
      fontSize: size,
      fontWeight: 500,
      lineHeight: 1,
      letterSpacing: "var(--lin3s-stat-ls)",
      color: valueColor
    }
  }, value), caption && /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--lin3s-font-body)",
      fontSize: "var(--lin3s-body-sm-size)",
      lineHeight: 1.45,
      color: captionColor,
      maxWidth: 260
    }
  }, caption));
}
Object.assign(__ds_scope, { StatNumber });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/content/StatNumber.jsx", error: String((e && e.message) || e) }); }

// components/forms/Checkbox.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * LIN3S checkbox — square (rounded-xs), hairline border, fills ink when checked with a
 * white tick. Monochrome only; no colour states.
 */
function Checkbox({
  label,
  checked,
  defaultChecked,
  disabled = false,
  onChange,
  id,
  style,
  ...rest
}) {
  const inputId = id || React.useId();
  return /*#__PURE__*/React.createElement("label", {
    htmlFor: inputId,
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: 10,
      fontFamily: "var(--lin3s-font-body)",
      fontSize: "var(--lin3s-body-size)",
      color: "var(--lin3s-ink)",
      cursor: disabled ? "not-allowed" : "pointer",
      opacity: disabled ? 0.5 : 1,
      ...style
    }
  }, /*#__PURE__*/React.createElement("input", _extends({
    id: inputId,
    type: "checkbox",
    checked: checked,
    defaultChecked: defaultChecked,
    disabled: disabled,
    onChange: onChange,
    style: {
      appearance: "none",
      WebkitAppearance: "none",
      width: 20,
      height: 20,
      margin: 0,
      borderRadius: "var(--lin3s-rounded-xs)",
      border: "1px solid var(--lin3s-ink)",
      background: "var(--lin3s-canvas)",
      display: "grid",
      placeItems: "center",
      cursor: "inherit"
    }
  }, rest)), label && /*#__PURE__*/React.createElement("span", null, label), /*#__PURE__*/React.createElement("style", null, `
        #${CSS.escape(inputId)}:checked { background: var(--lin3s-primary); border-color: var(--lin3s-primary); }
        #${CSS.escape(inputId)}:checked::after {
          content: ""; width: 10px; height: 6px; margin-top: -2px;
          border-left: 2px solid var(--lin3s-on-primary); border-bottom: 2px solid var(--lin3s-on-primary);
          transform: rotate(-45deg);
        }
      `));
}
Object.assign(__ds_scope, { Checkbox });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Checkbox.jsx", error: String((e && e.message) || e) }); }

// components/forms/Input.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * LIN3S text input — white field, hairline border, ink focus (border thickens to ink,
 * never a colour shift). Supports an optional label, helper text, and error state.
 */
function Input({
  label,
  helper,
  error = false,
  type = "text",
  value,
  defaultValue,
  placeholder,
  disabled = false,
  onChange,
  id,
  style,
  ...rest
}) {
  const [focused, setFocused] = React.useState(false);
  const inputId = id || React.useId();
  const borderColor = error ? "var(--lin3s-error)" : focused ? "var(--lin3s-ink)" : "var(--lin3s-hairline)";
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 6,
      fontFamily: "var(--lin3s-font-body)",
      ...style
    }
  }, label && /*#__PURE__*/React.createElement("label", {
    htmlFor: inputId,
    style: {
      fontSize: 13,
      fontWeight: 500,
      color: "var(--lin3s-ink)",
      lineHeight: 1.4
    }
  }, label), /*#__PURE__*/React.createElement("input", _extends({
    id: inputId,
    type: type,
    value: value,
    defaultValue: defaultValue,
    placeholder: placeholder,
    disabled: disabled,
    onChange: onChange,
    onFocus: () => setFocused(true),
    onBlur: () => setFocused(false),
    style: {
      fontFamily: "var(--lin3s-font-body)",
      fontSize: "var(--lin3s-body-size)",
      color: "var(--lin3s-ink)",
      background: "var(--lin3s-canvas)",
      border: `1px solid ${borderColor}`,
      borderRadius: "var(--lin3s-rounded-md)",
      padding: "12px 14px",
      minHeight: 44,
      boxSizing: "border-box",
      outline: "none",
      opacity: disabled ? 0.5 : 1,
      transition: "border-color .16s ease"
    }
  }, rest)), helper && /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: 12,
      lineHeight: 1.4,
      color: error ? "var(--lin3s-error)" : "var(--lin3s-ink-muted)"
    }
  }, helper));
}
Object.assign(__ds_scope, { Input });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Input.jsx", error: String((e && e.message) || e) }); }

// components/forms/Select.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * LIN3S select — native dropdown styled to match the text input (white field, hairline
 * border, ink focus). Pass `options` as an array of {value, label} or strings.
 */
function Select({
  label,
  options = [],
  value,
  defaultValue,
  disabled = false,
  onChange,
  id,
  style,
  ...rest
}) {
  const [focused, setFocused] = React.useState(false);
  const inputId = id || React.useId();
  const opts = options.map(o => typeof o === "string" ? {
    value: o,
    label: o
  } : o);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 6,
      fontFamily: "var(--lin3s-font-body)",
      ...style
    }
  }, label && /*#__PURE__*/React.createElement("label", {
    htmlFor: inputId,
    style: {
      fontSize: 13,
      fontWeight: 500,
      color: "var(--lin3s-ink)",
      lineHeight: 1.4
    }
  }, label), /*#__PURE__*/React.createElement("div", {
    style: {
      position: "relative"
    }
  }, /*#__PURE__*/React.createElement("select", _extends({
    id: inputId,
    value: value,
    defaultValue: defaultValue,
    disabled: disabled,
    onChange: onChange,
    onFocus: () => setFocused(true),
    onBlur: () => setFocused(false),
    style: {
      appearance: "none",
      WebkitAppearance: "none",
      width: "100%",
      fontFamily: "var(--lin3s-font-body)",
      fontSize: "var(--lin3s-body-size)",
      color: "var(--lin3s-ink)",
      background: "var(--lin3s-canvas)",
      border: `1px solid ${focused ? "var(--lin3s-ink)" : "var(--lin3s-hairline)"}`,
      borderRadius: "var(--lin3s-rounded-md)",
      padding: "12px 38px 12px 14px",
      minHeight: 44,
      boxSizing: "border-box",
      outline: "none",
      opacity: disabled ? 0.5 : 1,
      cursor: disabled ? "not-allowed" : "pointer",
      transition: "border-color .16s ease"
    }
  }, rest), opts.map(o => /*#__PURE__*/React.createElement("option", {
    key: o.value,
    value: o.value
  }, o.label))), /*#__PURE__*/React.createElement("span", {
    "aria-hidden": "true",
    style: {
      position: "absolute",
      right: 14,
      top: "50%",
      transform: "translateY(-50%)",
      pointerEvents: "none",
      width: 8,
      height: 8,
      borderRight: "1.5px solid var(--lin3s-ink-muted)",
      borderBottom: "1.5px solid var(--lin3s-ink-muted)",
      marginTop: -3,
      rotate: "45deg"
    }
  })));
}
Object.assign(__ds_scope, { Select });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Select.jsx", error: String((e && e.message) || e) }); }

// components/forms/Switch.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * LIN3S switch — pill track that fills ink when on. Monochrome; no colour states.
 */
function Switch({
  label,
  checked,
  defaultChecked,
  disabled = false,
  onChange,
  id,
  style,
  ...rest
}) {
  const isControlled = checked !== undefined;
  const [on, setOn] = React.useState(defaultChecked || false);
  const value = isControlled ? checked : on;
  const inputId = id || React.useId();
  const toggle = e => {
    if (disabled) return;
    if (!isControlled) setOn(e.target.checked);
    onChange && onChange(e);
  };
  return /*#__PURE__*/React.createElement("label", {
    htmlFor: inputId,
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: 10,
      fontFamily: "var(--lin3s-font-body)",
      fontSize: "var(--lin3s-body-size)",
      color: "var(--lin3s-ink)",
      cursor: disabled ? "not-allowed" : "pointer",
      opacity: disabled ? 0.5 : 1,
      ...style
    }
  }, /*#__PURE__*/React.createElement("input", _extends({
    id: inputId,
    type: "checkbox",
    role: "switch",
    checked: value,
    disabled: disabled,
    onChange: toggle,
    style: {
      position: "absolute",
      opacity: 0,
      width: 0,
      height: 0
    }
  }, rest)), /*#__PURE__*/React.createElement("span", {
    "aria-hidden": "true",
    style: {
      position: "relative",
      width: 44,
      height: 26,
      borderRadius: "var(--lin3s-rounded-pill)",
      background: value ? "var(--lin3s-primary)" : "var(--lin3s-hairline)",
      transition: "background-color .2s ease",
      flex: "none"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      position: "absolute",
      top: 3,
      left: value ? 21 : 3,
      width: 20,
      height: 20,
      borderRadius: "var(--lin3s-rounded-full)",
      background: "var(--lin3s-canvas)",
      transition: "left .2s ease"
    }
  })), label && /*#__PURE__*/React.createElement("span", null, label));
}
Object.assign(__ds_scope, { Switch });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/forms/Switch.jsx", error: String((e && e.message) || e) }); }

// ui_kits/website/site-chrome.jsx
try { (() => {
/* LIN3S website UI kit — shared chrome (Nav, Footer) + small layout helpers.
 * Composes the design-system primitives from window.LIN3SDesignSystem_a9fd2b.
 */
const DS = window.LIN3SDesignSystem_a9fd2b;
const {
  Button,
  IconButton,
  Wordmark,
  IsotypeMarker,
  Eyebrow
} = DS;
const NAV_LINKS = [{
  id: "home",
  label: "Home"
}, {
  id: "services",
  label: "Services"
}, {
  id: "work",
  label: "Work"
}, {
  id: "about",
  label: "About"
}];
function Nav({
  route,
  onNavigate
}) {
  const [open, setOpen] = React.useState(false);
  return /*#__PURE__*/React.createElement("header", {
    style: {
      position: "sticky",
      top: 0,
      zIndex: 20,
      height: "var(--lin3s-nav-height)",
      background: "var(--lin3s-canvas)",
      borderBottom: "1px solid var(--lin3s-hairline)",
      display: "flex",
      alignItems: "center"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: "100%",
      maxWidth: "var(--lin3s-container-max)",
      margin: "0 auto",
      padding: "0 32px",
      display: "flex",
      alignItems: "center",
      gap: 28
    }
  }, /*#__PURE__*/React.createElement("a", {
    href: "#",
    onClick: e => {
      e.preventDefault();
      onNavigate("home");
    },
    style: {
      display: "flex",
      alignItems: "center",
      textDecoration: "none"
    },
    "aria-label": "LIN3S home"
  }, /*#__PURE__*/React.createElement(Wordmark, {
    height: 22
  })), /*#__PURE__*/React.createElement("nav", {
    style: {
      display: "flex",
      gap: 26,
      marginLeft: 18
    },
    className: "lin3s-navlinks"
  }, NAV_LINKS.map(l => /*#__PURE__*/React.createElement("a", {
    key: l.id,
    href: "#",
    onClick: e => {
      e.preventDefault();
      onNavigate(l.id);
    },
    style: {
      fontFamily: "var(--lin3s-font-body)",
      fontSize: 13,
      fontWeight: 500,
      textDecoration: "none",
      color: route === l.id ? "var(--lin3s-ink)" : "var(--lin3s-ink-muted)",
      transition: "color .16s ease"
    }
  }, l.label))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginLeft: "auto",
      display: "flex",
      alignItems: "center",
      gap: 14
    }
  }, /*#__PURE__*/React.createElement(IsotypeMarker, {
    size: 14,
    style: {
      opacity: 0.85
    }
  }), /*#__PURE__*/React.createElement(Button, {
    size: "sm",
    onClick: () => onNavigate("contact")
  }, "Get in touch"))));
}
function MediaTile({
  label = "Image",
  ratio = "4 / 3",
  style
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      aspectRatio: ratio,
      background: "var(--lin3s-surface-soft)",
      borderRadius: "var(--lin3s-rounded-lg)",
      display: "flex",
      alignItems: "flex-end",
      padding: 16,
      boxSizing: "border-box",
      ...style
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--lin3s-font-body)",
      fontSize: 11,
      letterSpacing: 1.2,
      textTransform: "uppercase",
      color: "var(--lin3s-ink-muted)"
    }
  }, label));
}
function Section({
  children,
  bg = "canvas",
  style
}) {
  const grounds = {
    canvas: "var(--lin3s-canvas)",
    muted: "var(--lin3s-canvas-muted)",
    inverse: "var(--lin3s-inverse-canvas)"
  };
  return /*#__PURE__*/React.createElement("section", {
    style: {
      background: grounds[bg],
      padding: "var(--lin3s-space-section) 32px",
      ...style
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: "var(--lin3s-container-max)",
      margin: "0 auto"
    }
  }, children));
}
function Footer({
  onNavigate
}) {
  return /*#__PURE__*/React.createElement("footer", {
    style: {
      background: "var(--lin3s-inverse-canvas)",
      color: "var(--lin3s-inverse-ink)",
      padding: "64px 32px 40px"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: "var(--lin3s-container-max)",
      margin: "0 auto"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      justifyContent: "space-between",
      alignItems: "flex-start",
      gap: 40,
      flexWrap: "wrap"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      maxWidth: 360,
      display: "flex",
      flexDirection: "column",
      gap: 18
    }
  }, /*#__PURE__*/React.createElement(Wordmark, {
    height: 26,
    tone: "inverse"
  }), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      fontFamily: "var(--lin3s-font-body)",
      fontSize: 14,
      lineHeight: 1.5,
      color: "var(--lin3s-inverse-ink-soft)"
    }
  }, "Digital experts unlocking your potential. Powered by data. Driven by humans.")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 56,
      flexWrap: "wrap"
    }
  }, /*#__PURE__*/React.createElement(FooterCol, {
    title: "Company",
    links: ["About", "Hubs", "Careers", "Contact"],
    onNavigate: onNavigate
  }), /*#__PURE__*/React.createElement(FooterCol, {
    title: "Work",
    links: ["Retail", "B2B", "Sports", "All cases"],
    onNavigate: onNavigate
  }))), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 56,
      paddingTop: 22,
      borderTop: "1px solid rgba(255,255,255,.14)",
      display: "flex",
      justifyContent: "space-between",
      alignItems: "center",
      flexWrap: "wrap",
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--lin3s-font-body)",
      fontSize: 12,
      color: "var(--lin3s-inverse-ink-soft)"
    }
  }, "\xA9 2026 LIN3S \xB7 lin3s.com"), /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--lin3s-font-display)",
      fontSize: 15,
      color: "var(--lin3s-inverse-ink)"
    }
  }, "Humans leading data."))));
}
function FooterCol({
  title,
  links,
  onNavigate
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--lin3s-font-body)",
      fontSize: 11,
      letterSpacing: 1.2,
      textTransform: "uppercase",
      color: "var(--lin3s-inverse-ink-soft)"
    }
  }, title), links.map(l => /*#__PURE__*/React.createElement("a", {
    key: l,
    href: "#",
    onClick: e => {
      e.preventDefault();
      onNavigate && onNavigate("home");
    },
    style: {
      fontFamily: "var(--lin3s-font-body)",
      fontSize: 14,
      color: "var(--lin3s-inverse-ink)",
      textDecoration: "none",
      opacity: 0.92
    }
  }, l)));
}
Object.assign(window, {
  Nav,
  Footer,
  MediaTile,
  Section
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/website/site-chrome.jsx", error: String((e && e.message) || e) }); }

// ui_kits/website/site-contact.jsx
try { (() => {
/* LIN3S website — Contact view with a working (fake) form. */
const _dsContact = window.LIN3SDesignSystem_a9fd2b;
function Contact() {
  const [sent, setSent] = React.useState(false);
  const [form, setForm] = React.useState({
    name: "",
    email: "",
    sector: "Retail",
    msg: "",
    consent: false
  });
  const set = k => e => setForm(f => ({
    ...f,
    [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value
  }));
  const valid = form.name.trim() && /\S+@\S+\.\S+/.test(form.email) && form.consent;
  return /*#__PURE__*/React.createElement(window.Section, {
    bg: "canvas"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: 64,
      alignItems: "start"
    },
    className: "lin3s-contact-grid"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 22
    }
  }, /*#__PURE__*/React.createElement(_dsContact.Eyebrow, {
    marker: true
  }, "Let's talk"), /*#__PURE__*/React.createElement("h1", {
    style: {
      margin: 0,
      fontFamily: "var(--lin3s-font-display)",
      fontSize: 56,
      fontWeight: 500,
      lineHeight: 1.04,
      letterSpacing: "-1.1px",
      color: "var(--lin3s-ink)"
    }
  }, "Tell us where you want to go."), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      maxWidth: 420,
      fontFamily: "var(--lin3s-font-body)",
      fontSize: 18,
      lineHeight: 1.5,
      color: "var(--lin3s-ink-soft)"
    }
  }, "We work for you \u2014 but above all, with you. No jargon, honest feedback, and a measurable plan."), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 8,
      display: "flex",
      flexDirection: "column",
      gap: 6
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--lin3s-font-body)",
      fontSize: 14,
      color: "var(--lin3s-ink-muted)"
    }
  }, "hello@lin3s.com"), /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--lin3s-font-display)",
      fontSize: 18,
      color: "var(--lin3s-ink)",
      marginTop: 8
    }
  }, "Humans leading data."))), /*#__PURE__*/React.createElement(_dsContact.Card, null, sent ? /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "32px 8px",
      textAlign: "center",
      display: "flex",
      flexDirection: "column",
      gap: 12,
      alignItems: "center"
    }
  }, /*#__PURE__*/React.createElement(window.IsoOk, null), /*#__PURE__*/React.createElement("h3", {
    style: {
      margin: 0,
      fontFamily: "var(--lin3s-font-display)",
      fontSize: 24,
      color: "var(--lin3s-ink)"
    }
  }, "Thanks \u2014 we'll be in touch."), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      fontFamily: "var(--lin3s-font-body)",
      fontSize: 15,
      color: "var(--lin3s-ink-soft)"
    }
  }, "A LIN3S specialist will reply within one working day."), /*#__PURE__*/React.createElement(_dsContact.Button, {
    variant: "secondary",
    onClick: () => {
      setSent(false);
      setForm({
        name: "",
        email: "",
        sector: "Retail",
        msg: "",
        consent: false
      });
    },
    style: {
      marginTop: 8
    }
  }, "Send another")) : /*#__PURE__*/React.createElement("form", {
    onSubmit: e => {
      e.preventDefault();
      if (valid) setSent(true);
    },
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 18
    }
  }, /*#__PURE__*/React.createElement(_dsContact.Input, {
    label: "Name",
    placeholder: "Your name",
    value: form.name,
    onChange: set("name")
  }), /*#__PURE__*/React.createElement(_dsContact.Input, {
    label: "Work email",
    type: "email",
    placeholder: "you@company.com",
    value: form.email,
    onChange: set("email")
  }), /*#__PURE__*/React.createElement(_dsContact.Select, {
    label: "Sector",
    options: ["Retail", "B2B", "Sports", "Other"],
    value: form.sector,
    onChange: set("sector")
  }), /*#__PURE__*/React.createElement(_dsContact.Input, {
    label: "What do you want to move?",
    placeholder: "A metric, a launch, a question\u2026",
    value: form.msg,
    onChange: set("msg")
  }), /*#__PURE__*/React.createElement(_dsContact.Checkbox, {
    label: "I accept the privacy policy",
    checked: form.consent,
    onChange: set("consent")
  }), /*#__PURE__*/React.createElement(_dsContact.Button, {
    type: "submit",
    disabled: !valid,
    style: {
      marginTop: 4
    }
  }, "Send message")))));
}
function IsoOk() {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "inline-flex",
      gap: 5
    }
  }, /*#__PURE__*/React.createElement("i", {
    style: {
      width: 12,
      height: 16,
      background: "var(--lin3s-ink)",
      borderRadius: 1,
      display: "block"
    }
  }), /*#__PURE__*/React.createElement("i", {
    style: {
      width: 12,
      height: 16,
      background: "var(--lin3s-ink)",
      borderRadius: 1,
      display: "block"
    }
  }), /*#__PURE__*/React.createElement("i", {
    style: {
      width: 12,
      height: 16,
      background: "var(--lin3s-comm-red)",
      borderRadius: 1,
      display: "block"
    }
  }));
}
Object.assign(window, {
  Contact,
  IsoOk
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/website/site-contact.jsx", error: String((e && e.message) || e) }); }

// ui_kits/website/site-home.jsx
try { (() => {
/* LIN3S website — Home view. Hero (6/6) → services → stat band → featured work → campaign CTA. */
const _dsHome = window.LIN3SDesignSystem_a9fd2b;

/* Signature "pixel / data-disintegration" treatment, applied live over any photo:
   the subject's right edge erodes to white pixels and disperses into brand-tone data-bits
   on pure white. Works on hotlinked HD imagery — no baked canvas needed. */
function DisintegrateImage({
  src,
  alt,
  ratio = "5 / 4",
  radius = "var(--lin3s-rounded-lg)"
}) {
  const pixels = React.useMemo(() => {
    const cols = 30,
      rows = 24,
      arr = [];
    const tones = ["#7FC7D6", "#BFE6EC", "#A9DCE6", "#5FB0C2", "#2B2422", "#E7B79B"];
    for (let cx = 0; cx < cols; cx++) {
      const fx = cx / (cols - 1);
      if (fx < 0.5) continue;
      const t = (fx - 0.5) / 0.5; // 0 at 50% width → 1 at right edge
      for (let cy = 0; cy < rows; cy++) {
        const p = (1 - t) * 0.5 + 0.04;
        if (Math.random() < p) {
          const erosion = t < 0.32;
          arr.push({
            left: fx * 100 + Math.random() * 1.6,
            top: cy / (rows - 1) * 100 + Math.random() * 1.6,
            size: Math.random() < 0.34 ? 5 : 9,
            bg: erosion ? "#ffffff" : tones[Math.random() * tones.length | 0],
            op: erosion ? (0.5 + t).toFixed(2) : (0.28 + (1 - t) * 0.6).toFixed(2)
          });
        }
      }
    }
    return arr;
  }, [src]);
  return /*#__PURE__*/React.createElement("div", {
    style: {
      position: "relative",
      width: "100%",
      aspectRatio: ratio,
      background: "#fff",
      borderRadius: radius,
      overflow: "hidden"
    }
  }, /*#__PURE__*/React.createElement("img", {
    src: src,
    alt: alt,
    crossOrigin: "anonymous",
    style: {
      position: "absolute",
      inset: 0,
      width: "100%",
      height: "100%",
      objectFit: "cover",
      display: "block",
      WebkitMaskImage: "linear-gradient(90deg, #000 54%, transparent 93%)",
      maskImage: "linear-gradient(90deg, #000 54%, transparent 93%)"
    }
  }), pixels.map((p, i) => /*#__PURE__*/React.createElement("span", {
    key: i,
    style: {
      position: "absolute",
      left: p.left + "%",
      top: p.top + "%",
      width: p.size,
      height: p.size,
      background: p.bg,
      opacity: p.op,
      borderRadius: 1
    }
  })));
}
function Hero({
  onNavigate
}) {
  return /*#__PURE__*/React.createElement(window.Section, {
    bg: "canvas",
    style: {
      paddingBottom: 64
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1.05fr 0.95fr",
      gap: 56,
      alignItems: "center"
    },
    className: "lin3s-hero-grid"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 24
    }
  }, /*#__PURE__*/React.createElement(_dsHome.Eyebrow, {
    marker: true
  }, "Digital experts \xB7 powered by data"), /*#__PURE__*/React.createElement("h1", {
    style: {
      margin: 0,
      fontFamily: "var(--lin3s-font-display)",
      fontSize: 64,
      fontWeight: 500,
      lineHeight: 1.02,
      letterSpacing: "-1.4px",
      color: "var(--lin3s-ink)"
    }
  }, "Digital experts unlocking your potential."), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      maxWidth: 460,
      fontFamily: "var(--lin3s-font-body)",
      fontSize: 20,
      lineHeight: 1.5,
      color: "var(--lin3s-ink-soft)"
    }
  }, "We connect data and people \u2014 because behind every metric there are decisions, and a real chance to transform a business."), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 12,
      marginTop: 4
    }
  }, /*#__PURE__*/React.createElement(_dsHome.Button, {
    onClick: () => onNavigate("contact")
  }, "Get in touch"), /*#__PURE__*/React.createElement(_dsHome.Button, {
    variant: "secondary",
    onClick: () => onNavigate("work")
  }, "View work"))), /*#__PURE__*/React.createElement(DisintegrateImage, {
    src: "https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?fm=jpg&q=80&w=1200&h=960&fit=crop&crop=faces",
    alt: "A LIN3S team member dissolving into data",
    ratio: "5 / 4"
  })));
}
const SERVICES = [{
  t: "Data & Analytics",
  d: "Measurement frameworks, dashboards and the discipline to make data trustworthy."
}, {
  t: "Digital Strategy",
  d: "From raw analysis to an actionable, high-impact route — fast."
}, {
  t: "Experience & Product",
  d: "Interfaces designed around the shopper, the fan, the buyer."
}, {
  t: "Growth & Activation",
  d: "Campaigns and CRO that turn the numbers into tangible progress."
}];
function Services() {
  return /*#__PURE__*/React.createElement(window.Section, {
    bg: "muted"
  }, /*#__PURE__*/React.createElement(_dsHome.Eyebrow, null, "What we do"), /*#__PURE__*/React.createElement("h2", {
    style: {
      margin: "14px 0 40px",
      maxWidth: 720,
      fontFamily: "var(--lin3s-font-display)",
      fontSize: 40,
      fontWeight: 500,
      lineHeight: 1.08,
      letterSpacing: "-0.8px",
      color: "var(--lin3s-ink)"
    }
  }, "We turn raw analysis into measurable, growth-oriented strategy."), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "repeat(2, 1fr)",
      gap: 16
    },
    className: "lin3s-svc-grid"
  }, SERVICES.map((s, i) => /*#__PURE__*/React.createElement(_dsHome.Card, {
    key: i,
    style: {
      background: "var(--lin3s-canvas)",
      border: "1px solid var(--lin3s-hairline)"
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      alignItems: "baseline",
      gap: 12
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--lin3s-font-display)",
      fontSize: 18,
      color: "var(--lin3s-ink-muted)"
    }
  }, "0", i + 1), /*#__PURE__*/React.createElement("h3", {
    style: {
      margin: 0,
      fontFamily: "var(--lin3s-font-display)",
      fontSize: 22,
      fontWeight: 500,
      letterSpacing: "-0.3px",
      color: "var(--lin3s-ink)"
    }
  }, s.t)), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: "10px 0 0",
      fontFamily: "var(--lin3s-font-body)",
      fontSize: 15,
      lineHeight: 1.5,
      color: "var(--lin3s-ink-soft)"
    }
  }, s.d)))));
}
function StatBand() {
  const stats = [{
    v: "82%",
    c: "of clients renew within the first year"
  }, {
    v: "3.4×",
    c: "average return on data investment"
  }, {
    v: "+40",
    c: "specialists across our sector hubs"
  }];
  return /*#__PURE__*/React.createElement(window.Section, {
    bg: "canvas"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "repeat(3, 1fr)",
      gap: 40,
      borderTop: "1px solid var(--lin3s-hairline)",
      paddingTop: 48
    },
    className: "lin3s-stat-grid"
  }, stats.map((s, i) => /*#__PURE__*/React.createElement(_dsHome.StatNumber, {
    key: i,
    value: s.v,
    caption: s.c,
    accent: i === 1
  }))));
}
function FeaturedWork({
  onNavigate
}) {
  const cases = [{
    tag: "Retail",
    t: "Conversion, rebuilt around the shopper.",
    img: "https://images.unsplash.com/photo-1556157382-97eda2d62296?fm=jpg&q=78&w=900&h=680&fit=crop&crop=faces"
  }, {
    tag: "B2B",
    t: "A pipeline that finally tells the truth.",
    img: "https://images.unsplash.com/photo-1517245386807-bb43f82c33c4?fm=jpg&q=78&w=900&h=680&fit=crop&crop=faces"
  }, {
    tag: "Sports",
    t: "Turning matchday data into season-long loyalty.",
    img: "https://images.unsplash.com/photo-1560250097-0b93528c311a?fm=jpg&q=78&w=900&h=680&fit=crop&crop=faces"
  }];
  return /*#__PURE__*/React.createElement(window.Section, {
    bg: "muted"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      justifyContent: "space-between",
      alignItems: "flex-end",
      marginBottom: 36,
      flexWrap: "wrap",
      gap: 16
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(_dsHome.Eyebrow, null, "Selected work"), /*#__PURE__*/React.createElement("h2", {
    style: {
      margin: "12px 0 0",
      fontFamily: "var(--lin3s-font-display)",
      fontSize: 40,
      fontWeight: 500,
      letterSpacing: "-0.8px",
      color: "var(--lin3s-ink)"
    }
  }, "The numbers, moved.")), /*#__PURE__*/React.createElement(_dsHome.Button, {
    variant: "outline",
    onClick: () => onNavigate("work")
  }, "All cases")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "repeat(3, 1fr)",
      gap: 16
    },
    className: "lin3s-work-grid"
  }, cases.map((c, i) => /*#__PURE__*/React.createElement("a", {
    key: i,
    href: "#",
    onClick: e => {
      e.preventDefault();
      onNavigate("work");
    },
    style: {
      textDecoration: "none",
      display: "flex",
      flexDirection: "column",
      gap: 14
    }
  }, /*#__PURE__*/React.createElement(DisintegrateImage, {
    src: c.img,
    alt: c.t,
    ratio: "4 / 3"
  }), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(_dsHome.Eyebrow, null, c.tag), /*#__PURE__*/React.createElement("h3", {
    style: {
      margin: "8px 0 0",
      fontFamily: "var(--lin3s-font-display)",
      fontSize: 22,
      fontWeight: 500,
      letterSpacing: "-0.3px",
      lineHeight: 1.2,
      color: "var(--lin3s-ink)"
    }
  }, c.t))))));
}
function CampaignCTA({
  onNavigate
}) {
  return /*#__PURE__*/React.createElement(window.Section, {
    bg: "canvas"
  }, /*#__PURE__*/React.createElement(_dsHome.ColorBlock, {
    color: "red",
    eyebrow: "Let's talk",
    headline: "Your business has been just boosted.",
    body: "Tell us where you want to go. We'll define the path and make it real \u2014 fast.",
    action: /*#__PURE__*/React.createElement(_dsHome.Button, {
      variant: "onInverse",
      onClick: () => onNavigate("contact")
    }, "Start a project")
  }));
}
function Home({
  onNavigate
}) {
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Hero, {
    onNavigate: onNavigate
  }), /*#__PURE__*/React.createElement(Services, null), /*#__PURE__*/React.createElement(StatBand, null), /*#__PURE__*/React.createElement(FeaturedWork, {
    onNavigate: onNavigate
  }), /*#__PURE__*/React.createElement(CampaignCTA, {
    onNavigate: onNavigate
  }));
}
Object.assign(window, {
  Home
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/website/site-home.jsx", error: String((e && e.message) || e) }); }

// ui_kits/website/site-work.jsx
try { (() => {
/* LIN3S website — Work index + a single case-study reader. */
const _dsWork = window.LIN3SDesignSystem_a9fd2b;
const CASES = [{
  tag: "Retail",
  t: "Conversion, rebuilt around the shopper.",
  stat: "+38%",
  statc: "checkout completion",
  body: "We rebuilt the analytics layer, then the funnel — measuring every step until the numbers moved."
}, {
  tag: "B2B",
  t: "A pipeline that finally tells the truth.",
  stat: "2.1×",
  statc: "qualified leads",
  body: "One source of truth across marketing and sales, with attribution the whole team trusts."
}, {
  tag: "Sports",
  t: "Matchday data into season-long loyalty.",
  stat: "+27%",
  statc: "season renewals",
  body: "Behavioural signals from matchday turned into a year-round membership strategy."
}, {
  tag: "Retail",
  t: "Stock that follows demand, not guesswork.",
  stat: "−19%",
  statc: "overstock",
  body: "Forecasting models wired straight into the merchandising calendar."
}];
function Work({
  onOpenCase
}) {
  return /*#__PURE__*/React.createElement(window.Section, {
    bg: "canvas"
  }, /*#__PURE__*/React.createElement(_dsWork.Eyebrow, {
    marker: true
  }, "Selected work"), /*#__PURE__*/React.createElement("h1", {
    style: {
      margin: "14px 0 12px",
      maxWidth: 760,
      fontFamily: "var(--lin3s-font-display)",
      fontSize: 56,
      fontWeight: 500,
      lineHeight: 1.05,
      letterSpacing: "-1.1px",
      color: "var(--lin3s-ink)"
    }
  }, "What isn't measured doesn't exist."), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: "0 0 48px",
      maxWidth: 520,
      fontFamily: "var(--lin3s-font-body)",
      fontSize: 18,
      lineHeight: 1.5,
      color: "var(--lin3s-ink-soft)"
    }
  }, "A selection of work across our retail, B2B and sports hubs \u2014 each one measured to impact."), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: 16
    },
    className: "lin3s-work-grid"
  }, CASES.map((c, i) => /*#__PURE__*/React.createElement("a", {
    key: i,
    href: "#",
    onClick: e => {
      e.preventDefault();
      onOpenCase(i);
    },
    style: {
      textDecoration: "none"
    }
  }, /*#__PURE__*/React.createElement(_dsWork.Card, {
    variant: "media"
  }, /*#__PURE__*/React.createElement(window.MediaTile, {
    label: c.tag,
    ratio: "16 / 9",
    style: {
      borderRadius: 0
    }
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      padding: 24
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      justifyContent: "space-between",
      alignItems: "baseline",
      gap: 16
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(_dsWork.Eyebrow, null, c.tag), /*#__PURE__*/React.createElement("h3", {
    style: {
      margin: "8px 0 0",
      fontFamily: "var(--lin3s-font-display)",
      fontSize: 24,
      fontWeight: 500,
      letterSpacing: "-0.4px",
      lineHeight: 1.18,
      color: "var(--lin3s-ink)"
    }
  }, c.t)), /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--lin3s-font-display)",
      fontSize: 34,
      color: i === 0 ? "var(--lin3s-comm-red)" : "var(--lin3s-ink)",
      whiteSpace: "nowrap",
      letterSpacing: "-0.6px"
    }
  }, c.stat))))))));
}
function CaseStudy({
  index,
  onBack
}) {
  const c = CASES[index] || CASES[0];
  return /*#__PURE__*/React.createElement(window.Section, {
    bg: "canvas"
  }, /*#__PURE__*/React.createElement("button", {
    onClick: onBack,
    style: {
      border: "none",
      background: "none",
      cursor: "pointer",
      padding: 0,
      marginBottom: 28,
      fontFamily: "var(--lin3s-font-body)",
      fontSize: 13,
      fontWeight: 500,
      color: "var(--lin3s-ink-muted)",
      display: "inline-flex",
      alignItems: "center",
      gap: 8
    }
  }, "\u2190 All work"), /*#__PURE__*/React.createElement(_dsWork.Eyebrow, {
    marker: true
  }, c.tag, " hub \xB7 Case study"), /*#__PURE__*/React.createElement("h1", {
    style: {
      margin: "14px 0 0",
      maxWidth: 820,
      fontFamily: "var(--lin3s-font-display)",
      fontSize: 56,
      fontWeight: 500,
      lineHeight: 1.04,
      letterSpacing: "-1.1px",
      color: "var(--lin3s-ink)"
    }
  }, c.t), /*#__PURE__*/React.createElement("div", {
    style: {
      margin: "40px 0",
      borderRadius: "var(--lin3s-rounded-lg)",
      overflow: "hidden"
    }
  }, /*#__PURE__*/React.createElement(window.MediaTile, {
    label: "Project imagery",
    ratio: "21 / 9",
    style: {
      borderRadius: 0
    }
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "grid",
      gridTemplateColumns: "1.4fr 1fr",
      gap: 56,
      alignItems: "start"
    },
    className: "lin3s-case-grid"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 18
    }
  }, /*#__PURE__*/React.createElement("h2", {
    style: {
      margin: 0,
      fontFamily: "var(--lin3s-font-display)",
      fontSize: 24,
      fontWeight: 400,
      letterSpacing: "-0.24px",
      color: "var(--lin3s-ink)"
    }
  }, "The challenge"), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      fontFamily: "var(--lin3s-font-body)",
      fontSize: 18,
      lineHeight: 1.55,
      color: "var(--lin3s-ink-soft)"
    }
  }, c.body), /*#__PURE__*/React.createElement("p", {
    style: {
      margin: 0,
      fontFamily: "var(--lin3s-font-body)",
      fontSize: 16,
      lineHeight: 1.6,
      color: "var(--lin3s-ink-soft)"
    }
  }, "We worked ", /*#__PURE__*/React.createElement("em", null, "with"), " the team \u2014 not just for them \u2014 sharing the models and the reasoning, so every decision after us could stand on its own data.")), /*#__PURE__*/React.createElement(_dsWork.Card, {
    variant: "inverse",
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 28
    }
  }, /*#__PURE__*/React.createElement(_dsWork.StatNumber, {
    value: c.stat,
    caption: c.statc,
    size: "56px",
    tone: "inverse"
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      borderTop: "1px solid rgba(255,255,255,.16)",
      paddingTop: 20
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--lin3s-font-body)",
      fontSize: 13,
      lineHeight: 1.5,
      color: "var(--lin3s-inverse-ink-soft)"
    }
  }, "Measured against a pre-engagement baseline over 6 months.")))));
}
Object.assign(window, {
  Work,
  CaseStudy
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/website/site-work.jsx", error: String((e && e.message) || e) }); }

__ds_ns.ColorBlock = __ds_scope.ColorBlock;

__ds_ns.IsotypeMarker = __ds_scope.IsotypeMarker;

__ds_ns.Wordmark = __ds_scope.Wordmark;

__ds_ns.Button = __ds_scope.Button;

__ds_ns.IconButton = __ds_scope.IconButton;

__ds_ns.Badge = __ds_scope.Badge;

__ds_ns.Card = __ds_scope.Card;

__ds_ns.Eyebrow = __ds_scope.Eyebrow;

__ds_ns.StatNumber = __ds_scope.StatNumber;

__ds_ns.Checkbox = __ds_scope.Checkbox;

__ds_ns.Input = __ds_scope.Input;

__ds_ns.Select = __ds_scope.Select;

__ds_ns.Switch = __ds_scope.Switch;

})();
