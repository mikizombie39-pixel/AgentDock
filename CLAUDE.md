# CLAUDE.md

## Coding guidelines
Adapted from andrej-karpathy-skills (MIT, https://github.com/forrestchang/andrej-karpathy-skills): principles 3 and 4 only.

### Surgical changes
- Touch only what the task needs. Do not "improve" adjacent code, comments, or formatting.
- Do not refactor what is not broken. Match the existing style.
- If you notice unrelated dead code, mention it; do not delete it.
- Remove only the imports, variables, and functions that your own change made unused.
- Test: every changed line traces directly to the request.

### Goal-driven execution
- Turn each task into a verifiable goal before writing code
  (e.g. "fix the bug" → "write a test that reproduces it, then make it pass").
- For multi-step work, state a short plan: step → verify: check.
- Loop until the check passes; report what was verified and what was not.
