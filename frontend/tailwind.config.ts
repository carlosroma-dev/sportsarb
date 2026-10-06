import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg:      "#080C14",
        card:    "#0F1623",
        surface: "#161D2E",
        primary: { DEFAULT: "#9EEA2F", hover: "#7FD320" },
        gold:    "#D6A72E",
        amber:   "#F59E0B",
        accent:  "#3B82F6",
        muted:   "#8B95A8",
        line:    "rgba(255,255,255,.07)",
      },
      boxShadow: {
        neon: "0 0 0 1px rgba(158,234,47,.35) inset, 0 8px 28px rgba(158,234,47,.10)",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;
