/** @type {import('tailwindcss').Config} */
module.exports = {
    darkMode: 'class',
    content: [
        "./src/**/*.{js,ts,jsx,tsx,mdx}",
    ],
    theme: {
        extend: {
            // Brand color palette
            colors: {
                // Primary brand colors (Blue)
                primary: {
                    50: '#e6f0ff',
                    100: '#b3d1ff',
                    200: '#80b3ff',
                    300: '#4d94ff',
                    400: '#1a75ff',
                    500: '#0066CC', // Primary Blue
                    600: '#0052a3',
                    700: '#003d7a',
                    800: '#002952',
                    900: '#001429',
                },
                // Accent colors (cyan/teal gradient)
                accent: {
                    50: '#e6ffff',
                    100: '#b3ffff',
                    200: '#80ffff',
                    300: '#4dffff',
                    400: '#00e5ff',
                    500: '#00bcd4', // Accent cyan
                    600: '#0097a7',
                    700: '#00838f',
                    800: '#006064',
                    900: '#004d40',
                },
                // Dark theme backgrounds
                navy: {
                    50: '#e8eaf6',
                    100: '#c5cae9',
                    200: '#9fa8da',
                    300: '#7986cb',
                    400: '#5c6bc0',
                    500: '#3f51b5',
                    600: '#303f9f',
                    700: '#1a237e',
                    800: '#0d1b3e', // Main dark background
                    900: '#060d1f', // Darkest
                    950: '#030711', // Near black
                },
                // Status colors
                success: {
                    50: '#e8f5e9',
                    500: '#4caf50',
                    600: '#43a047',
                },
                warning: {
                    50: '#fff3e0',
                    500: '#ff9800',
                    600: '#fb8c00',
                },
                danger: {
                    50: '#ffebee',
                    500: '#f44336',
                    600: '#e53935',
                },
                // Background colors
                background: "var(--background)",
                foreground: "var(--foreground)",
                surface: "var(--surface)",
                'surface-hover': "var(--surface-hover)",
                border: "var(--border)",
                'text-primary': "var(--text-primary)",
                'text-secondary': "var(--text-secondary)",
                'text-muted': "var(--text-muted)",
            },
            fontFamily: {
                sans: ['Inter', 'Roboto', 'system-ui', '-apple-system', 'sans-serif'],
                display: ['Inter', 'Roboto', 'system-ui', 'sans-serif'],
                mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
            },
            fontSize: {
                'display-xl': ['4rem', { lineHeight: '1.1', letterSpacing: '-0.02em' }],
                'display-lg': ['3rem', { lineHeight: '1.1', letterSpacing: '-0.02em' }],
                'display-md': ['2.25rem', { lineHeight: '1.2', letterSpacing: '-0.01em' }],
                'display-sm': ['1.875rem', { lineHeight: '1.2', letterSpacing: '-0.01em' }],
            },
            boxShadow: {
                'glow': '0 0 20px rgba(0, 102, 204, 0.3)',
                'glow-lg': '0 0 40px rgba(0, 102, 204, 0.4)',
                'glow-accent': '0 0 20px rgba(0, 188, 212, 0.3)',
                'card': '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)',
                'card-hover': '0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05)',
            },
            backgroundImage: {
                // Brand gradients
                'gradient-radial': 'radial-gradient(var(--tw-gradient-stops))',
                'gradient-brand': 'linear-gradient(135deg, #0066CC 0%, #00bcd4 100%)',
                'gradient-dark': 'linear-gradient(180deg, #0d1b3e 0%, #060d1f 100%)',
                'gradient-card': 'linear-gradient(145deg, rgba(255,255,255,0.05) 0%, rgba(255,255,255,0.02) 100%)',
                'gradient-border': 'linear-gradient(135deg, rgba(0,102,204,0.5) 0%, rgba(0,188,212,0.5) 100%)',
            },
            animation: {
                'fade-in': 'fadeIn 0.5s ease-out',
                'slide-up': 'slideUp 0.4s ease-out',
                'slide-in-right': 'slideInRight 0.3s ease-out',
                'pulse-slow': 'pulse 3s infinite',
                'glow': 'glow 2s ease-in-out infinite alternate',
                'gradient': 'gradient 8s ease infinite',
            },
            keyframes: {
                fadeIn: {
                    '0%': { opacity: '0', transform: 'translateY(-10px)' },
                    '100%': { opacity: '1', transform: 'translateY(0)' },
                },
                slideUp: {
                    '0%': { opacity: '0', transform: 'translateY(20px)' },
                    '100%': { opacity: '1', transform: 'translateY(0)' },
                },
                slideInRight: {
                    '0%': { opacity: '0', transform: 'translateX(20px)' },
                    '100%': { opacity: '1', transform: 'translateX(0)' },
                },
                glow: {
                    '0%': { boxShadow: '0 0 5px rgba(0, 102, 204, 0.2)' },
                    '100%': { boxShadow: '0 0 20px rgba(0, 102, 204, 0.4)' },
                },
                gradient: {
                    '0%, 100%': { backgroundPosition: '0% 50%' },
                    '50%': { backgroundPosition: '100% 50%' },
                },
                shimmer: {
                    '0%': { transform: 'translateX(-100%) skewX(-12deg)' },
                    '100%': { transform: 'translateX(200%) skewX(-12deg)' },
                },
            },
            borderRadius: {
                'xl': '1rem',
                '2xl': '1.5rem',
            },
        },
    },
    plugins: [],
};
