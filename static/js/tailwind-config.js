tailwind.config = {
    plugins: [
        // Add typography plugin for prose classes
        function({ addUtilities }) {
            addUtilities({
                '.prose h1': { fontSize: '2.25rem', fontWeight: '800', marginTop: '0', marginBottom: '0.8888889em' },
                '.prose h2': { fontSize: '1.5rem', fontWeight: '700', marginTop: '2em', marginBottom: '1em' },
                '.prose h3': { fontSize: '1.25rem', fontWeight: '600', marginTop: '1.6em', marginBottom: '0.6em' },
                '.prose h4': { fontSize: '1.125rem', fontWeight: '600', marginTop: '1.5em', marginBottom: '0.5em' },
                '.prose h5': { fontSize: '1rem', fontWeight: '600', marginTop: '1.5em', marginBottom: '0.5em' },
                '.prose h6': { fontSize: '1rem', fontWeight: '600', marginTop: '1.5em', marginBottom: '0.5em' },
                '.prose a': { color: '#2563eb', textDecoration: 'underline', fontWeight: '500' },
                '.prose a:hover': { color: '#1d4ed8' },
                '.prose strong': { fontWeight: '600' },
                '.prose p': { marginTop: '1.25em', marginBottom: '1.25em' },
                '.prose ul': { marginTop: '1.25em', marginBottom: '1.25em', listStyleType: 'disc', paddingLeft: '1.625em' },
                '.prose ol': { marginTop: '1.25em', marginBottom: '1.25em', listStyleType: 'decimal', paddingLeft: '1.625em' },
                '.prose li': { marginTop: '0.5em', marginBottom: '0.5em' },
            })
        }
    ],
    theme: {
        extend: {
            fontFamily: {
                'ibm': ['"IBM Plex Mono"', 'monospace'],
            },
            colors: {
                flexoki: {
                    'paper': '#FFFCF0',
                    'paper-light': '#FFFEF8',
                    'ui': '#F2F0E5',
                    'ui-2': '#E6E4D9',
                    'ui-3': '#DAD8CE',
                    'tx': '#100F0F',
                    'tx-2': '#1C1B1A',
                    'tx-3': '#282726',
                    'tx-4': '#343332',
                    'tx-5': '#403F3E',
                    're': '#AF3029',
                    're-2': '#D14D41',
                    'or': '#BC5215',
                    'or-2': '#DA702C',
                    'ye': '#AD8301',
                    'ye-2': '#D0A215',
                    'gr': '#66800B',
                    'gr-2': '#879A39',
                    'cy': '#24837B',
                    'cy-2': '#3AA99F',
                    'bl': '#205EA6',
                    'bl-2': '#4385BE',
                    'pu': '#5E409D',
                    'pu-2': '#8B7EC8',
                    'ma': '#A02F6F',
                    'ma-2': '#CE5D97'
                }
            }
        }
    }
}