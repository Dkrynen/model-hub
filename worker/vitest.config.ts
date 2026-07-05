import { defineConfig } from "vitest/config";

// Plain Node-environment vitest. The gate is tested with a thin handler
// harness: the Worker's exported `fetch` is called directly with a mocked
// global `fetch` (Polar) and a faked R2 binding — real Request/Response/
// ReadableStream from the Node runtime, no `workerd` needed.
//
// (The brief preferred @cloudflare/vitest-pool-workers; its current npm
// release ships a broken `./config` export under vitest 4, so we use the
// blessed thin-harness fallback. See README.md → Testing.)
export default defineConfig({
  test: {
    environment: "node",
    include: ["test/**/*.test.ts"],
  },
});
