# Building Courses with Your Own Claude (via MCP)

A guide for course authors on the Red Arch Knowledge Manager. Your host will
give you the app's URL — written as `https://<km2-host>` below.

You'll connect **your own Claude** (Claude Code or Claude Desktop) to the
platform through its MCP server. After that, Claude can create and edit
courses, slide decks, quizzes, and graded scenarios for you conversationally —
using your account, with your permissions, against the live platform.

---

## Part 1 — Connect Claude to the platform

**How auth works:** you sign in once through the app's normal login in a Chrome
window the connector opens; after that it rides your live session. No API keys,
no stored passwords — Claude acts strictly as you.

### Claude Desktop — install the bundle (easiest, no installs needed)

You only need **Google Chrome** and **Claude Desktop**.

1. Save the `km2-mcp.mcpb` file Jeremy sends you.
2. In Claude Desktop: **Settings → Extensions**, then drag the file in (or
   double-click it). Approve when prompted.
3. That's it — the connector runs inside Claude Desktop's own runtime. All
   settings (which server it talks to, how it signs in) are baked into the
   bundle.

### Claude Code (terminal) — alternative for developers

Needs **Node.js 20+** and the `km2-mcp` source zip. One-time:

```bash
cd ~/km2-mcp && npm install && npm run build

claude mcp add --scope user km2 \
  -e KM2_APP_URL=https://<km2-host> \
  -e KM2_API_URL=https://<km2-host>/api \
  -e KM2_CLERK_JWT_TEMPLATE=redarch-km \
  -e KM2_BROWSER_CHANNEL=chrome \
  -- node ~/km2-mcp/dist/index.js
```

### Sign in and verify

In a new Claude conversation:

1. Say **"Log into KM2."** — Claude runs the connector's login tool; a Chrome
   window opens on the app's sign-in page. Sign in with **Continue with
   Google** using your invited email. You can close the window afterward; the
   session persists, so you'll rarely do this again.
2. Say **"Check my KM2 status."** — Claude should report your email and the
   active organization.
3. Say **"List my KM2 orgs and switch to Corporate Training."** — that org has
   the full course machinery (see below); it's where course authoring works out
   of the box.

If all three work, you're connected. (This exact sequence — connect, switch
org, create content, run a workflow, render a page — has been tested end to
end.)

## Part 2 — What a "course" is here

Every course has the same gated structure, in this order:

1. **Modules** — in-app slide decks the learner works through (each module can
   also link to a source document for deeper reading).
2. **Knowledge check** — a multiple-choice quiz, graded **server-side** (answer
   keys never reach the browser, so they can't be scraped).
3. **Scenario assessment** — the learner writes a free-text response to a
   realistic situation; an LLM grades it **against your rubric**. Passing the
   scenario (with a passed quiz) issues a certificate and completes the course.

Learners find courses in the **Course Catalog**, enroll themselves, and track
progress in **My Training**. Under the hood each piece is a record — Course,
Module, Assessment, Question, Scenario — which is exactly what your Claude
creates and edits through the connector.

## Part 3 — Author a course with Claude

Work inside the **Corporate Training** org (it has the course entities, the
player pages, and the grading workflows already wired). Some example asks:

**Explore what exists:**

> List the courses in this org, and show me the modules and quiz questions of
> the Security Awareness course.

**Create a course** (be as specific as you'd be in a design brief):

> Create a new course: "Effective 1:1 Meetings", category `role`, for
> first-time managers, about 40 minutes. Write 3 modules with full slide decks
> (5–7 slides each, markdown bodies). Add an assessment with a 75% threshold
> and 4 multiple-choice questions with plausible distractors and per-question
> explanations. Add a roleplay scenario where the learner plays the manager in
> a difficult feedback conversation, with a grading rubric and pass threshold
> of 75. Model it on how the existing Security Awareness course is structured.

Telling Claude to **model it on an existing course** matters: it will read the
existing records and views, mirror their structure (including the per-course
quiz and scenario pages, which it can clone and repoint), and set the course's
`quiz_view_slug` / `scenario_view_slug` so the course page links work.

**Iterate with your instructional-design judgment:**

> Rewrite module 2's slides to be scenario-driven rather than bullet lists.

> Question 3's distractors are too easy — make them plausible misconceptions.

> Tighten the scenario rubric: observable behaviors only, and distinguish a
> pass from a near-miss.

Where your expertise counts most, in priority order:

1. **The scenario rubric** — the LLM grades learner responses against it
   verbatim. Write it like grading criteria for a human rater. Vague rubric →
   vague grading.
2. **Quiz items** — distractor quality, and explanations that teach on a miss.
3. **Slide decks** — markdown bodies; can embed video with a watch-gate.
4. **Thresholds** — per-course quiz/scenario passing scores.

## Part 4 — Ground it in real source material (recommended)

Upload reference material (policies, playbooks, manuals) under **Resources**
in the web app — binary/PDF upload is the one thing the connector doesn't do.
Once ingested, the **AI Tutor** inside each course answers learner questions
with citations into your documents, and each module can carry a "Read →" link
to its source. A course without source documents has an ungrounded tutor.

Claude *can* create text/markdown documents directly ("create a document titled
… with this content") — useful for authoring reference material from scratch.

## Part 5 — Test it as a learner

1. **Views → Course Catalog** in the web app: your course appears once its
   status is `published`. Click **Enroll**.
2. Open the course, work through a slide deck.
3. Take the quiz — immediate score; retries allowed.
4. Do the scenario — try a deliberately weak answer first and read the
   rubric-driven feedback, then a strong one. Passing both issues your
   certificate.
5. Check **My Training** for progress and certificates; org-wide dashboards
   are under **Reports**.

## Known rough edges (being worked on)

- Right after self-enrolling, the enrollment-bound "Course Player" page may
  show an **empty module list** (per-module progress rows aren't auto-created
  yet). The course page reached from the **catalog** always works — use that
  path, or ask Claude to "seed module progress rows for my enrollment".
- If a login window gets stuck or Chrome complains about the profile, delete
  the `~/.km2-mcp/profile` folder and say "log into KM2" again.

## Troubleshooting the connection

| Symptom | Fix |
|---|---|
| "Not signed in" | Say "log into KM2" and finish the browser sign-in. |
| "No active organization" | "List my KM2 orgs", then "switch to …". |
| Login window times out | Finish signing in, then ask again — it detects the session immediately. |
| 403 on some action | Wrong active org, or the action needs org-admin there. |
| Browser won't launch | Ensure Chrome is installed; delete `~/.km2-mcp/profile` and retry. |

## Feedback we'd especially value from you

- Does authoring through Claude land at a useful starting altitude, or does the
  draft need too much rework to be worth it?
- Is the module → quiz → scenario gating right for real corporate training, or
  do you need other assessment shapes (observation checklists, manager
  sign-off, spaced repetition)?
- Is rubric-driven LLM grading credible enough to certify on? What would it
  take for you to trust it?
- What's missing from this loop that your current authoring tools have?

Send feedback (screenshots welcome) to Jeremy.
