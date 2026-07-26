import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  // Use the automatic JSX runtime (matches Next.js) so component tests don't
  // need React in scope.
  esbuild: {
    jsx: "automatic",
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // jest-dom's ESM build uses extensionless lodash imports that Node's ESM
    // resolver rejects — let Vitest's module runner process it instead.
    server: {
      deps: {
        inline: ["@testing-library/jest-dom"],
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
