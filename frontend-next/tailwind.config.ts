import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-inter)", "Inter", "system-ui", "-apple-system", "sans-serif"],
      },
      // Semantic colors map onto the design tokens defined in app/globals.css.
      colors: {
        ink: {
          DEFAULT: "var(--ta-ink)",
          soft: "var(--ta-ink-soft)",
        },
        muted: "var(--ta-muted)",
        faint: "var(--ta-faint)",
        line: {
          DEFAULT: "var(--ta-line)",
          soft: "var(--ta-line-soft)",
          faint: "var(--ta-line-faint)",
        },
        panel: "var(--ta-panel)",
        surface: "var(--ta-surface-muted)",
        accent: {
          DEFAULT: "var(--ta-accent)",
          hover: "var(--ta-accent-hover)",
          strong: "var(--ta-accent-strong)",
          soft: "var(--ta-accent-soft)",
          border: "var(--ta-accent-border)",
        },
        queued: {
          fg: "var(--ta-queued-fg)",
          bg: "var(--ta-queued-bg)",
          dot: "var(--ta-queued-dot)",
        },
        running: {
          fg: "var(--ta-running-fg)",
          bg: "var(--ta-running-bg)",
          dot: "var(--ta-running-dot)",
        },
        success: {
          fg: "var(--ta-success-fg)",
          bg: "var(--ta-success-bg)",
          soft: "var(--ta-success-soft)",
          dot: "var(--ta-success-dot)",
        },
        failed: {
          fg: "var(--ta-failed-fg)",
          bg: "var(--ta-failed-bg)",
          border: "var(--ta-failed-border)",
          dot: "var(--ta-failed-dot)",
        },
        neutralstatus: {
          fg: "var(--ta-neutral-fg)",
          bg: "var(--ta-neutral-bg)",
          dot: "var(--ta-neutral-dot)",
        },
      },
      borderRadius: {
        field: "var(--radius-md)",
        card: "var(--radius-lg)",
        pill: "var(--radius-pill)",
      },
      boxShadow: {
        card: "var(--shadow-card)",
        soft: "var(--shadow-soft)",
        pop: "var(--shadow-pop)",
      },
    },
  },
  plugins: [typography],
};
export default config;
