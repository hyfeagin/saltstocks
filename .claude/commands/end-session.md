# /end-session

Wrap up the current development session cleanly.

## Steps to perform

1. **Read `.session/SCRATCH.md`** — collect all worklog entries added this session.

2. **Update `BOOT.md` Session Log** — prepend new entries (newest first) to the Session Log table. Keep the table to 20 rows maximum; drop the oldest entry if it would exceed that.

3. **Update `BOOT.md` File Map** if any new files were created or line counts changed significantly.

4. **Reset `.session/SCRATCH.md`** — replace the content with just the header comment block (no worklog entries):
   ```
   # Session Scratchpad — VOLATILE
   # This file is auto-read at session start and reset by /end-session.
   # Do NOT commit. Append one line per completed task during a session.
   # Format: [YYYY-MM-DD] what was done
   ```

5. **Propose a git commit message** summarizing the session's work. Use the format:
   ```
   feat/fix/chore/docs: short summary

   - bullet 1
   - bullet 2
   ```
   Then ask: "Ready to commit and push with `bash scripts/done.sh`?"

6. **Do not push automatically** — always ask first.
