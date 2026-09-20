# Jev Gateway adapter

`jev_plays_emerald.jev.JevGateway` sends one structured Choice question to
Vercel AI Gateway and returns a validated, immutable `JevChoice`. Set
`AI_GATEWAY_API_KEY` in the process environment before constructing it.

```python
from jev_plays_emerald.jev import JevGateway

decision = await JevGateway(timeout_seconds=30).choose(
    state=observation,
    options={action.id: action.label for action in legal_actions},
    instructions="Choose the next legal action.",
)
```

`choose` accepts JSON-compatible state, an action-ID-to-description mapping,
JSON-compatible instructions, and optional provider options. It returns
`choice`, a read-only `probabilities` mapping, optional `confidence`, frozen
`usage.input_tokens`/`usage.output_tokens`, and `latency_ms`. A deadline raises
`JevTimeoutError`; other HTTP or transport failures raise `JevGatewayError`
with an optional safe-to-log `status_code`; malformed successful responses
raise `ValueError`.

For credential safety, a key loaded from `AI_GATEWAY_API_KEY` can only be sent
to the canonical Vercel Gateway base URL. Tests may use a custom local base URL
only when they pass an explicit nonempty `api_key`.

The consumer must compare its decision context before executing
`decision.choice`. The adapter deliberately has no retry loop; the mode owns
retry and pause policy so one failure cannot be retried independently at two
layers. Singleton actions should bypass this adapter and be recorded as
deterministic.

The response is accepted only when the selected ID is legal and probabilities
cover every legal ID, are finite and nonnegative, and total `1 ± 0.01`.
Returned values are preserved rather than normalized. TypeSafe confidence, if
present, must be finite and between zero and one. `JevChoice` also contains
request latency and optional input/output token usage.

## Protocol status

The evaluation route is experimental and source-visible rather than a
documented public REST contract. This adapter pins the wire shape observed in
Vercel AI SDK commit
[`20dd00a`](https://github.com/vercel/ai/tree/20dd00abba618d5a516e0fee40ccd3e18a2bd1fb):

- `POST https://ai-gateway.vercel.sh/v4/ai/evaluation-model`
- model `typesafe-ai/jev`, evaluation specification `4`, and Gateway protocol
  `0.0.1`
- request `{state, questions, providerOptions?}`, with Choice options in the
  question's `criteria` map
- response Choice at `answers.action`, usage at `usage`, and optional
  confidence at `providerMetadata.typesafe.confidence.action`

The request header and body construction follows the pinned SDK's
[`gateway-evaluation-model.ts`](https://github.com/vercel/ai/blob/20dd00abba618d5a516e0fee40ccd3e18a2bd1fb/packages/gateway/src/gateway-evaluation-model.ts),
[`gateway-provider.ts`](https://github.com/vercel/ai/blob/20dd00abba618d5a516e0fee40ccd3e18a2bd1fb/packages/gateway/src/gateway-provider.ts),
and the provider's
[`evaluation-model-v4-question.ts`](https://github.com/vercel/ai/blob/20dd00abba618d5a516e0fee40ccd3e18a2bd1fb/packages/provider/src/evaluation-model/v4/evaluation-model-v4-question.ts).

On 2026-09-20, an API-only smoke request returned HTTP 200 with a complete
three-option starter distribution, confidence, and token usage. This verifies
the credential and protocol path; it was not a choice made against live game
state. The python.org Python 3.13 installation on the test Mac had no CA file
at its configured OpenSSL paths, so the adapter uses the explicitly pinned
certifi bundle while keeping TLS verification enabled.
