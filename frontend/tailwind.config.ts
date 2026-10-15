import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        success: "hsl(var(--success))",
        warning: "hsl(var(--warning))",
        hud: {
          dark: "hsl(var(--hud-dark))",
          panel2: "hsl(var(--hud-panel2))",
          bar: "hsl(var(--hud-bar))",
          ghost: "hsl(var(--hud-ghost))",
          dim: "hsl(var(--hud-pri-dim))",
          med: "hsl(var(--hud-text-med))",
          white: "hsl(var(--hud-white))",
          borderb: "hsl(var(--hud-border-b))",
          bordera: "hsl(var(--hud-border-a))",
          orange: "hsl(var(--hud-acc))",
          amber: "hsl(var(--hud-acc2))",
          green: "hsl(var(--hud-green))",
          red: "hsl(var(--hud-red))",
        },
      },
      borderRadius: { lg: "var(--radius)", md: "calc(var(--radius) - 0.2rem)", sm: "calc(var(--radius) - 0.4rem)" },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "Courier New", "monospace"],
      },
      keyframes: {
        "pulse-dot": { "0%, 100%": { opacity: "1" }, "50%": { opacity: "0.35" } },
      },
      animation: { "pulse-dot": "pulse-dot 1.4s ease-in-out infinite" },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
export default config;
