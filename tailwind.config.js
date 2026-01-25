/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./web/templates/**/*.html",
    "./web/static/js/**/*.js"
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        surface: {
          base: '#050505',
          elevated: '#0a0a0a',
          DEFAULT: '#111111',
          hover: '#161616',
        },
        border: {
          subtle: '#1a1a1a',
          DEFAULT: '#262626',
          strong: '#333333',
        },
        brand: {
          DEFAULT: '#22c55e',
          hover: '#16a34a',
          muted: 'rgba(34, 197, 94, 0.12)',
          glow: 'rgba(34, 197, 94, 0.4)',
        }
      },
      fontFamily: {
        sans: ['Outfit', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      animation: {
        'pulse-slow': 'pulse 3s ease-in-out infinite',
        'glow': 'glow 2s ease-in-out infinite',
      }
    }
  },
  plugins: [],
}
