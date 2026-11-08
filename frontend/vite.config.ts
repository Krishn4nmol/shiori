import { createRequire } from 'node:module'
import path from 'node:path'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, normalizePath } from 'vite'
import { viteStaticCopy } from 'vite-plugin-static-copy'

const require = createRequire(import.meta.url)
const pdfjsDir = path.dirname(require.resolve('pdfjs-dist/package.json'))

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    // pdf.js needs character maps (Japanese text), standard fonts and image decoders,
    // served locally so PDFs render offline.
    viteStaticCopy({
      targets: ['cmaps', 'standard_fonts', 'wasm'].map((dir) => ({
        src: normalizePath(path.join(pdfjsDir, dir, '*')),
        dest: `pdfjs/${dir}`,
        rename: { stripBase: true }, // flat: /pdfjs/cmaps/<file>
      })),
    }),
  ],
  build: {
    // pdf.js (~630 kB) is its own lazily loaded chunk: it downloads only when a PDF opens.
    chunkSizeWarningLimit: 700,
  },
  server: {
    port: 5173,
    proxy: {
      // Forward API calls to the FastAPI backend during development
      '/api': 'http://localhost:8000',
    },
  },
})
