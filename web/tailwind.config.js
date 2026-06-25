/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#f5f7fb",
        surface: "#ffffff",
        "surface-2": "#fafbff",
        ink: "#1f2533",
        body: "#475069",
        muted: "#8a93a6",
        line: "#e9ecf4",
        "line-strong": "#dfe3ee",
        brand: {
          DEFAULT: "#5457ea",
          50: "#eef0fe",
          100: "#e2e5fd",
          200: "#c9cefb",
          500: "#5457ea",
          600: "#4346d4",
          700: "#3638b8",
        },
        violet: "#8b5cf6",
        success: { DEFAULT: "#10b981", soft: "#e7f8f1" },
        danger: { DEFAULT: "#ef4444", soft: "#fdeaea" },
        warning: { DEFAULT: "#f59e0b", soft: "#fef3e2" },
        info: { DEFAULT: "#3b82f6", soft: "#e8f0fe" },
        neutral: { DEFAULT: "#94a3b8", soft: "#f1f3f7" },
      },
      fontFamily: {
        sans: [
          "Inter",
          "PingFang SC",
          "HarmonyOS Sans SC",
          "Microsoft YaHei",
          "system-ui",
          "sans-serif",
        ],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        soft: "0 1px 2px rgba(16,23,40,.04), 0 1px 3px rgba(16,23,40,.04)",
        card: "0 1px 3px rgba(16,23,40,.05), 0 6px 16px rgba(16,23,40,.05)",
        pop: "0 12px 34px rgba(16,23,40,.12)",
        brand: "0 8px 20px rgba(84,87,234,.28)",
      },
      borderRadius: {
        xl: "12px",
        "2xl": "16px",
      },
      backgroundImage: {
        "brand-grad": "linear-gradient(135deg, #5b6ef5 0%, #8b5cf6 100%)",
        "brand-grad-soft":
          "linear-gradient(135deg, rgba(91,110,245,.10) 0%, rgba(139,92,246,.10) 100%)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "slide-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: { "100%": { transform: "translateX(100%)" } },
        "pulse-ring": {
          "0%": { boxShadow: "0 0 0 0 rgba(59,130,246,.4)" },
          "70%": { boxShadow: "0 0 0 6px rgba(59,130,246,0)" },
          "100%": { boxShadow: "0 0 0 0 rgba(59,130,246,0)" },
        },
      },
      animation: {
        "fade-in": "fade-in .25s ease-out both",
        "slide-up": "slide-up .3s ease-out both",
        shimmer: "shimmer 1.4s infinite",
        "pulse-ring": "pulse-ring 1.8s infinite",
      },
    },
  },
  plugins: [],
};
