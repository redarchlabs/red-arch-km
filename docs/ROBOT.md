# Robot integration (Ollie)

KM2 drives a physical [Reachy Mini](https://github.com/redarchlabs/reachy-the-robot) — "Ollie"
— that greets visitors in the lobby of the Christa McAuliffe Space Center. This page exists so
that nobody has to rediscover where the other half lives.

> **The robot code and the presentation scripts are NOT in this repo.** They are in
> **[redarchlabs/reachy-the-robot](https://github.com/redarchlabs/reachy-the-robot)**
> (locally, `~/github/reachy-virtual-robot`).

## Where things live

| What | Where |
|---|---|
| Robot service, pose vocabulary, TTS, screen clients | robot repo, `app/` |
| **The three Space Center presentations + the generator that writes them** | robot repo, **`scripts/presentations/`** (has its own README) |
| The robot ↔ KM2 contract, endpoint by endpoint | robot repo, `KM2_INTEGRATION.md` |
| Buttons, dashboards, and the workflows that call the robot | **this repo** — as org data, not as code |

The split is worth stating plainly: **KM2 holds no robot code.** What KM2 holds is
configuration — an outbound connection, a set of workflows, and the views with the buttons on
them — all of it org data in the database rather than files here. That is why the robot side
has a directory of scripts whose job is to *write* KM2 objects over the REST API.

## The KM2 side

Everything lives in the **Robots (OpenAI)** org.

- **Connection** `robot` — outbound HTTPS to the Pi. Authenticated with the
  `X-KM2-Command-Key` header, which must match `KM2_COMMAND_SECRET` in the robot's `.env`;
  a mismatch shows up as a 401 from the robot, not from KM2. See
  [MCP_AND_INTEGRATIONS.md](MCP_AND_INTEGRATIONS.md) for outbound connections generally.
- **Views** — *Robot Control* (the operator console, organised into tabs), *Presentations*,
  and the org *Home*.
- **Workflows** — three `Presentation: …` workflows, plus `Robot: Stop Presentation` and
  `Robot: Pre-render Presentations`, plus the mannerism and simulation workflows.
- Inbound events from the robot arrive as signed webhooks (`X-KM2-Signature`) — see
  [WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md).

A presentation workflow is three nodes: torque the arm (`POST /arm/live`), then one
`POST /perform` carrying the whole timeline, with `timeout_seconds` raised because
`/perform` renders every line to speech *before* it answers — roughly 12 s for a two-minute
script. The default 10 s outbound timeout is not enough, and the failure is deceptive: the
step fails with an empty error while the robot performs the whole thing perfectly.

## The one rule that crosses the boundary

**The robot must have a pose before a KM2 script may cue it.**

A `/perform` timeline cues motion by *name* (`[arm right explain]`), and the name is resolved
on the Pi against `app/limbs.py:ARM_POSES`. Publishing a KM2 workflow that names a pose the
robot does not have makes every cue 400 — and the client's refusal path re-arms with a ~1.5 s
bus scan, so the motion lane spends the presentation scanning instead of moving.

Check first, deploy the robot, *then* republish from KM2:

```bash
curl -sk https://<pi>:8080/capabilities | python3 -c 'import json,sys; print(json.load(sys.stdin)["arm_poses"])'
```

The robot repo's `tests/test_presentation_scripts.py` enforces the local half of this.

## Changing a presentation

Edit the prose in the robot repo (`scripts/presentations/scripts.py`) and re-run
`republish.py` and `warmwf.py` from there — **not** by hand-editing the workflow in the KM2
console. The timelines are generated, and the generator enforces a constraint that is easy to
break by hand: `/perform` renders each spoken step as its own TTS utterance, so every pause
and every cue splits the line and resets prosody. Motion therefore lives in a parallel lane
that never interrupts speech. Full explanation in that directory's README.
