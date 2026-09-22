# Luna through Codex

Jev still chooses the actions. Luna supplies occasional route advice through the local Codex CLI when the existing planner trigger fires. Each advice request starts an ephemeral Codex session; it does not retain a growing conversation.

Install a recent `codex` CLI, sign in with `codex login`, and check `codex login status`. The status must say `Logged in using ChatGPT`, and the account must have access to `gpt-5.6-luna`. Launch the game with:

```sh
export JEV_PLANNER_BACKEND=codex
export JEV_PLANNER_MODEL=gpt-5.6-luna
```

This uses the saved ChatGPT login and its applicable quota or credits. It is not unlimited or free inference. The adapter refuses API-key authentication, excludes API-key environment variables, and forces ChatGPT authentication. Errors never silently fall back to the paid gateway. To choose gateway billing explicitly, set `JEV_PLANNER_BACKEND=gateway` and a gateway-compatible `JEV_PLANNER_MODEL`.

The adapter uses a temporary working directory, read-only sandbox, disabled shell/web/app tools, a JSON output schema, and a short game-specific instruction file. A request has a 120-second default timeout and a 1 MiB combined output bound. Temporary files are removed after the process exits. The caller should allow at least five seconds beyond the request timeout. Login is checked for each request; model access and quota failures are reported when Codex runs, rather than tested with another billable inference on startup.

## Coaching limits and evidence

For `--target first-gym`, `JEV_PLANNER_MAX_CALLS` defaults to **32** and
`JEV_PLANNER_MAX_TOKENS` to **200000** per mode instance. The opening-only
`rival` target retains the **8** call / **50000** token defaults. Both accept
nonnegative integers; zero disables new
coaching. Calls include stale and failed work. The token cutoff uses reported
input plus output, including cached input only once. Because exact provider
usage arrives after a call, one in-flight call may cross the cutoff. This is a
stop threshold, not a guaranteed hard token reservation. Missing input/output
usage also prevents new coaching, even after Resume.

When a limit is reached, Jev keeps choosing actions and retains applicable
existing advice. No new Luna call starts; the viewer explains why. An initial
provider error still pauses visibly as before. Budgets and live counters reset
with a new mode instance; the JSONL preserves the prior instance's evidence.

Each intervention records its call ID and trigger, returned hint, whether its
suggested action actually started, the action outcome, and changes to trusted
story flags/state afterward. Stale or invalid hints cannot be recorded as
followed. Progress is an observation, not proof that the hint caused it.
`planner-intervention` events preserve every update; the viewer shows the latest
24 interventions. `planner-budget` events record budget changes. Separate live
Jev/planner counters distinguish known tokens, missing usage, failed and pending
requests; selected decisions do not count tokens a second time.

## Local transport probes

Two live requests used the same small rival-house observation and one legal action. Both returned valid advice naming that action using `gpt-5.6-luna` and the saved ChatGPT login.

| Configuration | Input tokens | Output tokens | Cached input | Elapsed |
| --- | ---: | ---: | ---: | ---: |
| Initial generic Codex instructions | 12,248 | 117 | 0 | 10.71 s |
| Short game instructions and minimal skill catalog | 3,715 | 75 | 0 | 6.08 s |

The second configuration is implemented. Input fell by 69.7% in this pair of probes. These are transport checks, not gameplay benchmarks: they do not establish a completion rate, reliable latency improvement, or Jev-versus-Luna token savings. Token counts include Codex prompt overhead. Actual gameplay comparisons must count every call, compare the same checkpoint and objective, and report unfinished runs honestly.

`service/src/baseline.ts` supports a Luna-only benchmark through the same transport: send one choice request JSON on stdin and receive `{choice, usage, latencyMs}` on stdout. `JEV_BASELINE_MODEL` defaults to `gpt-5.6-luna`. It uses the offered legal action IDs and does not manufacture probabilities.

## References

- [Non-interactive Codex](https://learn.chatgpt.com/docs/non-interactive-mode): saved CLI authentication, JSON events, and scripted execution.
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference): `forced_login_method`, `model_instructions_file`, tool settings, and skill context limits.

The installed CLI's `codex exec --help` and `codex features list` were also checked; the live probes verified the selected settings on this installation.
