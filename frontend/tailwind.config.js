/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          900: "#0d1117",
          800: "#161b22",
          700: "#21262d",
          500: "#388bfd",
          400: "#58a6ff",
        },
      },
    },
  },
  plugins: [],
};
