# The TypeScript decision service

`service/` holds the part of the decision layer that talks to a model: the Jev
client and the optional LLM planner. Everything else stays Python - the emulator
loop, the observation reader, the option enumerator, the executors, and
staleness validation.

```text
Python                           |  TypeScript
---------------------------------+---------------------------------
L0  frame loop                   |
L1  Observation (RAM -> JSON)    |
L2  option enumerator            |
                                 |  L3  planner (LLM, opt-in)
                                 |  L4  Jev client          (done)
L5  executors (frame generators) |
    staleness + dispatch         |
```

## Running it

```bash
cd service && pnpm install
JEV_DECISION_SERVICE=1 uv run --env-file .env python -m jev_plays_emerald --rom ...
```

Setting `JEV_PLANNER_MODEL=openai/gpt-5.6-luna` also enables the service and its
`plan` request. Without either setting the direct Python client (`jev.py`) is used, so both
implementations stay runnable while they are compared. The launcher starts the
service once and throws it away before the emulator boots, so a missing `node`
or an unbuilt `service/` fails at launch rather than at the first decision.

Jev's choice instructions contain a high-level mission and a general method for
using recent in-game dialogue and observed state. They contain no authored route.
Leave `JEV_PLANNER_MODEL` unset for a Jev-only run.

## Transport

Newline-delimited JSON on the child's stdin and stdout, one line per request,
matched by `id`. `DecisionService` in `service.py` owns the child's lifetime.
stdout carries only protocol lines; anything human-readable goes to stderr.

The child inherits `AI_GATEWAY_API_KEY` from the environment. The key is never
an argument, never logged, and appears in neither language's source.

The model worker sends one request at a time and reads the matching response
under a lock, with a bounded deadline. Menu enumeration stays in-process on
the emulator owner thread. Neither planning nor choosing blocks that thread;
while a request is pending, inputs stay neutral and the viewer stays responsive.

## The planner

`service/src/planner.ts` uses either bounded headless Codex calls (backend `codex`)
or `generateText` and the configured Gateway model (backend `gateway`) to
return a bounded JSON recovery plan. The destination must be one of the current
action IDs; Python validates it again after the response crosses the process
boundary. The other fields are a concise hint, exact location, an action to avoid,
and a visible success signal.

The Codex child receives a minimal environment without API keys, uses saved
ChatGPT authentication, and has no game controls or coding tools. Its usage
includes CLI prompt overhead. [Configuration and verification](codex-planner.md).

Python owns a 24-attempt memory and triggers planning after three repetitions or
eight decisions without story progress. Useful map travel, party improvement,
healing and inventory changes reset the general counter. Advice expires after Jev
completes its one immediate action; another call requires a new stall. A local
atomic JSON ledger retains verified lessons and map-scoped dead ends for inspection;
they are not replayed into model prompts. Python checks pause, context and story
staleness before accepting advice; planner failures pause visibly. Planner mode
keeps every legal alternative instead of applying the old repetition filter.
[Behavior, launch command and limitations](planner-proposal.md).

## The Jev client

`service/src/jev.ts` uses the AI SDK's evaluation-model API rather than the
hand-pinned wire format `jev.py` carries:

```ts
const { answers, usage, providerMetadata } = await evaluate({
  model: gateway.evaluationModel('typesafe-ai/jev'),
  state,
  questions: { action: { type: 'choice', instructions, criteria: options } },
  maxRetries: 0,
  abortSignal: AbortSignal.timeout(timeoutMs),
})
```

`evaluate` is exported as `experimental_evaluate`; the API can still change
shape. It validates that the returned choice is one of the offered criteria, so
that check no longer lives in this project. `maxRetries: 0` is deliberate: the
mode owns retry and pause policy, and a failure retried at two layers is a
failure charged twice.

`service.py` runs every accepted answer through the same `validate_choice` the
direct client uses, so switching implementations cannot quietly widen what
counts as a valid distribution.

## Where the boundary stops, and why

The model calls cross the boundary. Nothing else does.

The tempting next move is to send the option enumerator over too - it is pure
logic over Observation fields, so it *can* go. It should not. `legal_actions`
runs on the emulator owner thread on every frame, so moving it means asking
another process for the menu 60 times a second, inside the 16 ms the emulator is
frozen for. That was built and measured: it works, at the cost of a memo, a
frame-thread deadline, a reader thread, a pause-on-failure path in the hot loop,
a hand-written mirror of the Observation fields in TypeScript, and the
cross-language action contract that exists to keep the two in agreement. Around
1,100 lines of boundary for 330 lines of logic, and no capability gained.

What TypeScript is actually for here is the AI SDK: `gateway.evaluationModel()`,
and a planner that has to be compared across `openai/gpt-6-astra`,
`gpt-5.6-sol`, Anthropic and Google on one key. That argument applies to the
layers that speak to a provider (L3, L4) and to nothing else.

So the enumerator stays in `opening.py`, in-process, where it is also cheapest
to change - it is the layer that churns as new towns are added. This reverses
step 4 of issue #3, which was written before the per-frame cost was known.

## The action contract## The action contract

`schema/actions.json` is the source of truth for the action vocabulary. An ID
the service can offer must resolve to a Python executor, and nothing else
enforces that across the two languages:

- `tests/test_action_contract.py` asserts `ACTION_EXECUTORS` and the schema name
  exactly the same kinds.
- `service/src/actions.ts` refuses to send a menu containing a kind the schema
  does not declare, before the request reaches the gateway.

Adding an action kind means editing the schema, the TypeScript enumerator, and
the Python executor together; the contract test fails until all three agree.

## Checks

```bash
cd service && pnpm check && pnpm test     # typecheck and unit tests, no network
DYLD_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest tests -q
```

The Python suite drives a stand-in child over the real pipes for framing,
deadlines and error mapping, and starts the real service once to confirm `node`,
the schema import and the framing agree. Neither suite calls the gateway.

## Not done yet

The recorded-request diff between the two clients (issue #3, step 2) needs live
gateway calls and has not been run. Until it has, the TypeScript client is
proven to frame, validate and fail correctly, not to return what `jev.py`
returns on the same input.
