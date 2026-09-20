import type { NextConfig } from "next";

// Local-only viewer: the dev server binds 127.0.0.1 (see package.json). Both spellings of
// loopback are allowed so opening it either way does not trip the dev-origin check.
const config: NextConfig = {
  allowedDevOrigins: ["localhost", "127.0.0.1"],
  // Next writes AGENTS.md / CLAUDE.md into the app by default; committing generated agent
  // instructions is a decision for the repo owner, not a side effect of `npm run dev`.
  agentRules: false,
};
export default config;
