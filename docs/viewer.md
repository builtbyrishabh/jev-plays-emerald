# Local viewer

The viewer is part of the existing PokéBot process. Run the normal bootstrap and launcher, then open:

<http://127.0.0.1:8888/jev/index.html>

The plugin serves `web/` through PokéBot's existing aiohttp application, so frontend edits are available after a refresh. The bootstrap links the plugin entrypoint instead of copying it. It accepts an existing link to the repository source, migrates a byte-for-byte matching old copy, and refuses unrelated files or links.

The page embeds the upstream `/stream_video?fps=15` feed and polls `/custom_state` four times per second. The game remains the largest element. The decision panel leads with the mission, current action, AI decision count for the current mode run, available option count, and planner calls. Jev's preference distribution is ranked with the selected action marked explicitly; percentages are preferences, not success probabilities. A scrollable timeline shows the latest 50 choices by readable action name. Battle HP and opening progress are expandable, and remembered dialogue is omitted from the presentation. The AI count excludes deterministic actions and retries and resets with a new mode run. Deterministic choices say that probabilities are unavailable instead of inventing a distribution.

Pending, error, paused, disconnected, and loaded-checkpoint states have distinct labels. A rival flag already present in a loaded save is labeled as a checkpoint; only the mode's `completed` proof marks the current run complete.

Pause and resume send `{ "paused": true | false }` to `POST /jev/control`. The HTTP thread validates that payload and puts a callback on PokéBot's existing `work_queue`. The callback resolves the active `JevEmeraldMode` and calls `set_paused` on the owner thread. The HTTP handler never reads emulator memory or runs an action. Pausing invalidates any pending decision in the mode; resuming causes the frame loop to take a fresh observation.

The plugin's frame listener publishes a new namespaced snapshot at `jev_emerald` from immutable mode properties. The snapshot is an explicit allowlist: it contains viewer status, observations needed by the panel, legal actions, the active action, progress, and recent choices. It does not serialize the mode object, callbacks, request state, API configuration, or credentials.

When a mode supplies planner advice, a separate hint card shows the planner’s guidance, model, destination, action to avoid, and success signal. Luna is named when the configured model is Luna. While planning,
inputs remain neutral. Jev's subsequent distribution stays in the action panel;
the planner neither presses buttons nor supplies those probabilities.

Run the focused checks with:

```sh
.venv/bin/pytest tests/test_web_state.py -q
```

The top-level multipart stream may be blocked when opened directly by Chrome. The viewer embeds it as an image, which is the supported path verified for this project.
