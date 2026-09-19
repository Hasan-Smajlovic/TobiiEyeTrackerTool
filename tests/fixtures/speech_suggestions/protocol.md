# Speech prediction evaluation protocol

These synthetic Bosnian Latin messages were authored and language-reviewed during
implementation, before model preparation and ranking experiments. They are not
transcripts or examples copied from the intended user's conversations. The review
checks spelling, ordinary phrasing, inflections, and coverage of five conversation
types. Independent review by a Bosnian-speaking Windows tester remains useful.

The 40 development messages may guide configuration choices. The 100 held-out
messages must not be added to the seed, used to choose weights, or used to select
a candidate model. Freeze the file hashes before the first model comparison.
Report the first held-out result even if it misses the target.

Compare the existing grouped keyboard, unigram-only prediction, and the frozen
contextual model. Use the current default five letters per group. Selecting a
letter requires opening its group and selecting it (two activations); DŽ, LJ,
and NJ are individual keyboard letters. Spaces cost one activation. Opening and
closing the symbol page each cost one activation, and each symbol costs one.
All messages use punctuation available in the existing symbol page. Uppercase
output is the keyboard's normal behaviour and is not a spelling error.

The deterministic suggestion policy checks the five candidates before each word
and after each successive letter. Select the intended exact word as soon as it
is offered and doing so saves activations compared with completing it manually.
Count the inserted space and its removal before punctuation under the actual
editing rules. No unsolicited space is required at message end. Count any final
space deletion if needed to reproduce the exact target. Report letters entered,
button activations, selections, and top-five availability separately. Exact-word
availability does not treat a different inflection as a hit.

All three paths type the same punctuation. The policy makes no intentional wrong
selections, so its correction and undo counts are zero, explicitly reported as
an ideal-selection assumption. Separate scripted behaviour tests exercise wrong
selection, undo, deletion, and correction. Human selection/search costs and real
communication speed are outside this simulation. Report the number of consecutive
selections at the same suggestion position that require gaze departure and return;
these movements are not button activations.

Use an empty personal profile for every initial-model message. Evaluate learning
in separate scenarios, never by replaying the held-out set into personal counts.
Report aggregate and per-category activation reductions against the grouped
keyboard. The acceptance target is at least 20% aggregate reduction. Latency on
Mac is development evidence only; reference-device acceptance is owned by the
[feature specification](../../../docs/SPEECH_SUGGESTIONS.md).
