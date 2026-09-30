/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        colons: {
          accent: '#718aae',
          accentHover: '#58749b',
        },
      },
      animation: {
        'fade-in': 'fadeIn 0.25s ease-out',
        'pulse-dot': 'pulseDot 1.4s infinite',
      },
      keyframes: {
        fadeIn: { '0%': { opacity: '0', transform: 'translateY(6px)' }, '100%': { opacity: '1', transform: 'translateY(0)' } },
        pulseDot: { '0%,60%,100%': { opacity: '0.25' }, '30%': { opacity: '1' } },
      },
    },
  },
  plugins: [],
}
