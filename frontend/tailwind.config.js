/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 900: "#101820", 800: "#16212c", 700: "#1d2b38", 600: "#27394a" },
        accent: { DEFAULT: "#3f7fd6", soft: "#7fb0ea", dim: "#22406b" },
        paper: "#f4f6f8",
      },
      boxShadow: {
        panel: "0 10px 30px rgba(16, 24, 32, 0.35)",
        glow: "0 0 0 1px rgba(127,176,234,0.35), 0 0 24px rgba(63,127,214,0.25)",
      },
    },
  },
  plugins: [],
};
