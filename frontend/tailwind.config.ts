import type { Config } from 'tailwindcss';

/**
 * The palette is deliberately narrow: one surface ramp, two accent hues.
 *
 * Amber is reserved for alerting things (severity, degraded sources, KEV) and
 * cyan for observational things (signals, coverage, navigation). Holding that
 * line is what lets a reader scan the dashboard and know whether a colour
 * means "look at me" or "this is information" without reading a legend.
 */
const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}', './lib/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        surface: {
          900: '#070b11',
          800: '#0b1119',
          700: '#111a25',
          600: '#17222f',
          500: '#1e2b3b',
          400: '#2a3b4f',
        },
        ink: {
          DEFAULT: '#e8eef7',
          muted: '#8fa3bd',
          faint: '#5d7089',
        },
        signal: {
          DEFAULT: '#38bdf8',
          dim: '#0e7490',
        },
        alert: {
          DEFAULT: '#ffb020',
          dim: '#92400e',
        },
        critical: '#ff5470',
        good: '#34d399',
      },
      fontFamily: {
        // A system stack rather than a fetched webfont: it removes a network
        // dependency from the build and renders identically on every platform.
        sans: [
          'ui-sans-serif',
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'Roboto',
          'Helvetica Neue',
          'Arial',
          'sans-serif',
        ],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      boxShadow: {
        panel: '0 1px 0 0 rgba(255,255,255,0.04) inset, 0 8px 24px -12px rgba(0,0,0,0.8)',
      },
    },
  },
  plugins: [],
};

export default config;
