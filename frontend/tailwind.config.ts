import type { Config } from "tailwindcss";

// Warm editorial palette — Claude.ai-inspired paper background with a clay
// accent and serif assistant text. Distinct from the previous dark zinc/ink.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Warm paper neutrals (cool-warm cream).
        paper: {
          50: "#ffffff",
          100: "#faf9f7",
          150: "#f5f3ef",
          200: "#f0ede7",
          300: "#e7e3db",
          400: "#d8d3c8",
          500: "#bfb9ac",
          600: "#9a9488",
          700: "#6f6a60",
          800: "#46423b",
          900: "#2b2925",
          950: "#1a1916",
        },
        // Claude-style clay/coral accent.
        clay: {
          50: "#fdf6f2",
          100: "#faece4",
          200: "#f4d4c4",
          300: "#ebb59c",
          400: "#d98c6c",
          500: "#d97757",
          600: "#c2613f",
          700: "#a14e32",
          800: "#83402b",
          900: "#6b3624",
        },
      },
      fontFamily: {
        assistant: ["ui-serif", "Georgia", "Cambria", '"Times New Roman"', "serif"],
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "Noto Sans", "sans-serif"],
      },
      boxShadow: {
        soft: "0 1px 2px rgba(43,41,37,0.04), 0 4px 12px rgba(43,41,37,0.04)",
        composer: "0 2px 8px rgba(43,41,37,0.06), 0 12px 32px rgba(43,41,37,0.06)",
      },
    },
  },
  plugins: [],
};
export default config;
