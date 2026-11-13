---
name: reflect
description: End-of-session review that turns what went wrong into small, approved improvements. Use at the end of every working session, or when the user says "reflect", "wrap up", "what did we learn", or asks to improve the skills or CLAUDE.md based on this session.
---

# Reflect

Skills and CLAUDE.md should change only because of things that happened.

1. Run `python3 .claude/omega/reflect.py`. It lists briefs that failed their check, how often, on which command, and slow checks. Add anything else you saw go wrong or take too long this session. If there is nothing, say so and stop.

2. For each item, propose the smallest edit that would have prevented it. Name the file (a SKILL.md, an agent file, or CLAUDE.md) and give the exact text. Prefer one changed sentence to a new section.

3. Show the proposals and wait for the user's answer:

   ```
   1. What happened: <evidence>
      Edit: <file> - <exact change>
      Why it helps: <one line>
   ```

   Apply only what the user approves.

4. Record and close:
   - gotcha: `python3 .claude/omega/log.py issue "name" --what "..." --fix "..."`
   - settled choice: `python3 .claude/omega/log.py decision "..." --why "..."`
   - then `python3 .claude/omega/reflect.py --mark`

Rules of thumb: one incident earns a known-issues note, a repeated one earns a rule. If an existing rule was ignored, make it clearer instead of adding a second. CLAUDE.md is loaded every session, so when it grows, propose a removal alongside the addition.
