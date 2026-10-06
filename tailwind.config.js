/** @type {import('tailwindcss').Config} */
const step = (name) => [
  `var(--ga-text-${name})`,
  {
    lineHeight: `var(--ga-text-${name}-line-height)`,
    fontWeight: `var(--ga-text-${name}-weight)`,
    letterSpacing: `var(--ga-text-${name}-tracking, normal)`,
  },
];

const plain = (name) => [`var(--ga-text-${name})`, { lineHeight: `var(--ga-text-${name}-line-height)` }];

module.exports = {
  content: ["./web/templates/**/*.html", "./web/static/js/**/*.js"],
  darkMode: "class",
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      inherit: "inherit",
      bg: "var(--ga-bg)",
      surface: "var(--ga-surface)",
      inset: "var(--ga-inset)",
      overlay: "var(--ga-overlay)",
      line: "var(--ga-line)",
      fg: "var(--ga-fg)",
      muted: "var(--ga-muted)",
      brand: {
        DEFAULT: "var(--ga-brand)",
        press: "var(--ga-brand-press)",
        soft: "var(--ga-brand-soft)",
      },
      "on-brand": "var(--ga-on-brand)",
      ok: { DEFAULT: "var(--ga-status-ok)", soft: "var(--ga-status-ok-soft)" },
      attention: { DEFAULT: "var(--ga-status-attention)", soft: "var(--ga-status-attention-soft)" },
      critical: { DEFAULT: "var(--ga-status-critical)", soft: "var(--ga-status-critical-soft)" },
      stale: { DEFAULT: "var(--ga-status-stale)", soft: "var(--ga-status-stale-soft)" },
      pending: { DEFAULT: "var(--ga-status-pending)", soft: "var(--ga-status-pending-soft)" },
      offline: { DEFAULT: "var(--ga-status-offline)", soft: "var(--ga-status-offline-soft)" },
    },
    fontFamily: {
      sans: "var(--ga-font-sans)",
      mono: "var(--ga-font-mono)",
    },
    fontSize: {
      xs: plain("caption"),
      sm: plain("body"),
      base: plain("body"),
      lg: plain("title-3"),
      xl: plain("title-2"),
      "2xl": plain("title-1"),
      "title-1": step("title-1"),
      "title-2": step("title-2"),
      "title-3": step("title-3"),
      body: step("body"),
      label: step("label"),
      caption: step("caption"),
      overline: step("overline"),
      metric: step("metric"),
      "metric-xl": step("metric-xl"),
    },
    borderRadius: {
      none: "0",
      control: "var(--ga-radius-control)",
      card: "var(--ga-radius-card)",
      sheet: "var(--ga-radius-sheet)",
      pill: "var(--ga-radius-pill)",
      full: "9999px",
    },
    extend: {
      maxWidth: { content: "1200px" },
    },
  },
  plugins: [],
};
