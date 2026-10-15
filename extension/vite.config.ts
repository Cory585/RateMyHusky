import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Chrome loads files by name, so no hashed filenames. Everything in public/
// (manifest.json, icons) is copied into dist as-is.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: '[name].js',
        chunkFileNames: '[name].js',
        assetFileNames: '[name].[ext]',
      },
    },
  },
});
