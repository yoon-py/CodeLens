import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // dev mode: proxy ontology.json to a running `lensme serve` (port 4173)
    proxy: { '/ontology.json': 'http://127.0.0.1:4173' },
  },
})
