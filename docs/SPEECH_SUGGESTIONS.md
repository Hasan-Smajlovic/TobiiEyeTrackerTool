# Bosnian speech suggestions

Status: Mac implementation and all available software verification are complete
for issue #4. Windows packaging, target-display, performance, and real Tobii
verification are still required. This document owns the requirements,
implementation record, and acceptance plan for word suggestions in the Speech
screen's `Brzi izbor` area.

The goal is to reduce the effort needed to compose Bosnian messages through
gaze. Support ordinary conversation, humour, memories, opinions, and everyday
needs. Useful suggestions must help the person express their own message.

This work is independent of #6, #7, #8, and #11 and does not block them. The fixed
words in the [application UI reference](design/speech-keyboard-reference.html)
demonstrate layout and interaction; they are not a language dataset.

## Documentation ownership

This document describes the planned feature, its decisions, and its acceptance
criteria. Existing project guidance remains in its owning documents:

- [Architecture](ARCHITECTURE.md) owns runtime responsibilities and the
  compatibility contract. Update it when implementation introduces components or
  persistent data.
- [Development](DEVELOPMENT.md#application-design-reference-workflow) owns UI
  reference review and verification. The HTML reference owns visible layout and
  interaction examples, including the eventual undo control.
- [Windows release guide](WINDOWS_RELEASE.md) owns installation, distribution,
  update, and rollback procedures.
- [User guide](USER_GUIDE.md#speech) describes shipped behaviour. Add user
  instructions there when the feature is available.

## Agreed scope

| Area | Requirement |
| --- | --- |
| Language | Bosnian words in Latin script, including Bosnian letters and inflected forms. |
| Letter case | Display and enter the conversation message, suggestions, categories, answers, and phrases in uppercase. Normalize lowercase or mixed-case input immediately and match prefixes without case sensitivity. |
| Completion | Offer completions of a word already being typed. |
| Prediction | Offer the next word using the preceding text. |
| Availability | Work offline from the first launch with bundled language data. No initial download or paid inference service is required. |
| Personalisation | Learn from conversation suggestion selections, messages submitted through `Izgovori`, and successfully saved phrase or answer text. Keep learned data locally. |
| Interaction | Use the existing five suggestion positions, with equivalent text results for mouse and gaze selection. |
| Supported inputs | Offer suggestions in conversation messages and saved phrase or answer editors. Disable them when editing category names. |
| Editing position | Offer suggestions only at the end of the active input, with no text selected. |
| User control | Preserve the message, support one-step suggestion undo without a time limit, and speak only through `Izgovori`. |
| Personal data control | Provide individual learned-word removal in settings, accessible through gaze and mouse. |

Bundle prepared language data in the installation. Deliver improved versions
through the existing application release and update flow, preserving personal
learning separately and retaining a working version if an update fails. A
separate online language-data downloader is deferred beyond the first version.
Prediction must not send the user's text or learned counts to an online service.

This network constraint concerns prediction. Existing offline and online speech
voices retain the behaviour described in the [user guide](USER_GUIDE.md#speech).

Cloud prediction, LLM integration, automatic sentence rewriting, and full
conversation archives are outside the initial scope.

## Suggestion behaviour

### Supported inputs

Offer completion and next-word suggestions when composing a conversation message
or adding or editing a saved phrase or answer. Use the same end-of-input,
selection, and gaze reactivation rules in each supported input. Keep suggestion
slots inactive while editing a category name; category labels must not become
learning input.

Integrate with the phrase and answer entry flows available at the implementation
baseline. Editing includes changing a draft before saving it; this feature does
not require adding a separate flow for modifying existing library records or
redesigning library management.

In a phrase or answer editor, derive suggestions from that editor's text and
apply selections only to that text. Preserve the saved conversation message
through entry, editing, save, and cancel, following the existing restoration
behaviour. Invalidate pending suggestions and dwell when switching inputs so
results or selections from one input cannot apply to another. Editors contribute
to learning only after successful save, as defined in [Personal learning](#personal-learning).

### Editing position

The first version completes the last word or appends a next word at the end of
the active input, matching the existing gaze keyboard's append and delete
behaviour. If the caret moves away from the end or any text is selected, disable
all suggestion slots and cancel any pending suggestion selection. Do not move the
caret, replace selected text, or change the message to make a suggestion apply.

When the caret returns to the end and no text is selected, show suggestions for
the current text again, subject to the existing gaze reactivation rules. A result
computed before a caret or selection change must not bypass these conditions.
Completion within the message and replacement of selected text are outside the
first version's scope. The same restriction applies in phrase and answer editors.

### Refresh and empty results

- Refresh after a letter, space, punctuation, deletion, suggestion selection, or
  undo changes the text. An unfinished word requests completion; a completed word
  followed by a space requests the next word.
- After `.`, `?`, or `!`, offer a new sentence's starting words, even before a
  separating space is typed. A comma retains the current sentence's context.
- An empty message offers useful starting words, eventually ranked by how often
  the person starts messages with them. `SELAM`, `JA`, `KAKO`, `MOŽE`, and `HVALA`
  illustrate the behaviour; the exact initial list still needs language review.
- Show up to five distinct suggestions. Keep slot positions and layout stable.
  When fewer candidates exist, leave unused slots inactive. When none match the
  prefix, keep all slots inactive and allow normal typing to continue.
- Do not fill missing completions with unrelated words or rotate suggestions
  while the text is unchanged. Apply new learned rankings on the next refresh.

### Selection and undo

The examples below use the agreed end-of-message behaviour. `␠` denotes one
trailing space for documentation only; the application inserts an ordinary space.

| Message before selection | Selected word | Message afterwards |
| --- | --- | --- |
| `ŽELIM VO` | `VODU` | `ŽELIM VODU␠` |
| `ŽELIM␠` | `RAZGOVARATI` | `ŽELIM RAZGOVARATI␠` |
| Empty | `SELAM` | `SELAM␠` |
| `Želim vo` | `VODU` | `Želim VODU␠` |

Complete only the started word, or append the selected next word, then leave one
space ready for continued typing. Preserve the rest of the conversation message.
Selection refreshes suggestions but does not trigger speech or clear the message.

Undoing the last suggestion must take one gaze selection and restore the exact
previous text. For example, undoing `ŽELIM VOZITI␠` selected from `ŽELIM VO`
restores `ŽELIM VO`. An undone selection must not remain a positive learning
signal. Placement and wording of this control belong in the HTML design review.

Suggestion undo has no time limit. Waiting or looking elsewhere does not expire
it. Typing, deleting, or otherwise editing the text after a suggestion makes that
suggestion's undo unavailable, so undo cannot discard subsequently entered text.
Selecting another suggestion replaces the undo opportunity: it restores the text
immediately before that newest selection. Undo is a single step, not an editing
history; using it does not expose an older suggestion to undo.

Undo belongs to the input where the suggestion was selected and must never
restore one input's text into another. `Izgovori` leaves the text and any
still-valid suggestion undo intact. Using that undo also removes the selection's
learning contribution, including after speech submission; it cannot reverse
audio already played.

Suspend the conversation's undo and learning state while a library editor is
active, then restore them with the unchanged conversation. Each editor has its
own suggestion undo. Closing an editor after successful save or cancellation
discards only that editor's undo. A failed save keeps the editor and its current
undo state available.

### Punctuation and spacing

When the next text entry after a suggestion is `.`, `,`, `?`, or `!`, remove the
single trailing space automatically inserted by that suggestion and place the
punctuation immediately after the word. Apply this at the end of the active
supported input for both gaze and mouse or physical keyboard entry. The rule
does not add punctuation keys to the existing gaze keyboard.

When selecting a next word directly after one of these punctuation marks, insert
one space before the word if there is no separating whitespace, then leave the
usual one trailing space after the selected word. Reuse existing whitespace
instead of adding a duplicate separator. Preserve the rest of the message;
do not clean up manually entered spaces or reformat earlier text.

In the first example, the trailing `␠` was automatically inserted by the preceding
suggestion selection.

| Input before action | Action | Input afterwards |
| --- | --- | --- |
| `ŽELIM VODU␠` | Enter `.` | `ŽELIM VODU.` |
| `ŽELIM VODU.` | Select `HVALA` | `ŽELIM VODU. HVALA␠` |
| `ŽELIM VODU.␠` | Select `HVALA` | `ŽELIM VODU. HVALA␠` |

Entering punctuation is a text edit and expires the preceding suggestion undo
under the normal rule. Removing only its automatic separator must not be treated
as deleting or correcting the selected word for learning. Selecting a word after
punctuation creates its own one-step undo, restoring the exact preceding input,
including its spacing.

### Sentence context

Treat `.`, `?`, and `!` as sentence boundaries for prediction. Words before the
most recent boundary must not supply the immediate n-gram context for a new
sentence. Once the person starts that sentence, use its words and the current
prefix under the normal completion and next-word rules.

A comma separates words without resetting the sentence context. Keep preceding
words in that sentence available for prediction across the comma. Punctuation
itself is not a suggested word.

Personal preferences remain active for both sentence starters and continuations;
a sentence boundary never clears learned data or removes earlier message text.
For example, `ŽELIM VODU.` can offer starters such as `HVALA`, `JA`, or `MOŽE`,
ranked from the available base and personal data rather than a fixed list.

Apply the same boundaries when preparing the seed and learning from user text:
word n-grams must not bridge `.`, `?`, or `!`, while commas preserve the sentence
context. Use the same rules in conversation messages and phrase or answer
editors. Recompute context from the current text after edits or undo.

### Gaze stability

After a suggestion activates, continuing to look at its position must not select
the replacement word. The person must look outside that button and return before
another selection there can begin. A changed label must not count as leaving the
button. If the text changes during a pending selection, cancel that selection
rather than applying it to the new text.

Mouse and gaze must invoke the same text-editing behaviour. Integrate with the
existing selection timing and repeat protection in
[`gaze_selection.py`](../gaze_mouse/gaze_selection.py), subject to the
[gaze compatibility contract](ARCHITECTURE.md#compatibility-contract). Dynamic
suggestion labels still require dedicated verification of that integration.

### Letter case

Display and enter all speech content in uppercase, including the conversation
message, suggestions, category names, answers, and phrases. Apply this equally
to grouped-keyboard input, physical-keyboard input, pasted text, saved library
content, gaze, and mouse. Prefix matching remains case-insensitive: `zel`, `ZEL`,
and `Zel` all become `ZEL` and can offer `ŽELIM` under the agreed Bosnian-letter
matching rules.

Normalize the active input immediately instead of maintaining mixed-case state.
Suggestion undo restores the previous uppercase prefix. Treat case variants of a
word as the same word for matching and personal learning, without merging
distinct Bosnian letters. Existing saved library entries may retain their stored
case until they are edited, but the Speech screen displays and inserts them in
uppercase.

### Bosnian spelling

Display and insert Bosnian letters without corrupting the text. Support
permissive matching when the person types ordinary letters without diacritics:

| Typed prefix | Matching behaviour |
| --- | --- |
| `zel` | Can offer `ŽELIM`. |
| `caj` | Can offer `ČAJ`. |
| `c` | Can match words starting with `C`, `Č`, or `Ć`. |
| `š` | Must respect the explicit letter, displayed and inserted as `Š`. |
| `d` | Can match words starting with `D` or `Đ`. |
| `dj` | Can offer literal `DJ` words such as `DJECA`, as well as `Đ` words such as `ĐAK`. |
| `dodj` | Can offer `DOĐI`. |
| `đ` | Must match `Đ`, without broadening it to `D` or `DJ`. |

The `d` and `dj` alternatives for `đ` apply within a typed prefix as well as at
its start. Keep literal matches eligible: accepting `dj` as an alternative for
`đ` must not exclude words such as `djeca`. These alternatives do not make `dž`
equivalent to `đ`. Showing matches does not rewrite the input; selecting one
inserts the candidate's spelling under the normal completion rules.

Selecting a completion replaces the typed prefix with the candidate's spelling;
merely showing it does not rewrite text. Preserve different inflected forms such
as `voda`, `vodu`, and `vode`. Personal learning must support words absent from
the seed and colloquial forms such as `završit`, without silently expanding them.

Match `DŽ`, `LJ`, and `NJ` identically whether entered through one grouped letter
key or through their separate characters. A prefix such as `L` may find `LJETO`;
`LJ` restricts matches to that longer prefix. Preserve the existing gaze
keyboard's deletion of these letter pairs as one unit. Prediction must not
depend on which sequence of keyboard actions produced the same text.

## Personal learning

| Event | Required effect |
| --- | --- |
| Select a suggestion in a conversation message | Increase its relevance, particularly in the same preceding context. |
| Undo that conversation selection while its undo remains available | Remove that selection's positive contribution. |
| Manually change or delete a selected conversation word before that occurrence is submitted for speech | Remove the contribution of that selection, even if suggestion undo is no longer available. Preserve earlier learning from other uses of the word. |
| Invoke the available `Izgovori` action for a nonempty conversation message | Learn newly typed words and useful word combinations when the request is made, independently of voice availability or audio completion. Do not count already credited selections twice. |
| Invoke `Izgovori` again for the same unchanged message | Do not increase counts again. |
| Invoke `Izgovori` after revising an already submitted message | Credit new or replaced word occurrences and newly formed contexts, without recrediting unchanged portions. |
| Invoke an unavailable speech action or submit empty input | Do not learn anything. |
| Clear the conversation, then compose or insert the same text again and submit it through `Izgovori` | Treat it as a new use that contributes to learning, without double-counting suggestion selections in that new message. |
| Type or select suggestions in a phrase or answer editor | Use existing predictions without changing learned counts while editing. |
| Successfully save a new or changed phrase or answer | Learn once from the final saved text, without additional credit for suggestion selections made while editing. |
| Cancel editing, fail to save, or save the same unchanged phrase or answer again | Do not increase learned counts. |
| Start a later session | Retain learned preferences. |

Learning records the person's intent to speak. A voice failure must not prevent
learning from an otherwise valid request, and retrying unchanged text must not
add another contribution. This does not make `Izgovori` available in editors or
other contexts where the existing action is disabled.

Duplicate-speech protection applies to the ongoing conversation message, not to
the text for the lifetime of the application. For example, speaking `HVALA`
twice without changing the message contributes once. Clearing the conversation,
composing or inserting `HVALA` again, and speaking it is a new use. Temporarily
clearing the visible input to open a phrase or answer editor must not turn the
saved conversation into a new message.

When a submitted message is extended or revised, learn the new or replaced word
occurrences and newly formed contexts on the next speech request. For example,
submitting `ŽELIM VODU`, appending `MOLIM`, and submitting `ŽELIM VODU MOLIM`
must not credit the original two word occurrences again. Earlier submitted uses
remain learned when text is later edited manually; explicit suggestion undo
retains its reversal behaviour described above.

Correcting or deleting a selected word before that occurrence is submitted for
speech reverses only its learning, including its word and contextual
contributions. This also applies to a new selection added after an earlier
version of the message was submitted. The reversal is independent of the
one-step undo control and must not happen twice for the same selection. Keep
the base model and learning from other occurrences intact.

For example, selecting `VOZITI` to compose `ŽELIM VOZITI` and then manually
changing the text to `ŽELIM VODU` removes that selection's contribution for
`VOZITI`. Submitting the corrected message through `Izgovori` learns from the
final message under the existing rules; it must not retain credit for the
discarded selection.

Phrase and answer editors learn words and short word combinations from the
successfully saved text, whether typed manually or entered through suggestions.
Discarded drafts, undone suggestions, and words removed before saving must not
contribute to learning. Cancelling an edit does not remove learning from earlier
successful saves; the cancelled draft simply contributes nothing new.

Word popularity alone must not displace useful contextual candidates everywhere.
Use message-start frequencies for starting words and context-specific evidence
for continuations. Exact weighting, limits, and any recency policy are technical
choices to validate; no decay schedule has been agreed.

Store counts and short word contexts rather than a full conversation history.
Keep the bundled base and personal data separate so a better base does not erase
learning. Do not embed a person's biography, family details, or real conversation
transcripts in the public seed or repository. Evaluation examples should be
synthetic.

### Forget a learned word

Provide a `Zaboravi naučenu riječ` action in settings, accessible through gaze and
mouse. It removes the selected word's personal learned entry and associated
personal ranking contributions while preserving learned data for other words.
The removal persists across application restarts.

Keep the bundled language data unchanged. A forgotten word that exists in the
base vocabulary may still appear as a normal contextual suggestion, without its
former personal boost. A word known only through personal learning is no longer
a candidate from that learning. Subsequent use can teach the word again under
the normal learning rules; forgetting does not create a permanent suppression
rule.

Forgetting a word must not change the conversation message, saved phrases, or
answers. Existing saved text alone must not immediately recreate the removed
learning; later qualifying use can teach it again. The settings flow must make
the selected word and the effect of the action clear. Its layout and interaction
belong in the HTML design review.

### Personal-storage failures

Keep message editing, speech, and prediction from valid available data usable
when personal storage fails. If the personal data cannot be read, use the bundled
model and preserve the unreadable file. Do not overwrite it with an empty
profile or silently discard the person's learning.

If a write fails, preserve the last successfully saved version and show a
nonblocking status. Never report a failed save or learned-word removal as
successful. Provide a retry through settings that works with gaze and mouse.
Do not automatically restore a stale backup that could resurrect forgotten
words. The settings and error states belong in the HTML design review.

## Language data and engine selection

### Source decision

Use **CLASSLA-web.bs 2.0** as the main seed source, conditional on validating its
Bosnian language quality and usefulness for conversation. The
[official repository](https://www.clarin.si/repository/xmlui/handle/11356/2079)
provides a separate Bosnian corpus and labels the resource CC0. Record the exact
input version, licence information, preprocessing choices, and integrity hashes
with each prepared data version.

Prepare the data before distribution; the user's computer receives a compact
derived model, not the full source corpus or a training task. Filter noise,
unwanted language mixing, boilerplate, and malformed tokens. Review the
conversational balance so web news and formal writing do not dominate. Supplement
with reviewed Bosnian conversational material and subsequent local learning.
The corpus label alone is not a guarantee that every candidate is good Bosnian.

The Bosnian corpus archive was downloaded and verified against the published MD5
before preparation. The retained archive SHA-256, sample configuration, genre and
domain counts, preparation inputs, and model checksum are recorded in
[`bosnian-model.meta.json`](../gaze_mouse/assets/bosnian-model.meta.json). The
prepared model contains 60,000 words, 125,342 bigrams, and 103,788 trigrams in a
1,348,957-byte gzip file. Its web sample contains 5,482,578 tokens from 20,924
documents across 1,635 domains. The full source archive is a development input
and is not distributed with the application.

The reviewed supplement is a structured TSV with 527 synthetic conversation
messages in 12 categories and explicit weights. Immediate needs, health, care,
comfort, food and drink, and emergencies receive more weight than general
conversation, memories, opinions, and humour. The 52 reviewed sentence starters
are stored separately and all survive model pruning. A repository test rejects
exact overlap between the supplement and either frozen evaluation set. That
check does not establish independence from paraphrases or similar expressions.

### Statistical baseline

Start the engine prototype with word frequencies and short word sequences:

- Unigrams provide individual word frequencies.
- Bigrams and trigrams score a next word from the preceding one or two words.
- Prefix matching limits completions to the word being typed.
- Personal counts adjust ranking within the applicable context.

Back off to shorter contexts when a longer sequence has no useful evidence, while
still respecting a typed prefix. This is an n-gram statistical language model,
not an LLM. Its short context limits what it can predict; usefulness must be
demonstrated before committing to its final configuration.

A trie is a possible prefix index, not the prediction model. Prefer existing
storage and indexing tools over writing a custom tree. A sorted prefix index
may suffice; choose according to measured latency, memory, data size, and
maintainability on the supported runtime.

The implemented engine uses only the Python standard library: a sorted normalized
prefix index with `bisect`, in-memory unigram, bigram, and trigram counts, and a
separate JSON personal profile. This avoids a new native packaging dependency and
keeps the matching and reversible learning rules explicit. `marisa-trie` was not
needed for the measured model size or latency. Pressagio 0.1.6 was rejected because
its older generic API does not provide the required Bosnian matching, occurrence
reversal, or storage recovery behaviour.

Context weights adapt to the retained evidence. Starting with the unigram
distribution, each longer available context takes a share of
`min(0.9, count / (count + 10 * distinct_next_words))`; the remaining share stays
with shorter contexts. Counts include weighted seed examples and personal
counts, so this is a ranking heuristic, not a calibrated probability of being
correct. The 10% fallback floor keeps other completions eligible. The existing
separate personal/base blend remains in place.

[`ranking-benchmark.json`](../language/bs/ranking-benchmark.json) compares fixed
and adaptive weights on the same prepared language data. Fixed weights need
1,018 development activations; discounts of 2 and 10 both need 1,014, with the
same 72/168 next-word top-five and 47/168 top-one hits. Discount 40 needs 1,022
activations and loses next-word hits. The recorded tie rule selects 10, which
backs off more on sparse contexts than 2. These are development results, not
independent validation. The prior 500-message model with fixed weights needed
1,028 development activations.

## Quality and performance evaluation

Use at least 100 reviewed synthetic Bosnian messages, covering everyday needs,
social conversation, humour, memories, and opinions. Include unfamiliar personal
words, inflections, colloquial forms, and prefixes with and without diacritics.
Keep the held-out evaluation messages separate from curated seed additions and
parameter tuning. Do not treat memorisation of examples as useful prediction.

Freeze the evaluation messages, scoring method, keyboard settings, starting
profile, and selection assumptions before comparing candidate models. Use a
separate development set while choosing data and ranking parameters. Report the
initial bundled model's results and personal-learning scenarios separately so
learning evaluation messages does not inflate the initial result.

Compare typing with the existing grouped keyboard, frequency-only suggestions,
and the contextual model. Record:

- Whether the intended word is among the five suggestions, both before typing
  it and after successive prefix letters.
- Letters and gaze selections needed to complete the message, including undo
  and corrections. Record leaving and returning to a suggestion button separately
  from activations; moving the gaze is not itself a button selection.
- Whether personal learning improves a repeated context without overwhelming
  unrelated contexts.
- Update latency, initial loading time, memory use, and prepared data size.

Evaluation reports count one `next_word` query before the first letter of each
non-sentence-initial word and report exact top-one and top-five rates. Sentence
starts have their own denominator. Completion queries occur after at least one
letter along the ideal selection path; their hit rate is path-dependent and must
not be described as next-word accuracy. Selection counts also distinguish
prediction from completion. The frozen keyboard scoring method is unchanged.

The original held-out set has been inspected in repeated implementation reviews.
Its unchanged 100 messages now serve as a regression set. Do not tune from it,
and do not present a new run as fresh blind validation. Independent quality
validation requires new messages authored and reviewed separately, kept away
from supplement and ranking decisions until the candidate is fixed.

The agreed initial usefulness target is at least 20% fewer required gaze
selections in the offline evaluation than the existing grouped keyboard on the
same messages. Count activations for spaces, corrections, and undo, and report
both aggregate results and results by conversation type. Record gaze departure
and return separately. A simulated activation count does not establish real
communication speed or gaze usability.

The reference laptop has an Intel Core i7-10610U, 16 GB RAM, and Windows 11 Pro
x64. Display resolution, Windows scaling, and exact physical size remain to be
confirmed during Windows verification. Use the provisional review sizes defined
in [Mac implementation and Windows handoff](#mac-implementation-and-windows-handoff)
until then. No feature benchmark or Tobii validation has run on it.

The agreed latency target is at most 100 ms from a text change to applicable
suggestions being displayed for at least 95% of measured updates after loading.
This is an acceptance target, not a measured result or an agreed overall memory
budget. Test on the reference laptop with speech and gaze active and report
loading separately.
Prediction work must not stall the UI or apply an old result to changed text.

The frozen Mac evaluation is recorded in
[`evaluation-development.json`](../language/bs/evaluation-development.json) and
[`evaluation-heldout.json`](../language/bs/evaluation-heldout.json). On the first
held-out run, the original 20,000-word contextual model used 3,724 activations
versus 6,337 for the grouped keyboard, a 41.23% reduction. The 500-message,
60,000-word model subsequently used 3,441 activations, a 45.70% reduction.
The current 527-message model and adaptive ranking were selected from the
development comparisons in [`model-benchmark.json`](../language/bs/model-benchmark.json)
and [`ranking-benchmark.json`](../language/bs/ranking-benchmark.json).
Its final regression run used 3,417 activations, a 46.08% reduction, and every
conversation category remained above the 20% target. Exact next-word top-five
hits rose from 127/475 (26.74%) to 128/475 (26.95%); top-one hits rose from
76/475 (16.00%) to 78/475 (16.42%). Sentence starts are excluded from those
rates. The improvement is modest and does not establish blind generalisation.
Loaded contextual queries measured 1.29 ms mean, 7.19 ms p95, and 12.70 ms
maximum on the final Mac regression run; initial loading was 570.23 ms. An
isolated loader process reached 171.3 MB peak RSS, compared with 20.1 MB after
importing the module without loading the model. These are development
measurements, not the required end-to-end result on the reference Windows laptop
with speech and gaze active. Real Windows and Tobii validation remains required
separately.

## Technical choices to validate

The product behaviour above is agreed. The remaining technical choices belong
to the prototype and implementation, with evidence recorded in this document.
They do not require another round of product decisions unless a result calls
for changing the agreed behaviour or acceptance targets.

| Choice | Required evidence | Current state |
| --- | --- | --- |
| Prediction library and prefix index | Bosnian matching correctness, development-set quality, speed, memory use, runtime compatibility, packaging, and maintainability. | Selected standard-library sorted index and application-specific n-gram ranker. Unicode and prefix cases are covered by pytest; no new runtime dependency is required. |
| Prepared seed and ranking configuration | Reproducible source processing, language review, vocabulary and n-gram counts, measured ranking behaviour, and data size. | Prepared from verified CLASSLA-web.bs 2.0 plus 527 weighted reviewed conversation messages and all 52 starters. Development-only vocabulary and ranking comparisons selected 60k and adaptive discount 10; metadata, benchmarks, and evaluation artifacts are linked above. |
| Personal storage and event accounting | Reversible occurrence-level learning, deduplication, durable word removal, failure handling, and preservation across app updates and rollback. | Implemented as version 1 aggregate counts with occurrence-level session credits, atomic replacement, unreadable-file preservation, retry, and installer preservation of `data/`. |
| UI layout and controls | Reviewed HTML states for suggestions, undo, editors, learned-word removal, retry, and error feedback at provisional sizes on Mac, then confirmed at actual Windows display settings. | HTML states are implemented. Qt galleries were reviewed at 1280x720 and 1440x900; target Windows display and real gaze review remain pending. |

An unavailable prediction engine must leave message editing and speech usable.
Update the table as choices are validated, including the reason for each choice
and links to its maintained artifacts or measurements.

## Implementation sequence

Implement in the phases below. Each phase produces a reviewable result and the
evidence needed by the next phase. The status column separates completed Mac
work from the Windows and target-device evidence that still requires those
environments.

### Execution and completion

A request to implement this document covers all six phases unless explicitly
narrowed. Continue through data preparation, implementation, verification, fixes,
and documentation. A plan, prototype, first completed phase, or interface filled
with demonstration words is not completion of the feature.

Choose and validate the technical details delegated above without requesting
another product decision. Reviewable phase results are checkpoints for checking
the work, not automatic pauses for approval. Follow the
[UI reference workflow](DEVELOPMENT.md#application-design-reference-workflow)
before each corresponding UI change; review the rendered reference and resolve
problems before continuing. That workflow does not itself require a separate
product approval at every phase. Review and merge responsibilities remain in
[Contributing](../CONTRIBUTING.md#review-and-merge-responsibility).

Implementation runs on macOS. After the Mac handoff, a project collaborator
verifies the change on Windows, followed by testing on the target setup with
real gaze interaction. These are planned verification stages, not unresolved
access requirements that should stop Mac implementation. Access to a Mac,
a rendered HTML reference, or simulated gaze does not establish Windows package
compatibility or real-device acceptance.

Continue resolving implementation defects and failed checks within the agreed
scope. Escalate a decision only when it changes agreed behaviour or targets, or
an external dependency or access requirement cannot be resolved within existing
authorisation. Complete independent work before reporting what remains blocked.
Do not replace the required seed with demonstration data, weaken acceptance
targets, or label an unavailable check as passed to finish the task.

Keep the following completion evidence separate in this document, linking to
maintained tests, data metadata, and evaluation artifacts where appropriate:

| Evidence | Required record |
| --- | --- |
| Implementation | Completed phases and any remaining work, selected engine and data configuration, integrated controls, bundled model, and updated owning documentation. |
| Software verification | Commands actually run, test results, reproducible quality measurements and their environment, rendered UI review, and unresolved failures or unavailable checks. |
| Windows and device verification | Package and offline-install results, update and rollback preservation, reference-laptop latency, actual display settings, and real Tobii checks. |

Full feature acceptance requires evidence for every applicable acceptance
criterion below. If access to Windows or the reference device is unavailable,
finish all independent implementation and software verification, then report
the exact outstanding checks and what access is needed. The feature remains
awaiting that validation; implementation progress must not be presented as
proof of hardware behaviour or readiness for the installed user's device.

### Mac implementation and Windows handoff

Complete the implementation and all checks available on Mac across the six
phases before handing it over. Include real prepared language data, integration,
packaging changes, tests, and documentation; the Windows reviewer should not
need to finish feature code or prepare the corpus. Actual Windows package
execution and device checks remain open until they run on those environments.

| Stage | Responsibility and completion evidence |
| --- | --- |
| Mac implementation | Implement the agreed behaviour, fix failures found by available checks, evaluate prediction quality, review the UI, and prepare the Windows verification handoff. Record Mac timings as development measurements, not proof of the reference-laptop latency target. |
| Windows verification | The designated reviewer runs the repository checks, builds and checks the Windows package, and verifies launch, UI, offline model loading, persistence, and existing application flows using the owning development and release guides. Record the tested revision and actual display settings. |
| Target-device testing | After Windows verification, check real Tobii interaction, speech, the latency target, and practical usefulness when composing messages. Record remaining defects and limitations against the acceptance checklist. |

Reduce compatibility risk before handoff:

- Keep data preparation, tokenisation, matching, ranking, and learning logic
  testable without a Windows API, speech process, or connected Tobii device.
  Integrate through the existing application boundaries and pytest fakes;
  turning the complete Windows application into a Mac application is outside
  this feature's scope.
- Keep compatibility with the Python 3.10 Windows runtime and dependency policy
  in the [development guide](DEVELOPMENT.md). Check Windows x64 availability and
  PyInstaller requirements before choosing any new runtime dependency. A library
  working on the author's Mac is insufficient evidence for adoption.
- Test the prepared model, Unicode handling, source and packaged resource paths,
  and personal-data persistence. Do not depend on the author's current directory
  or local absolute paths. Keep the base model separate from writable personal
  data, using the existing application data-root conventions.
- Make the versioned prepared model available through documented build inputs,
  with integrity metadata. It must not exist only as an untracked file on the
  author's Mac. Extend the existing Windows package smoke test to load that
  bundled model and compute a prediction without network or hardware, using
  isolated test data. Checking that a model file exists is insufficient.
- Run the available lint, automated behaviour, and regression checks. Exercise
  editor restoration, stale results, dwell cancellation, learning reversal,
  restart, and storage failure using the existing test infrastructure. Do not
  disable checks or lower coverage requirements to obtain a passing Mac result;
  record checks that genuinely require another platform separately.
- Review the updated HTML reference and any runnable Qt rendering at provisional
  logical sizes of 1280 by 720 and 1440 by 900, already used by the UI tests.
  These are development viewports, not claims about the laptop's resolution or
  Windows scaling. Keep target-display confirmation open for Windows review;
  missing display information alone must not stop implementation.

Prepare a reproducible handoff with the exact revision or identifiable working
diff, model version and hash, completed checks, known limitations, and the Windows
checks still required. Link to the existing [development](DEVELOPMENT.md) and
[release](WINDOWS_RELEASE.md) commands instead of creating a second setup or
release process. Include concise feature-specific steps and expected results
for completion, next words, undo, editor restoration, learning, forgetting,
restart, and offline use, linked to the acceptance cases below. Record any
feature-specific test command in its owning documentation when implemented.

Mac work may be reported as complete when its implementation and available
verification are complete and this handoff is prepared. Explicitly label Windows
verification and target-device testing as pending. If those stages reveal a
defect, reproduce it with the available evidence, fix it, add an appropriate
regression check, and identify the new revision and checks to repeat. A successful
Mac run alone must never close the full feature acceptance.

### Phases

| Phase | Work and completion evidence | Status |
| --- | --- | --- |
| 1. Evaluation contract | Prepare the reviewed development and held-out message sets, the grouped-keyboard baseline, and a reproducible scoring method. Record keyboard settings and simulation assumptions. Turn the agreed text, undo, and learning examples into focused cases using the existing test suite. Keep the held-out set separate from tuning. | Mac complete: frozen fixtures, protocol, hashes, simulator, and focused tests are checked in. |
| 2. Data and engine prototype | Prepare a reproducible, reviewed seed sample with source/version, licence, filtering, and integrity metadata. Prototype tokenisation, prefix matching, n-gram ranking, and personal learning outside the Speech UI. Compare against frequency-only suggestions on development data; record the chosen tools, data configuration, performance, and packaging compatibility. | Mac complete: prepared model, metadata, standard-library engine, development comparison, and learning scenarios are recorded. |
| 3. UI reference | Update and review the HTML reference through the owning development workflow at the provisional sizes above, covering all supported inputs, empty/inactive suggestions, uppercase labels, punctuation spacing, undo, gaze reactivation, learned-word removal, and storage errors/retry. Complete this before the corresponding PySide6 UI changes. Confirm actual resolution and scaling during Windows verification. | Reference and PySide states implemented. Qt galleries reviewed at both provisional sizes; rendered HTML review and actual Windows display confirmation remain pending. |
| 4. Runtime integration | Integrate the engine with Speech inputs and the existing gaze controller. Implement occurrence-level learning, input-scoped undo, persistence, word removal, and recovery. Verify that stale results cannot affect a newer input or another editor and that a replacement label cannot bypass gaze reactivation. Cover the agreed behaviour and failures with the repository's unit and UI tests. | Mac software complete with unit and UI coverage. Real Tobii interaction remains pending. |
| 5. Offline release data | Include the prepared model in the package configuration and extend the existing package smoke test to load and use it. Verify first launch with networking disabled and preservation of personal data through the existing installation, update, and rollback flows on Windows. Record model and personal-data version compatibility. A separate language-data downloader is outside this phase. | Package inputs and smoke computation implemented. Windows frozen build, offline first launch, update, and rollback preservation remain pending. |
| 6. Acceptance and documentation | Evaluate the frozen candidate against the held-out messages and agreed targets. Complete Mac checks and the handoff, then record the Windows/Tobii checks and target-display review as they run through the agreed stages above. Keep software results and real-device results separate. Update the technical choices here, implemented components in Architecture, and feature instructions in the User guide with accurate availability and validation status. | Held-out target passed on Mac and owning docs are updated. Full repository checks are recorded below; Windows and target-device acceptance remain pending. |

## Mac verification and Windows handoff

The current handoff is the working diff on branch
`feat/4-bosnian-speech-suggestions`, based on revision `b3ff22c`. The last
Mac verification ran on 2026-09-19 with macOS 27.0 arm64, Python 3.10.21,
PySide6 6.11.2, and Qt 6.11.2.

The prepared model was rebuilt from all 2,538,848 records in the verified source
archive. The 20k, 40k, and 60k candidates shared one preparation pass, and the
development-only rule selected 60k. The selected candidate is byte-for-byte
identical to the bundled gzip, with SHA-256
`1fcd68c09860a6844ec187f91fb2759f8a4f26eb20dc939d314d7bf39acdb7f5`.
The development and held-out regression commands reproduced 1,014 and 3,417
contextual activations respectively. The regression result is a 46.08% reduction
from the 6,337 grouped-keyboard activations.

The following software checks passed on Mac:

- Ruff lint and formatting, Python bytecode compilation, `git diff --check`,
  actionlint 1.7.12, and PSScriptAnalyzer 1.25.0 for all 10 PowerShell scripts.
- The complete pytest suite with coverage: 202 passed, 23 Windows-only tests
  skipped, and 68.6% coverage against the 60% repository floor.
- The source `--package-smoke-test`, including model checksum validation and an
  offline `ŽELIM` prediction.
- All 19 deterministic Qt gallery surfaces rendered. The Speech and learned-word
  Settings states were reviewed at 1280 by 720 and 1440 by 900 with no clipping,
  overlap, or contrast defect remaining.

The recorded commands were:

```text
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -B -m compileall -q gaze_mouse scripts tests run_gaze_mouse.py
git diff --check
.venv/bin/python -B -m pytest -p no:cacheprovider --cov=gaze_mouse --cov=scripts --cov-report=term-missing:skip-covered --cov-fail-under=60
pwsh -NoLogo -NoProfile -File scripts/check_powershell.ps1
.dev-tools/actionlint/1.7.12-darwin-arm64/actionlint
.venv/bin/python -B -m gaze_mouse.main --package-smoke-test
.venv/bin/python scripts/prepare_speech_model.py .dev-tools/corpora/CLASSLA-web.bs.2.0.jsonl.gz --output /private/tmp/pogled-refinement.D5ILcD/candidate.json.gz
.venv/bin/python scripts/benchmark_speech_models.py .dev-tools/corpora/CLASSLA-web.bs.2.0.jsonl.gz --output-dir /private/tmp/pogled-refinement.D5ILcD/models --report language/bs/model-benchmark.json
.venv/bin/python scripts/compare_speech_ranking.py --output language/bs/ranking-benchmark.json
.venv/bin/python scripts/evaluate_speech_model.py --dataset development --output language/bs/evaluation-development.json
.venv/bin/python scripts/evaluate_speech_model.py --dataset heldout --output language/bs/evaluation-heldout.json
.venv/bin/python -B -m scripts.capture_ui --output /private/tmp/pogled-assist-suggestions-1280 --width 1280 --height 720
.venv/bin/python -B -m scripts.capture_ui --output /private/tmp/pogled-assist-suggestions-1440 --width 1440 --height 900
```

The language-model update has no visible UI change, so the earlier Qt gallery
and HTML review remain applicable. The HTML reference source was reviewed
against the Qt result. Direct rendering
of the local HTML file remains pending because the available controlled browser
blocked local-file access. The following checks also remain pending and must run
on the owning environment before full acceptance:

- `dev.ps1 check` and `dev.ps1 package` on Windows, including the frozen
  executable smoke test.
- First launch with networking disabled, then installation, update, rollback,
  and preservation of `data\speech_learning.json`.
- The actual display resolution and scaling, visible UI, and end-to-end p95
  suggestion latency on the reference Windows laptop with speech and gaze active.
- Real Tobii selection and eye-loss safety, AppBar, calibration, Windows input,
  and offline and online speech checks from the existing development and release
  guides.

## Acceptance checklist

Checked items have complete Mac software evidence. Hardware- or Windows-specific
items remain open until the handoff checks above are recorded.

- [ ] A fresh installation offers Bosnian completion and next-word suggestions
  without any network request or first-use download.
- [ ] The existing Windows package smoke test loads the bundled model and
  computes a prediction without network access, hardware, or personal user data.
- [x] Text changes refresh the applicable candidates; unchanged text is stable,
  and empty messages and no-result prefixes behave as specified.
- [x] Suggestions are inactive while the caret is away from the end or text is
  selected. Moving back to the end with no selection restores applicable
  candidates without altering text or bypassing gaze reactivation.
- [x] Suggestions work in conversation messages and phrase or answer editors,
  remain inactive for category names, and never apply an editor's text or pending
  selection to the saved conversation. Category labels are not learned.
- [ ] Mouse and gaze produce the same text, preserve Bosnian letters, preserve
  the rest of the conversation, and insert the agreed spacing.
- [x] Entering `.`, `,`, `?`, or `!` immediately after a suggestion removes only
  its automatically inserted trailing space. A following suggested word gets a
  separating space when needed, without duplicating existing whitespace or
  reformatting earlier text. Its undo restores the exact previous input.
- [x] `.`, `?`, and `!` start a new prediction context and offer sentence starters
  without requiring a typed space. A comma retains the sentence context. Seed
  preparation and personal learning use the same boundaries, and personal
  preferences and previous message text remain intact.
- [x] The conversation message, suggestions, categories, answers, and phrases
  display and enter as uppercase. Lowercase and mixed-case input normalize
  immediately, suggestion undo restores the prior uppercase input, and case
  variants do not become separate learned words.
- [x] One-step suggestion undo restores the exact prior text and reverses
  learning from that selection. Waiting or looking elsewhere does not expire
  undo; later text edits expire it, and a new suggestion replaces it with undo
  for that newest selection. Selecting a suggestion never triggers speech.
- [x] Speech submission retains any still-valid suggestion undo. Library editors
  keep separate undo and learning state, restore the conversation's state when
  closed, and discard editor undo only on closing that editor. A failed editor
  save leaves its undo available.
- [x] Continued gaze cannot activate a replacement suggestion in the same slot.
  Text changes cancel pending selection; existing gaze safety remains intact.
- [x] Completion respects explicit diacritics and supports the agreed ordinary
  letter alternatives, including `d` and `dj` for `đ` while retaining literal
  `dj` matches and respecting explicit `đ`, with final tokenisation cases covered.
- [x] `DŽ`, `LJ`, and `NJ` produce the same prefix matches regardless of how their
  characters were entered, and existing gaze-keyboard deletion stays intact.
- [x] Personal learning survives restart, improves relevant contexts, and does
  not double-count selected words or repeated unchanged speech requests.
- [x] An available speech request with nonempty text learns independently of
  voice availability or audio completion. Unavailable actions, empty input, and
  retries of an unchanged message add no learning.
- [x] Submitting a revised message learns new or replaced occurrences and new
  contexts without recrediting unchanged portions. Later manual edits preserve
  earlier submitted uses; explicit suggestion undo reverses its own contribution.
- [x] Clearing a conversation and composing or inserting the same text again
  allows a new use to contribute to learning. Repeated unchanged speech requests
  within that message remain deduplicated, and opening an editor does not create
  a new conversation use.
- [x] Manually changing or deleting a selected word before that occurrence's
  speech submission reverses only that selection's learning, even after its undo
  becomes unavailable, without removing earlier learning or reversing the same
  credit twice. Speech submission learns from the corrected final message.
- [x] Phrase and answer editors learn only from successfully saved final text.
  Draft selections receive no extra credit; cancelled or failed saves and
  repeated saves of unchanged text add no learning.
- [x] Gaze and mouse can forget an individual learned word through settings.
  Its personal entry and ranking contributions stay removed after restart,
  while other learned words, the base vocabulary, conversation text, and saved
  phrases or answers are preserved. Base candidates remain eligible and later
  qualifying use can teach the forgotten word again.
- [x] Prediction failures leave message editing and speech usable; personal
  data remains separate from replaceable language data.
- [x] Unreadable personal data is preserved while prediction uses the bundled
  model. Failed writes retain the last saved version, produce a nonblocking
  status, and never claim successful persistence. Settings offer a gaze- and
  mouse-accessible retry without silently restoring stale learned data.
- [x] Existing conversation restoration, library editors, alarm, sleep, and
  exit flows preserve their state with suggestions present.
- [x] At least 100 reviewed, held-out synthetic messages demonstrate at least
  20% fewer required gaze selections than the grouped-keyboard baseline under
  the frozen scoring method. Results by conversation type and separate
  personal-learning scenarios are recorded.
- [ ] At least 95% of loaded suggestion updates display applicable results within
  100 ms on the reference Windows machine with speech and gaze active. Initial
  loading time, memory use, and prepared data size are recorded separately.
- [x] Offline packaging, data preservation, visible UI, and real gaze checks
  are recorded separately, with unavailable checks explicitly marked not run.
