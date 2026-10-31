const SHADES = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950];
const scale = (name) =>
  Object.fromEntries(SHADES.map((s) => [s, `rgb(var(--${name}-${s}) / <alpha-value>)`]));

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Colour themes (src/themes.css): gray is the theme's neutral scale and blue its
        // accent, so every existing gray-*/blue-* class follows the chosen theme.
        gray: scale("gray"),
        blue: scale("blue"),
        brand: {
          50:  "#eff6ff",
          100: "#dbeafe",
          500: "#3b82f6",
          600: "#2563eb",
          700: "#1d4ed8",
        },
      },
    },
  },
  plugins: [],
};
