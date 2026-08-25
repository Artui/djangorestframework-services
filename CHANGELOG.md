# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.43.0] — 2026-08-25

### Added

- **Agent field audience — one serializer, more than one kind of reader.** A
  serializer written for a frontend is read verbatim by a model when the same
  spec is exposed as an agent tool, and the two want different subsets of it:
  a frontend needs the primary key to route on, where a model will read it out
  loud as if it were content. Declare the difference once, on the field, with
  `AgentField` in DRF's per-field `style` bag under `AGENT`:

  ```python
  class Meta:
      model = Invoice
      fields = ["id", "number", "status", "etag"]
      extra_kwargs = {
          "id": {"style": {AGENT: AgentField.handle("Invoice handle.")}},
          "etag": {"style": {AGENT: AgentField.hidden()}},
          "number": {"style": {AGENT: AgentField.label()}},
      }
  ```

  `style` is used because it is the only door `Meta.extra_kwargs` opens onto a
  field constructor, so a `ModelSerializer` keeps auto-generating its fields.
  Being **on the field** rather than in a list on `Meta` is what lets the
  marking travel into nested serializers with no hoisting rule, and what stops
  a rename from silently desyncing it. The DRF view path reads none of this —
  `style` is consulted only by `HTMLFormRenderer`, and only for its own keys —
  so REST responses are byte-identical whether or not a serializer is marked up.

  The payload and the schema are generated from that one declaration, so they
  cannot disagree about what a caller receives: hidden fields leave both,
  and a substituted choice field is re-declared in the schema in its display
  values. `output_to_json_schema` takes a `projection=` and applies it to the
  **item**, wherever the item sits for the requested `kind` — the array
  wrapper and the pagination envelope are the generator's own shapes and
  belong to no serializer.

  New public API: `AGENT`, `AgentField`, `AgentProjection`, `FieldAudience`,
  `build_agent_projection`, `agent_projection_for_spec`, `project_payload`,
  `annotate_output_schema`, and `render_for_agent` / `arender_for_agent` on the
  dispatch surface.

  **No wording for a model lives here.** The markings say what a field *is*;
  what a reader should do about it depends on the reader, and only the
  transport knows which kind is reading. `annotate_output_schema` and
  `output_to_json_schema` take a `handle_description=` and default to emitting
  nothing. The last of those is the one an alternate transport calls
  instead of `render_spec_output`, so every agent transport shapes payloads
  identically rather than each growing its own post-processor.

- **Choice fields carry their labels into the schema.** DRF holds
  `{value: display}` and only the values used to survive. A field whose labels
  say something its constants do not now emits
  `{"oneOf": [{"const": "PENDING_REVIEW", "title": "Awaiting review"}, ...]}`.
  `title` is an annotation keyword, so the accepted values are exactly the
  `const`s — identical to the `enum` form it replaces. Labels that merely repeat
  their value stay a bare `enum`.

### Fixed

- **A choice field's schema could reject a value the serializer accepts.**
  `allow_blank` / `allow_null` were widened in unconditionally, so a field
  already declaring `""` or `None` among its choices — Django prepends an
  empty choice to every `FilePathField` — emitted that `const` twice. `oneOf`
  requires *exactly* one match, so the widening meant to admit the value
  rejected it instead.

- **Output schemas dropped every `read_only` field.** `serializer_to_schema`
  skips them by design — it was written for input — and `output_to_json_schema`
  reused the same walker, so an output schema was silently missing its primary
  key, its ETag, and every `SerializerMethodField`, none of which had stopped
  being rendered. A consumer generating an `outputSchema` from a spec advertised
  a shape its own payloads did not match. `serializer_to_schema` now takes
  `for_output`, which the output path passes: `write_only` fields drop out
  instead. `required` on an output schema means the key is present, not that
  its value is non-null, and is claimed only where DRF cannot omit it —
  `Field.get_attribute` raises `SkipField` and drops the key entirely when the
  source attribute is missing and the field has no default, does not allow
  null, and is not required. **This changes the shape of every generated
  output schema.**

- **`MultipleChoiceField` was described as accepting a single value.** It
  subclasses `ChoiceField`, so the walk caught it first and emitted a plain
  `enum` for a field that accepts a *set*. It now produces a `uniqueItems` array
  of the choices, with `minItems: 1` when `allow_empty=False`. `FilePathField`
  subclasses `ChoiceField` too but is single-valued, so it keeps the plain
  branch.

- **`allow_blank` / `allow_null` never reached a choice field's schema.** Both
  widen what DRF accepts and neither appears in `field.choices`, so the schema
  claimed an exhaustive value set that rejected input the serializer takes.

- **The floor-resolution CI gate could resolve against a stale package index.**
  Its purpose is to answer "what would a consumer installing from scratch get" —
  the comment above it cites a real incident where only a consumer resolving from
  scratch caught a bad floor. But both of its resolutions read the runner's
  shared uv cache, so the answer came from whatever package list that cache held
  rather than from the index. A sibling's gate failed a floor raise as
  unsatisfiable while the index had been serving the release for some time; two
  re-runs reused the same cache and failed identically, and a cacheless resolve
  of the same requirement succeeded at once. Both resolutions now use
  `--refresh`, so the gate measures what it claims to. A stale listing was
  dangerous in both directions: it can invent a broken floor, or hide a real one.

## [0.42.0] — 2026-08-24

### Upgrade notes

**One way this can bite on upgrade, and it fails loudly at startup rather than
quietly in production.** A relation spec declaring `field_map` that renames an
input key **onto the primary key**, while `match_key` also matches on that key,
now raises `ImproperlyConfigured` where it previously constructed fine:

```python
# Now refused at construction.
ForwardRelationSpec(model=Author, scope=..., field_map={"ident": "pk"})
```

Nothing working breaks. That declaration could never do anything: the matcher
read `"pk"` off the row and found nothing, so the row fell through to create,
where the primary-key guard read `"ident"` and refused it — every payload using
the mapped name rejected, and none able to match. The error simply moved from
every request to the one place that can fix it. Send the key under a name
`match_key` reads, or match on a field the mapping does not rename. Mapped onto
a **natural** `match_key` the same declaration is coherent and still allowed.

Everything else is a bug fix or additive. A matched row that sends its own
primary key now writes without it — behaviourally identical for an integer key,
which already diffed to nothing, and a fix for a string key, which was a 500.

### Fixed

- **A matched nested row no longer carries its own primary key into the write.**
  `ForwardRelationSpec` and `ManyToManySpec` match through the ORM, which coerces
  `"5"` to pk `5`; Python then does not, so `pk` landed in `update_fields` and
  Django's `ValueError` escaped the service untranslated — a **500** for a
  payload the library had already matched. Any un-coerced input reaches it: a
  JSON body straight to a dataclass, a URL kwarg, an `input_serializer`
  declaring the nested payload as a plain `DictField`. A matched row's key is by
  construction already that row's, so it is now dropped from the write, and
  `exclude_fields=["pk"]` is no longer a line anyone needs to write. Only
  primary-key spellings are dropped: a natural `match_key` describes the row as
  well as identifying it, so changing it still renames the row it matched.
  `ChildSpec`, `GenericRelationSpec` and `ReverseOneToOneSpec` match through the
  parent's own manager, which does not coerce, and were never exposed to the
  crash.
- **A row whose write fails with a bare `ValueError` is now reported as that
  row.** Django raises one when a row's own data cannot be written at all, and
  with no `detail` to namespace it left the service as a 500 naming nothing. It
  now arrives as a `ServiceValidationError` against the relation and the row's
  position, like every other row error. Scoped to the mutation-helper call — a
  declared `create_service` / `update_service` is opaque caller code, and reading
  its `ValueError` as a 400 would report its own bug as the client's mistake.
- **`field_map` renaming an input key onto the primary key is refused at
  construction** when `match_key` matches on that key, rather than rejecting
  every payload at request time. Matching reads the row exactly as it arrived
  and does not apply `field_map`, while the primary-key guard does — so the
  matcher looked for one name and the guard refused the other, and no payload
  could ever match. Raises `ImproperlyConfigured` from `ChildSpec`,
  `ForwardRelationSpec`, `ManyToManySpec` and `GenericRelationSpec`. Mapped onto
  a *natural* `match_key` the same declaration is coherent and still allowed;
  `ReverseOneToOneSpec` finds its row through `fk` and has no match key to
  collide with.

### Changed

- **The relation specs' `m2m=` now accepts a plain mapping**, not only a callable
  — `Mapping[str, Any] | Callable[[Any], Mapping[str, Any]]`, matching what
  `create_model` / `update_model` have always taken. Lifting a working
  `create_model(Post, m2m={"tags": …})` into a `ChildSpec` previously failed
  under `ty` and raised `TypeError` at runtime. Additive; the callable form is
  unchanged.

### Documentation

- **The shaping knobs now say what they shape.** `field_map`, `exclude_fields`,
  `m2m`, `children` and `relations` configure the row's **write** and nothing
  else — matching, the primary-key guard and the parent link all read the row as
  it arrived. Four specs described the knob as "Forwarded likewise" and none
  said this, which is what sent a consumer asking why `exclude_fields=["id"]`
  excludes the very key that finds the row. Stated on all five specs and in
  [Nested writes](https://artui.github.io/djangorestframework-services/recipes/nested-writes/),
  along with the note that `match_key` is an input key on one side and a model
  field on the other.

## [0.41.0] — 2026-08-14

### Upgrade notes

**Two observable changes, both of them the point of the release.** A mutation's
returned instance now reads back what the write actually did, where before it
could still hold pre-write related data. Nothing about what reaches the database
changes; what changes is what a response rendered from that instance says.

- **Response bodies.** A serializer rendering the mutated instance now shows the
  written values for a forward relation and a reverse one-to-one, and the current
  membership for a prefetched collection. If you asserted the *old* values —
  deliberately or, far more likely, because that is what the library returned —
  those assertions now fail, and the new value is the correct one. Specs whose
  output comes from an `output_selector_spec` re-fetch were never affected either
  way.
- **Query counts.** They move in both directions. A re-matched forward relation
  now costs **one query fewer**, because reading it back no longer falls through
  to the database. A prefetched collection whose membership the write changed
  costs **one more**, because the stale cache is dropped rather than served. Test
  suites that pin exact counts with `assertNumQueries` are where this shows up.

Nothing needs configuring, and there is no flag to restore the old behaviour: the
old behaviour was a response reporting values the same request had already
replaced.

### Documentation

- **Reloading and re-fetching are now documented as the different tools they
  are.** Two pages each held half of this and neither pointed at the other:
  concepts prescribed an `output_selector_spec` re-fetch for a stale fetch-time
  annotation, while the nested-writes recipe said the relations a write resolved
  need no reload. A reader with a stale value in a response had no way to tell
  which lever was theirs, and the intuitive guess — that a `refresh_from_db()` is
  the cheaper version of a re-fetch — is wrong in both directions.

  There is now one table, keyed on **who computed the value** the response is
  missing. Your code before the save (plain columns, `auto_now`, the row a
  one-row relation resolved) needs nothing; the database during the save (an
  `F()` expression, a `GeneratedField`, a database-side default) needs a reload;
  the queryset that fetched the row (`annotations=`, `select_related`) needs a
  re-fetch and *only* a re-fetch, because reloading concrete columns cannot
  restore an annotation. The recipe and the migration note link to it rather than
  restating it.

  Two consequences are called out because they surprise: `views=F("views") + 1`
  leaves the *expression object* on the instance, so a serializer rendering it
  needs the reload; and a blanket `refresh_from_db()` after a nested write is not
  merely redundant but actively discards the relation agreement, since a reload
  drops every cached relation. A test now pins the `auto_now` end of the table,
  which was previously asserted only against the database.

### Fixed

- **A written one-row relation no longer leaves the pre-write row on the parent.**
  After `update_from_input` wrote a forward relation or a reverse one-to-one, the
  instance it returned could still hold the related object as it was *before* the
  write, so anything rendering from that instance — a response serializer, most
  obviously — reported values the write had already replaced. The write itself was
  always correct, and a follow-up read showed the new value, which is what made it
  expensive to find: only an assertion on the response body of the request that
  did the write could see it.

  Both kinds find their row by **re-querying** — a forward target inside `scope=`,
  a reverse one-to-one through its `fk` — so what gets saved is a different Python
  object from the one the parent had cached, and nothing invalidated that cache.
  The forward case slipped past the diff as well: two rows sharing a primary key
  are equal, so the column was correctly left alone and the stale object was left
  with it. Any caller that read the relation before the write was exposed, and
  reading it first is the ordinary shape — a validator reaching through the
  relation, a before/after comparison, a `scope=` callable.

  The written row is now assigned back onto the parent, and the cached entry is
  dropped where the write cleared or removed the relation, so the next read says
  what the database says. Both are in-memory descriptor work: **no extra query**,
  which is why this is not `drf-nested`'s blanket `refresh_from_db()` — that pays
  a query even when nothing read the relation, or when the caller re-fetches
  anyway, as a spec whose output comes from a selector does.

  Dropping the entry rather than assigning `None` through the descriptor is
  deliberate for the removal case: assigning would also blank the removed row's
  own link in memory, which a `delete_service` that kept the row linked never
  asked for.

- **A prefetched collection no longer reports the membership it had before the
  write.** Rows a nested write added or removed were invisible to a
  `prefetch_related` cache built beforehand, so the returned instance rendered the
  old collection. Over HTTP this was already covered — the view layer drops
  `_prefetched_objects_cache` on the mutation target wholesale, as DRF's
  `UpdateModelMixin` does — so what this reaches is a direct call to the mutation
  helpers, and `delete_relations` under a `soft_delete` parent, which outlives its
  own cascade and is exactly the row a response then renders.

  Dropping rather than rebuilding is the only honest option: the cached queryset
  carries its own ordering, its own filtering and any nested prefetches, so
  nothing assembled in memory can stand in for what the database would now return.

  Which writes drop it follows the same question as the fix above — did the
  library write the object the parent already had? Rows **added or removed**
  always drop it. A row merely **updated** keeps it for reverse-FK and generic
  collections, which are reconciled inside the parent's own accessor and so wrote
  the very object the cache holds; dropping there would cost a query to re-read
  what it already has. A **many-to-many** drops on any write, since it matches in
  `scope=` rather than in the membership and therefore writes a different object.
  A per-row **`update_service`** drops on any write, because the library did not
  do the writing and cannot vouch for what the service touched.

## [0.40.0] — 2026-08-13

### Added

- **`ServiceNotFound` and `ServiceConflict`** — two shapes of service failure
  common enough to name, mapped to `404` and `409` at the view boundary while a
  plain `ServiceError` stays `422`. They exist because there was **no
  transport-agnostic way to say either one**: the only lever was an HTTP status on
  the exception, which nothing reads and which cannot mean anything off HTTP.
  Being `ServiceError` subclasses, a transport that has never heard of them still
  handles them, and one that wants to do better matches on the class — before its
  generic handler, since the subclass check would otherwise swallow them. The
  mapper's own branch order says the same thing.

  `ServiceNotFound` answers identically for *absent* and for *not yours to see*,
  on purpose: a `403` on a row the caller cannot see confirms that the row exists.

  The generated OpenAPI document still declares only the `422` for spec-driven
  mutations. Advertising a `409` on every mutation, including those that cannot
  collide, trades one wrong claim for another — a spec has no field saying which
  failures its service raises, and inventing one to satisfy the schema is not a
  trade this package makes.

### Documentation

- **The preconditions recipe no longer teaches a line that does nothing.** It
  opened with `class BudgetLocked(ServiceError): status_code = 409` and promised
  "a clean 409 for a browser", while every non-validation `ServiceError` mapped to
  a fixed `422` — which the errors reference documented correctly, so two pages
  contradicted each other and the more inviting one was wrong. The recipe raises
  `ServiceConflict` now, and both pages say outright that a `status_code`
  attribute is read by nobody and could not be honoured off HTTP anyway. Found by
  writing that line from outside the package and watching a 422 come back.
- **The exceptions reference lists the whole family.** `ServiceNotFound`,
  `ServiceConflict` and — already missing — `AdditionalInputRequired` now render
  alongside the other two.

### Fixed

- **`OfflineHttpRequest.offline_host`'s description now reaches the page at all.**
  It documented itself with a Sphinx `#:` comment, which mkdocstrings reads as an
  ordinary Python comment — so the text was never rendered anywhere. It is an
  attribute docstring now, and the dispatch reference carries it.
- **The reST literal-block marker no longer reaches the page.** Sphinx reads a
  trailing `::` as "an indented literal block follows" and prints one colon;
  Markdown has no such rule, so the second colon rendered verbatim. The indented
  block was already coming out as a code block either way, so this drops the
  stray character and nothing else. 19 occurrences, the last of the Sphinx
  markup this package carried.

### Fixed

- **Docstring cross-references now render as links instead of raw markup.** The
  docstrings carried Sphinx roles — ``:class:`~rest_framework_services.ServiceSpec` ``
  — but the docs build is mkdocstrings, which renders docstring bodies as
  Markdown and has no such syntax, so all 450 reached the published page
  verbatim, `:class:` prefix and Sphinx's abbreviating `~` included. They are now
  mkdocstrings autorefs links; references to symbols the reference does not
  render, to undocumented dataclass fields and enum members (mkdocstrings emits
  no anchor for those), and to third-party symbols became plain code spans.

  This also clears the raw roles that were leaking onto **other** packages'
  reference pages, which render some of these symbols from the installed
  package — `djangorestframework-pydantic-ai` and
  `djangorestframework-mcp-server` both pick the fix up on their next resolve.

## [0.39.0] — 2026-08-12

### Upgrade notes

**One behaviour change to know about before upgrading.** Error payloads now use
the names the request used. Where an input serializer field declares `source=`,
a refusal that the service raised about the column is reported under the
serializer's field name instead — at every depth, including the fields inside a
nested row. If your clients read errors keyed on field name, this is the change
that reaches them: it makes those keys match what they sent, which for an
aliased field they previously did not. Serializers whose fields are named after
their columns are unaffected, as is any spec with no `input_serializer` and any
direct call to the mutation helpers.

### Changed

- **A refusal now comes back under the names the request used, not the model's.**
  A service raises about the model, because the model is what it was handed —
  and where a serializer field declares `source=`, the request called that field
  something else. DRF resolves `source=` while building `validated_data`, at
  every depth, so the wire name reaches neither the service nor the mutation
  helpers nor a relation spec; every refusal was then reported under a key the
  request did not have, which an error renderer keyed on field name drops. The
  serializer is the one thing still holding both vocabularies, so the dispatcher
  now translates a `ServiceValidationError` / `ValidationError` back through it
  on the way out:

  ```python
  # headline = CharField(source="title"); writer = AuthorSerializer(source="author")
  raise ServiceValidationError({"title": [...], "author": {"name": [...]}})
  # the client is told: {"headline": [...], "writer": {"some_name": [...]}}
  ```

  It renames only what the serializer can name — an unmatched key such as
  `non_field_errors` passes through untouched — and only from writable fields,
  skipping `source="*"` and dotted sources, neither of which is a key of
  `validated_data`. The detail's shape is preserved: a string stays a string, a
  collection keeps its row alignment. It covers the service call and its
  preconditions, and lives in the shared dispatch core, so HTTP, off-HTTP, sync
  and async all agree.

  **This changes error payloads for specs whose input serializer uses
  `source=`**, which is the point — but it is a visible change if you had
  adapted to the column names. A `ModelSerializer` whose fields are named after
  their columns is unaffected, as is any spec with no `input_serializer` and any
  direct call to the mutation helpers.

- **The API reference now documents attributes and arguments as typed entries
  rather than prose.** Spec classes such as `ServiceSpec`, `SelectorSpec` and the
  relation specs described each field in free-form paragraphs, and the fields
  themselves carried comments restating the same contracts further down the
  file — two hand-maintained copies of one fact, of which only one was
  published. They are now Google-style `Attributes:` / `Args:` blocks, so
  mkdocstrings renders each entry alongside its real annotation and default.
  Several fields that had been documented only in source comments, and so were
  absent from the reference entirely, are now published: `SelectorSpec`'s
  `progress_reporter` and `preconditions`, `ViewHooks.progress`, and
  `dispatch_spec`'s `filter_data`, `instance`, `progress` and `view_hooks`.

  No behaviour change, no signature change, and no public symbol moved.

### Removed

- **Emoji and marker glyphs, everywhere in the repository.** Comments,
  docstrings, `docs/` and this changelog. A `forbid-marker-glyphs` pre-commit
  hook now enforces it. Typography is untouched: dashes, arrows, the
  multiplication sign, math comparisons, box-drawing characters and the plain
  check mark used as table data all stay.

### Fixed

- **A relation row whose `match_key` was left `UNSET` is now a create, not a
  match against the sentinel.** `UNSET` means "field omitted from input"
  throughout the library and `filter_input` drops it before anything is
  assigned — but the match-key reads happen before that, so a row supplied as a
  partial-input dataclass with its `pk` unset had the sentinel read as if it
  were a key. The outcome varied by kind and column type: a `TypeError` from a
  numeric primary key, an `ImproperlyConfigured` telling the author to declare a
  `scope=` their create-only spec does not need, a `scope`-miss refusal on a
  `CharField` key coerced to the string `"UNSET"`, or the primary-key guard
  refusing a payload that carried no primary key — its message asking the caller
  to "omit the identifier to create a new one", which is what they had done.
  A non-pk `match_key` on an owned collection was accidentally correct, missing
  the lookup and falling through to the create branch, so the same defect passed
  or failed depending on which kind a test happened to cover first.

  All four sentinel-blind reads now treat `UNSET` exactly as they treat an
  absent key: the shared scoped-match resolver behind `ForwardRelationSpec` and
  `ManyToManySpec`, both owned-collection loops, and the primary-key guard. What
  a declared `create_service` / `update_service` receives in `data` is
  unchanged — still the coerced payload verbatim, sentinels included, which is
  how a nested service tells "omitted" from `None`.

## [0.38.0] — 2026-08-11

### Upgrade notes

**Nested writes cover every relation kind now, and `children=` keeps working
unchanged.** Nothing shipped in 0.18.0 changes behaviour; the new kinds arrive
through a `relations=` map beside it.

**One behaviour change to know about before upgrading.** A nested create that
carries a primary key is refused, on every relation kind. 0.37.0 introduced that
rule for `children=`; it now covers the four kinds added here. If you are
migrating from `drf-nested`, which upserts by primary key, this is the first
thing you will hit — send a row the parent already owns, or omit the identifier
and let it be created. A natural `match_key` is unaffected and still upserts.

### Added

- **`relations=` on the four mutation helpers and the model-service factories.**
  One map, a spec class per relation kind, with write order derived from the
  class rather than from dict insertion: forward relations, then the parent's
  `save()`, then reverse FK and reverse one-to-one, then generic, then
  many-to-many. `children=` is preserved as the reverse-FK alias, and a
  grandchild declared either way cascades either way.

  The five kinds: `ForwardRelationSpec` (foreign key and one-to-one, one code
  path, written before the parent so the assignment flows through the existing
  `diff_attrs` and `update_fields` machinery), `ReverseOneToOneSpec`,
  `ManyToManySpec`, `GenericRelationSpec`, and `ChildSpec` for reverse FK.
  Singular kinds are tri-state: omitted leaves the relation untouched, `None`
  clears it, a mapping writes it.

- **`create_service` / `update_service` / `delete_service` slots on a relation
  spec**, so a child row whose write has real behaviour stays declarative
  instead of forcing a hand-written reconciliation loop. The spec owns
  reconciliation and the service owns the row: matching, `mode`, and orphan
  handling never move into user code.

  Services run through `run_service` / `arun_service` with `atomic=False`, since
  the parent's atomic block already wraps the tree and a savepoint per row is
  not what anyone wants. An update service returning `None` means "use the
  in-memory instance", as elsewhere in the framework. An async loop needs an
  `async def` slot.

- **`context=` on the four mutation helpers**, threaded into nested pools only
  and never read by the helpers themselves. The model-service factories populate
  it from the dispatch pool they already receive, so a `scope=` callable and a
  nested service can see the acting caller. Hand-written services opt in with
  `context=kwargs`.

- **`scope=` on the kinds that match by key.** A queryset or a callable resolved
  from the context pool by signature. Unscoped means create-only; a payload that
  arrives carrying a match key without a declared scope raises
  `ImproperlyConfigured`, because the remedy is always to declare a scope and
  never to send different data.

- **`RelatedObjectChange` and `ChangeResult.relations`**, so a one-row relation
  stops being reported as a collection of one. Outcomes are `untouched`,
  `created`, `updated`, `cleared`, `unlinked`, `deleted` and `removed`;
  `ChildCollectionChange` gains a matching `removed` tuple for rows a
  `delete_service` handled.

- **`orphan=` on the specs whose rows the parent owns** — `ChildSpec`,
  `GenericRelationSpec`, `ReverseOneToOneSpec` — saying what happens to a row
  the relation lets go, where `mode=` says whether one is let go at all.
  `"auto"` (the default) keeps today's derived rule: unlink when the link is
  nullable, else delete. `"unlink"` and `"delete"` state it instead.

  The derived rule is still the better default, because it honours what the
  model declares. What it cannot do is stand in for intent: nullability is a
  fact about a column, so one `mode="replace"` means destructive or
  non-destructive depending on something the spec author is not looking at, and
  someone adding `null=True` in a later migration flips disposal from delete to
  unlink with no change to the spec and none to its tests.

  `"unlink"` against a link that cannot hold `NULL` raises
  `ImproperlyConfigured` when the relation is written — the same moment `"auto"`
  reads the schema, since a spec is routinely built before Django can be asked
  about a column. An explicit `orphan` beside a `delete_service` raises at
  construction: the service *is* the disposal, so the flag would decide nothing.
  Not on `ManyToManySpec` (a target is shared and never deleted) and not on
  `ForwardRelationSpec` (it removes nothing). The `delete_model` cascade honours
  it too, since it disposes of the same rows.

- **`RelationOutcome` and `RelationMode` enums.** Both subclass `str`, matching
  `SelectorKind`, so the values they replaced still compare equal and stay
  JSON-serializable: `change.outcome == "created"` keeps working and
  `mode="replace"` is still accepted. Typing them is what surfaced four places
  in the writer that were still passing the outcome around as a bare string.

- **Generic relations, with `django.contrib.contenttypes` gated.** The package is
  imported during `apps.populate()`, so the content-type model is imported
  inside the function and only after `apps.is_installed` confirms the app. A
  dedicated CI job runs the suite with the app absent and asserts the module is
  never imported, so the gate is measured rather than asserted.

### Changed

- **The delete cascade covers every relation kind, under one rule: it removes
  the rows the parent owns and does nothing to the rows it merely points at.**
  Reverse FK, generic and reverse one-to-one rows are removed deepest-first by
  the existing unlink-or-delete rule. A many-to-many target is shared, so only
  the membership goes and it is reported as unlinked. A forward target is left
  untouched rather than refused, because the same `relations=` map declares the
  write path and refusing it would make a good write spec un-cascadable.

  `delete_model` and `adelete_model` take `relations=` beside `children=`.

- **A relation named by both `m2m=` and `relations=` raises.** Both keywords
  stay and they do different jobs, but writing a relation twice in an order
  nobody chose is not one of them.

- **A row service declared next to the knobs it would silence raises at
  construction.** A `create_service` or `update_service` owns the row, so
  `field_map`, `exclude_fields`, `m2m`, `children` and `relations` on the same
  spec would be quietly ignored. Reconciliation knobs are unaffected, and
  `delete_service` is not restricted: it replaces the unlink-or-delete rule
  rather than the row write.

- **An error raised while writing a related row is reported under that
  relation.** A `create_service` / `update_service` raising
  `ServiceValidationError` — or DRF's `ValidationError`, since a service may
  reach for either — propagated bare, so a row's `{"title": [...]}` was
  indistinguishable from the parent's own `title`, and a collection never said
  which row was refused.

  The shape is DRF's `ListSerializer` rather than one of ours, so a reader
  migrating off a writable-nested serializer keeps the error handling they have:
  a collection reports a list as long as the incoming one with an empty dict
  against the rows that passed (`{"posts": [{}, {"title": ["Too rude."]}]}`) and
  a relation holding one row reports the payload under the name
  (`{"profile": {"bio": ["Too long."]}}`). The detail itself is passed through
  untouched — a string stays a string, a list stays a list — and the class you
  raise is the class the caller gets. Relation names nest, so a grandchild's
  error carries both.

  The library's own refusals — the primary-key guard, a `scope` that matched
  nothing — already named their relation and are unchanged. One consequence for
  a 0.37.0 `children=` tree: a *grandchild* primary-key refusal now arrives
  under the child's relation as well as its own, which is that nesting rule
  applied to an error that was already namespaced once.

### Security

- **The primary-key refusal added in 0.37.0 now covers every relation kind.**
  It was wired into the child-collection loop, and the kinds added in this
  release reach the same create through a different path: a forward relation
  whose match key is absent from the payload walks past the match entirely, and
  a reverse one-to-one has no match key at all, so every payload primary key
  landed in the create. Both were reproduced before fixing. One blanked a
  column on another parent's row; the other reassigned and overwrote it.

  The check now sits in the single row writer every kind funnels through, ahead
  of any declared `create_service`, so a service is never handed a key either.

  No published release is affected: the four kinds are new here, and 0.37.0
  already covers `children=`, the only kind that shipped.

### Not included

- **Many-to-many through a `through` model.** The most design-heavy of the six
  kinds and a named follow-up rather than a decline. Declare a `ChildSpec` on the
  through model in the meantime; the nested-writes recipe shows it.


## [0.37.0] — 2026-08-11

### Security

**A nested write could reach a row the parent does not own.** `children=` matched
incoming rows against `getattr(parent, relation).all()`, which is correctly
scoped to the parent — but **only the match was scoped**. A row that failed to
match fell through to the create branch, which builds `Model(**item, fk=parent)`
and saves it, and Django turns a `save()` carrying an explicit primary key into
an **UPDATE**.

So a caller updating their own parent could name somebody else's child row by
primary key and have it **reassigned to them and its fields overwritten**. No
unusual payload was required — just a pk the caller does not own.

Reproduced end to end before fixing: two authors, one post; the attacker updates
their own author sending the victim's post pk, and the post comes back with
`author='attacker'` and the attacker's title, with no new row created.

**The fix.** A nested create that carries a primary key is refused with
`ServiceValidationError` (HTTP 400) naming the relation. That covers the key
under `match_key`, the same key routed through `field_map`, and a stray primary
key sitting beside a natural `match_key` — the hazard is the primary key
reaching the create, not which key was used to match.

**Behaviour change, and deliberate.** A payload carrying an unmatched primary
key previously created a row (in practice, silently overwrote one) and now
raises. A **natural** `match_key` is untouched: an unmatched value is still a
genuine create, so declaring one is still how a caller gets upsert semantics.

**Affects `children=` since 0.18.0.** Upgrade if you accept nested collections
from a client.

*Scoping a read does not scope the write that follows it.* The parent filter
made the match safe and said nothing about what the create branch was allowed to
touch — which is why every gate stayed green: nothing had a reason to ask what an
unmatched primary key does.


## [0.36.1] — 2026-08-11

### Fixed

- **The `filter` extra named a django-filter version that was never released.**
  The floor read `django-filter>=23.0`, and there is no 23.0 on PyPI — the
  series opens at 23.1. A constraint naming a nonexistent version is not a
  narrower claim, it is an unverifiable one: nothing can ever resolve to it, so
  the bound that was actually being exercised (23.1) was never the bound being
  promised. Corrected to `>=23.1`, the oldest release that exists and passes
  the suite. No consumer can have been resolving 23.0, so this tightens the
  text, not the behaviour.

- **The `spectacular` extra's floor was never once resolved.** The extra claims
  `drf-spectacular>=0.27`, but `drf-spectacular` is declared a second time in
  the `dev` group — which read `>=0.29.0` — and under
  `--resolution lowest-direct` the higher of two declarations of the same
  package wins. The floor job installs `--all-groups --all-extras`, so it
  resolved 0.29.0 every time and reported it as the floor: the 0.27 claim was
  checked by nothing. The dev floor is now aligned to the extra's, and 0.27.0
  has been run against the full suite, so the number in the extra is a number
  that has actually been tested. This is the masking the floor job's own
  comment warns about, found in its own configuration.

- **A test pinned the suite to a typing-extensions newer than the declared
  floor.** `test_spec_subscripts_are_all_or_nothing` matched the arity error on
  the literal string `"Too few arguments"`. That is upstream's wording, and
  upstream changed it: typing_extensions said `"Too few parameters"` through
  4.10.0 and `"Too few arguments"` from 4.11.0 on. So the suite could only run
  at >=4.11 while the package declares `typing-extensions>=4.6` — and the
  package is genuinely fine at 4.6, which the full suite now confirms. The
  assertion, not the code, was what made the declared floor unrunnable; it now
  accepts either noun, since what the test is about is the arity being rejected
  at all.

## [0.36.0] — 2026-08-11

### Fixed

- **`filter_data` now reaches the read path.** `dispatch_spec` has accepted the
  argument since the mutation views needed to keep a request body and a query
  string apart, but it only ever reached the *service* branch: a `SelectorSpec`'s
  `filter_set` read `params` regardless, silently. A caller that passed
  `filter_data` to a selector got no error and no effect.

- **`adispatch_spec` accepts `filter_data`.** It did not declare the parameter
  at all, so passing it raised `TypeError` — the same gap one layer further out.
  Neither surfaced because the only caller passing it was the sync mutation path.

  Together these made it impossible for an off-HTTP transport to hand the
  `FilterSet` a **wider** view of the arguments than the selector callable's own
  kwarg pool. Those two pools genuinely differ for an agent transport: a spec's
  `OrderingFilter` subclasses `ChoiceFilter`, so it is reflected into the tool
  schema and advertised to the model, while no selector declares `ordering` as a
  keyword argument. Without two pools a transport must either withhold the value
  from the filter (the schema then promises an ordering nothing applies) or
  spread it into the callable's kwargs (a selector taking `**kwargs` receives an
  argument it never declared).

## [0.35.0] — 2026-08-10

### Added

- **`unguarded_specs(specs)`** — names every spec whose `permission_classes` is
  `None`. On HTTP that means *inherit*, and the view's own classes plus
  `DEFAULT_PERMISSION_CLASSES` answer it; **off HTTP neither exists**, so the
  same spec that is properly guarded behind a view becomes an unguarded
  operation the moment a toolset or MCP server exposes it. The ambiguity is a
  drf-services fact, so the knowledge lives here — but the helper **raises
  nothing and defaults nothing**: each transport keeps its own flag, default and
  wording, exactly as `enforce_permissions` is offered while `dispatch_spec`
  stays authorization-agnostic. An empty list counts as guarded; `[]` is an
  answer, `None` is its absence.

- **`combine_progress(*reporters)`** — fans one report out to several sinks,
  isolating each. The `ProgressReporter` contract says implementations must not
  raise, which a plain loop breaks twice over: one sink throwing skips every
  sink behind it, and the exception lands in domain code that has no reason to
  defend against a telemetry call. Returns `null_progress` when nothing is
  configured, and the single reporter unwrapped when only one is.

- **`ServiceSpec.progress_reporter` / `SelectorSpec.progress_reporter`** — a
  transport-independent progress sink (a task record, an audit trail, metrics),
  resolved by the dispatch core and fanned together with whatever the transport
  supplied. A provider invoked through the keyword pool like `kwargs`; returning
  `None` declines without displacing the transport's own reporter.

  It is a provider rather than a bare reporter because the two are both plain
  callables and cannot be told apart — every other static-or-callable field here
  (`m2m`, `success_status`) unions two shapes a type check separates. A static
  sink is one lambda.

- **`get_progress_reporter()` / `get_<action>_progress_reporter()` view hooks** —
  the transport-native half on the HTTP path, since from the core's perspective
  a view *is* the transport. Most-specific wins with no merging (a reporter is
  one sink, not a set of keys); a hook returning `None` falls through rather
  than suppressing the view-wide sink. Resolved for selectors too — a large
  export is a selector.

  **Reach for this only when a buffered request genuinely needs it.** If a
  request runs long enough to want progress, a task plus polling is usually the
  right shape; this seam exists for the cases where that does not apply, such as
  a streaming response or a websocket sidecar the host already runs.

## [0.34.0] — 2026-08-05

### Added

- **`preconditions` on `ServiceSpec` and `SelectorSpec`** — a declared,
  pool-bound slot for state and database business rules, so a rule like "this
  budget is locked" rides the spec to every transport instead of settling in a
  view method. Consumer-requested.

  **One field, not the two that were asked for.** The request proposed `guards`
  (state, pre-resolution) and `validators` (payload coherence, post-validation).
  The split dissolves once the *position* is fixed: firing **after validation,
  after target resolution, immediately before the service** puts `data`,
  `serializer`, `instance` / `collection`, `user` and `request` in one pool, so a
  single field covers both categories. That is also the correct security
  ordering — permissions → resolution → validation → preconditions → service —
  so business logic never runs against an unvalidated payload.

  Named `preconditions` because `TargetGuard` / `on_target_resolved` already owns
  "guard" here for a different contract, and `validators` collides head-on with
  `Serializer.validators` while meaning something else.

  - **Raise to abort; the return value is ignored.** A predicate written
    `-> bool` returning `False` is silently a no-op — the most likely porting
    mistake, and the reason the recipe leads with it.
  - **Raise `ServiceError` / `ServiceValidationError`, not a DRF
    `APIException`.** Only the framework-agnostic errors are mapped by every
    transport; a DRF exception lands as a clean 409 over HTTP and an unhandled
    internal error over MCP or an agent toolset.
  - **Selectors** get one position (after target resolution), seeded `instance`
    for `RETRIEVE` and `collection` for `LIST` — so pool binding is what prevents
    an instance rule being attached to a list spec. **Bulk** (`many=True`) runs
    them once with no target, matching where the target guard fires.
  - **Sync-only, like every other auxiliary callable a spec carries.** Only
    `selector` and `service` may be `async def`; a precondition runs in the
    thread-sensitive executor on the async path, so a spec stays written once for
    both transports.
  - **Fail-fast at `as_view()`** on a bare callable passed instead of a sequence,
    a non-callable element (named by index), a parameter nothing seeds, and
    `preconditions` declared on a *nested* spec — which never dispatches, so the
    field would never run. Each of those is otherwise a 500 at request time.
    The pool is keyed on framework seed names, **not** model names:
    `def is_editable(order)` does not receive your `Order`.
  - Deliberately **not** reflected into tool schemas: a function's `__name__` /
    `__doc__` are the only machine-readable text on it, and deriving agent-facing
    schema from either would make a docstring edit a wire-format change. Use
    `spec.metadata`, which already exists for consumer-owned facts.

  Recipe: [State rules with `preconditions`](docs/recipes/preconditions.md).

## [0.33.0] — 2026-08-01

### Fixed

- **Client-supplied params could shadow the authenticated user during target
  resolution.** `instance_selector_spec` / `collection_selector_spec` built their
  kwarg pool by hand as `{**base_pool, **params, **url_kwargs}`, bypassing the
  reserved-seed strip that `merge_arguments` applies to every other spread. So
  `params={"user": …}` replaced the authenticated user in the pool that decides
  **which row gets mutated** and **which set gets bulk-deleted** — and over MCP
  or an agent toolset those params are the tool call itself. Both nested
  resolutions now strip the seeds, sync and async.

- **A route capture could shadow the authenticated user on the HTTP selector
  path.** The view-local pool spread `view.kwargs` **over** `base_pool`, so on a
  nested route like `/users/<user>/posts/` a capture named `user` replaced
  `request.user` in the selector's kwarg pool — a selector written
  `def list_posts(*, user): return Post.objects.filter(author=user)` scoped by
  the URL value instead of the caller. `RESERVED_POOL_SEEDS` exists to prevent
  exactly this (its docstring calls it a credential-spoofing footgun) and the
  transport-neutral path already stripped those names; the HTTP selector path
  never did. It requires a project to name a route capture after a dispatcher
  seed — `user` being the realistic one — but the result is a horizontal
  scoping bypass, so treat this as the reason to upgrade.

  **The permission fix can turn a passing 200 into a 403** if a project has a
  `has_object_permission` that was never being consulted on a retrieve selector.
  That is the intended behaviour and matches what the same spec already did over
  MCP, but it is worth checking before upgrading.

- **Four spec behaviours that differed between HTTP and `dispatch_spec`.** The
  view path and the transport-neutral path ran two copies of the same sequence,
  and the copies had drifted. Each of these was a spec-carried behaviour honoured
  on one transport and silently skipped on the other:

  - **`ServiceSpec.input_data` is now resolved off HTTP.** It was wired only into
    the view's hook chain, so a spec lifting a route capture into a serializer
    field validated fine over HTTP and failed as a missing required field over
    MCP or an agent toolset. Server-provided keys win over client input, as they
    already did over HTTP; on a `many=True` payload the merge applies per item.
  - **An `async def` service dispatched through sync `dispatch_spec` is now
    awaited.** `run_service` returned the coroutine object itself — no exception,
    and under `atomic=True` the transaction committed before the body would have
    run. The bridge now lives in `run_service` alongside the one `run_selector`
    already had, so both transports share it.
  - **The bulk path now honours the view's hook chains.** A spec that grew
    `many=True` (or a `collection_selector_spec`) silently stopped seeing
    `get_service_kwargs` / `get_<action>_service_kwargs` / `get_input_data` /
    `get_<action>_input_data` and the input serializer-context hooks that the
    single-instance path applies.
  - **Object-level permissions now run on a selector-resolved retrieve target.**
    A `SelectorSpec` replaces DRF's `get_object()`, which runs
    `check_object_permissions` itself — so `SelectorRetrieveMixin` and
    `SelectorRetrieveView` were skipping the object half of a spec's
    `permission_classes` entirely, while `enforce_permissions` enforced it off
    HTTP. The check now happens once, in `dispatch_selector_for_spec`, covering
    the retrieve mixin, the standalone retrieve view, and `instance_selector_spec`
    (which previously ran it separately). `LIST` is unaffected — a queryset is not
    an object, and scoping a list stays `filter_set` plus the selector.

- **The `get_output_serializer_context` chain now reaches the bulk renderer.**
  The fourth and quietest of the hook chains — it only surfaces when a spec both
  renders through an output serializer and reads view-supplied context.


### Changed

- **One dispatch pipeline.** The HTTP mutation path no longer runs its own copy
  of validate → pool → service → output selector → status. It resolves the view's
  hook chains, calls `dispatch_spec`, and renders the result; the pipeline in
  between is the same one MCP and every other transport uses. The internal
  `_execute_mutation` flow runner is gone.

  This is why the fixes above were possible as *fixes* rather than four separate
  ports: there is now one place for a spec behaviour to live. The rule that
  replaces the old convention: **a behaviour belonging to the spec belongs in the
  core** — put it in the view layer and it is honoured over HTTP and silently
  skipped everywhere else.

  **The selector path is converged too.** `dispatch_selector_for_spec` now
  resolves the view's `get_selector_kwargs` chain and hands off to the same core;
  its view-local pool is gone, which is what closes the seed-shadowing bug above.

  The one genuine difference between the transports is expressed as the policy it
  already was rather than as a second pipeline: over HTTP the call passes
  `argument_binding=BUNDLE`, so query params feed `filter_set` without becoming
  selector kwargs (off HTTP the flat `params` mapping *is* the argument channel
  and a selector spreads it). Both behaviours are pinned in
  `tests/test_transport_parity.py`.

  `dispatch_selector_for_spec` **loses** its `extra_url_kwargs` and
  `source_label` parameters. Every call site passed `view.kwargs` verbatim, and
  the core reads it itself while stripping reserved seeds — taking the mapping
  from the caller would reopen the bug above. If you call this helper from
  your own view (as `CLAUDE.md` suggests), drop both arguments.

  `resolve_mutation_instance` no longer resolves `instance_selector_spec`
  either; it returns `UNSET` and the core does it, which is why deleting
  `source_label` costs nothing — `dispatch_spec` already labels that failure
  `ServiceSpec.instance_selector_spec.selector`. The view's `get_object()`
  fallback is the one branch that stays, being genuinely HTTP-only.

### Added

- **`ViewHooks`** — the carrier a DRF view uses to hand its resolved hook-chain
  layers to `dispatch_spec`. It holds the *view* layers only; the spec's own
  providers stay the dispatch core's job, so they resolve exactly once. Callers
  without a view omit it and nothing changes.
- **`DispatchResult.service_result` / `.instance` / `.data`** — the service's own return value
  (captured before an `output_selector_spec` re-fetch replaced `value`), the
  resolved mutation target, and the validated input. `service_result` is the
  flags carrier a callable `success_status` and a `response_finalizer` key on;
  `instance` lets a transport read the pre-mutation row rather than resolving it
  a second time and risking a different answer. All three are informational — a
  transport that only renders can ignore them.
- **`dispatch_spec(instance=…, filter_data=…)`** — two seams the HTTP caller
  needs and no other transport does. `instance` supplies an already-resolved
  target (HTTP's `get_object()` chain has no off-HTTP meaning; the `UNSET`
  default means "resolve it yourself", since `None` is a valid supplied value on
  create). `filter_data` separates the filter source from `params` for the same
  reason the selector paths stay separate.

## [0.32.0] — 2026-07-31

### Added

- **`AdditionalInputRequired` — a service saying what it still needs.** Not
  "what you sent is wrong" (that is `ServiceValidationError`) but "I got far
  enough to discover I need something else", which is usually conditional on
  what the service found and so cannot be a required field on the serializer:

  ```python
  def delete_rows(*, data):
      doomed = rows_matching(data)
      if len(doomed) > 100 and not data["confirmed"]:
          raise AdditionalInputRequired(
              f"{len(doomed)} rows match. Confirm to proceed.",
              schema={"confirmed": {"type": "boolean"}},
          )
  ```

  `schema` describes what is missing, keyed by the input name the service wants
  it back under. **The answer returns as ordinary input on every transport** —
  an HTTP client re-submits with the field, and a transport that can ask
  interactively merges the answer into the parameters before dispatch. Either
  way the service reads it as a normal argument, which is why raising is the
  whole of its involvement: no callback to hold, no session to resume.

  A `ServiceError` subclass, so a transport that has never heard of it still
  does something sensible. One that can ask catches it first — note the
  ordering, since a handler for `ServiceError` will otherwise swallow it.

  **Scoped down deliberately.** A first draft put the whole interaction here:
  a callable pool seed, an accept / decline / cancel result type, and a contract
  in which the service is re-executed from the top on retry. That was one
  protocol's interaction model (MCP's multi-round-trip requests) wearing a
  generic name. The tell was that its pool seed had to *raise* by default, where
  every other seed can be a no-op — a seed is safe to declare anywhere precisely
  because a transport that cannot honour it may drop it. What ships is the part
  that is genuinely transport-neutral; each transport owns how it asks.

### Fixed

- **OPTIONS to any spec-backed viewset raised `AttributeError` and returned an
  unhandled 500.** `_ActionSpecsMixin.get_permissions` assumed `self.action`
  always names a bound handler, but DRF sets `self.action = "metadata"` for
  OPTIONS — an action its own source calls implicit, with no method behind it.
  The undefaulted `getattr` raised before any permission was evaluated, and an
  `AttributeError` is not an `APIException`, so `handle_exception` re-raised it.
  Affected `ServiceViewSet` and `SelectorViewSet` alike, with or without
  `action_specs`. Present since 24bc37d.

  **Wider than explicit OPTIONS calls.** A CORS preflight *is* an OPTIONS
  request, and `django-cors-headers` only short-circuits paths matching
  `CORS_URLS_REGEX` that also carry `Access-Control-Request-Method` — every other
  route reached the view. Easy to miss in development and easy to hit in
  production on a subset of routes.

  Fail-closed, so an availability defect rather than an authorization bypass:
  the request died before permissions ran, it did not skip them.

  The `getattr` now defaults, rather than special-casing `"metadata"` — the
  branch was unsafe for *any* action with no attribute behind it, and an action
  naming no handler has no decorator-attached spec by definition. OPTIONS falls
  through to the view's own `permission_classes`, which is DRF's own behaviour.
  The suite had no OPTIONS coverage at all; it does now, including a plain DRF
  viewset as a control.

  Unrelated to 0.31's consumer-owned `metadata` mapping on specs, despite the
  shared word — that is a field this framework never reads, and this is DRF's
  implicit action name.

## [0.31.0] — 2026-07-31

### Added

- **`metadata` — a consumer-owned mapping on `SelectorSpec` and `ServiceSpec`.**
  Attach your own per-operation facts to the spec that describes the operation,
  and read them back from your own code:

  ```python
  class SameTenant(BasePermission):
      def has_permission(self, request, view):
          spec = view.action_specs[view.action]
          return (spec.metadata or {}).get("scope") != "tenant" or request.user.tenant_id


  ServiceSpec(service=refund_order, permission_classes=[SameTenant], metadata={"scope": "tenant"})
  ```

  **The framework never reads it.** No known keys, no per-key validation, no
  defaulting, and no effect on the generated JSON Schema or OpenAPI — pinned by
  tests asserting both phases are identical with and without it. Validation is
  shape-only: a non-mapping raises `ImproperlyConfigured` at construction (not
  at `as_view()` like every other spec field, because the field's whole point is
  to be read on paths that never mount a view — a registry consumer,
  `dispatch_spec`, an MCP binding).

  It lives on the spec rather than on `RegisteredSpec` because a DRF permission
  class receives `(request, view)`: it holds the spec but knows no registry and
  no name, so registry-side metadata would be unreachable from the place that
  needs it. `RegisteredSpec.tags` keeps its job — boolean-ish labels every
  transport interprets; `metadata` carries structured facts only your code does.

  It never merges or inherits: a `ServiceSpec` and its `output_selector_spec`
  hold independent metadata, and `PolymorphicServiceSpec` deliberately has no
  field of its own (declare it on the variants). The mapping is stored as given
  — neither copied nor frozen. Purely additive; omitting it changes nothing.

## [0.30.0] — 2026-07-31

### Added

- **`progress` — a reserved pool seed for long-running services.** A service or
  selector that declares `progress` receives a `ProgressReporter` and calls it
  as the work advances:

  ```python
  def export_invoices(*, data, progress):
      rows = list(build_rows(data))
      for index, row in enumerate(rows):
          write(row)
          progress(index + 1, total=len(rows), message="writing rows")
  ```

  **The no-op default is the load-bearing part.** Every transport seeds a
  reporter, and the ones with nowhere to send progress seed `null_progress` —
  so the service above runs unchanged over HTTP, off-HTTP and in tests. Without
  that, declaring the parameter would work over one transport and raise a
  `TypeError` over the next, and nobody could put it in code meant to be shared,
  which is the entire premise of writing a service once.

  A transport that *can* forward progress passes its own:
  `dispatch_spec(spec, ..., progress=reporter)` (and the async twin). Only the
  primary callable gets it — target resolution and the output re-fetch take the
  no-op, since there is nothing to report from a lookup, and a live reporter
  there would let the output selector emit progress *after* the service
  finished, which to a watching client reads as the work having restarted.

  `message` is prose for a person; **`meta` carries structured detail** for a
  machine on the far end — which stage, which file, how many rows failed.
  Without that slot the structure ends up in `message` anyway, stringified by
  the service and parsed back out at the sink: a wire format invented by
  accident inside a field documented as being for humans. Each receiver decides
  what to do with it — a websocket consumer forwards it into the frame its UI
  renders, one with nowhere to put it drops it — so it is telemetry, never a
  channel the operation's correctness depends on. Namespace the keys if the
  far end might be MCP: a progress notification carries them under the
  protocol's `_meta`, which reserves unprefixed names.

  **Supply one from wherever the dispatch starts.** A Celery task, a management
  command or an alternate transport passes `progress=` to `dispatch_spec`
  directly; an HTTP view returns one from the `get_service_kwargs()` /
  `get_selector_kwargs()` hook it already uses for tenants and clocks. Extras
  merge over the seeds, so a server-authored reporter replaces the no-op —
  while client *input* named `progress` cannot, being a reserved seed. That
  asymmetry is what makes the hook safe.

  **The reporter is sync**, even when the sink is not — it is called from
  domain code that is written once for both transports and so is never
  `async def`. Bridge an async sink (a Channels `group_send`) at the reporter
  with `async_to_sync`, not by pushing async into the service.

  Prompted by the MCP transport's `notifications/progress`, which had no way to
  learn how far a tool had got — but nothing here is MCP-shaped: a workflow
  model tracking a Celery job and pushing to a websocket is the same feature.

- **`base_pool` is now public**, alongside `base_serializer_context` and for the
  same reason: it is the baseline a transport composes rather than restates.
  Build your pool from it if you assemble one.

### Changed

- **`RESERVED_POOL_SEEDS` gains `"progress"`.** A caller-supplied argument of
  that name is stripped from the `SPREAD_*` bindings, as `request` / `user` /
  `data` already are — the value is the dispatcher's, and letting client input
  reach a parameter the service trusts is exactly what the reserved set exists
  to prevent. A `UrlKwarg` or `QueryParam` named `progress` is now refused at
  registration.

- **The two HTTP pools now route through `base_pool`** instead of restating
  `request` / `user` inline. Behaviourally identical for those two seeds, and it
  is what stops the HTTP and off-HTTP paths drifting on what a callable may
  declare — the drift this release would otherwise have introduced.

## [0.29.1] — 2026-07-29

### Fixed

- **`KeyError: 'SERVER_NAME'` from any serializer with a `FileField`, off the
  HTTP path.** A 0.29.0 regression. That release started putting `request` in the
  serializer context off HTTP — which is what makes `request.user` and friends
  work — but the synthesized request was a bare `HttpRequest` with an **empty**
  `META`, and Django resolves the host from `HTTP_HOST` / `SERVER_NAME`. So
  `build_absolute_uri()` raised, and DRF's `FileField.to_representation` calls it
  for every file value whenever a request is present. Before 0.29.0 the same
  field saw no request at all and returned a relative URL: supplying a hostless
  request was strictly worse than supplying none.

  The synthesized request is now an `OfflineHttpRequest`, which returns the
  **relative** URL instead of raising when no host is configured — the same shape
  DRF's own file / hyperlinked fields fall back to, so it restores the
  pre-0.29.0 output. Transports that pass a real `http_request` (the MCP server)
  were never affected.

### Added

- **`build_offline_context(host=…)` — an origin for absolute URLs off HTTP.**
  Accepts `"example.com"`, `"example.com:8000"`, or a full origin like
  `"https://example.com"` (the scheme decides whether links are https). With it,
  `build_absolute_uri()` builds real absolute URLs from a toolset, a management
  command, or a worker.

  There is deliberately no default. Only the project knows its public origin, and
  inferring one — the first `ALLOWED_HOSTS` entry, say, which is an
  *authorization* list and is routinely a wildcard or an internal load-balancer
  name — would emit confidently-wrong links that look valid. The value is
  **not** validated against `ALLOWED_HOSTS` either: that check exists to reject
  spoofed `Host` headers from untrusted clients, and there is no client here, so
  a worker can render links for a site it doesn't itself serve. `host` is ignored
  when `http_request` is supplied, so a caller may pass both unconditionally.
- **`OfflineHttpRequest`** — the synthetic request type, exported so its
  behaviour is documented rather than implied.

## [0.29.0] — 2026-07-29

### Added

- **`arender_spec_output` — the async sibling of `render_spec_output`.** Rendering
  is full of sync ORM work that isn't optional: `serializer.data` iterates the
  value (evaluating the queryset when `many=True`, plus a query per untraversed
  relation per row), the `output_serializer_context` provider is user code
  documented as the place to run one batched query, and the no-serializer path
  list-coerces. An async caller that awaited `adispatch_spec` — which returns a
  `LIST` result as a deliberately **lazy** queryset — therefore could not render
  it inline without raising `SynchronousOnlyOperation`, and had to know to wrap
  the call itself. The dispatch surface now offers the pair on both halves:
  `dispatch_spec` / `adispatch_spec` and `render_spec_output` /
  `arender_spec_output`. Same arguments, same result, executor instead of loop.

### Fixed

- **`adispatch_spec` ran a spec's sync callables on the event loop, so any of
  them that touched the ORM raised `SynchronousOnlyOperation`.** A spec is
  written once and dispatched over both transports, so none of its callables are
  `async def` — the async path has to offload each one. It offloaded the
  selector / service (`arun_callable`), the permission guard, and input-serializer
  validation, but called the rest inline:

  - **`shape_queryset`** — and with it `extend_queryset` and `filter_set`. A
    `filter_<name>` method that queries, or a `ModelChoiceFilter` whose
    `is_valid()` resolves its choice against the DB, raised. Reported from the
    MCP transport, which awaits `adispatch_spec` directly on the loop.
  - **`spec.kwargs` providers** — including the nested `instance_selector_spec`
    / `collection_selector_spec` ones. The documented headline use of a provider
    is a tenant / role lookup, which is a query.
  - **`input_serializer_context` providers** — documented as the place to run
    one batched query for the whole payload.
  - **callable `success_status`** — user code that may read a relation off the
    result.

  All four now go through the executor, thread-sensitive like the calls that
  already did, so they share one connection and see the same transaction state
  as on the sync path. Sync dispatch is unaffected — it was never on a loop.
- **Off-HTTP dispatch supplied no baseline serializer context, so
  `self.context["request"]` raised `KeyError`.** Over HTTP every serializer DRF
  builds carries `GenericAPIView.get_serializer_context()` — `request` /
  `format` / `view` — so serializers read those keys unguarded
  (`build_absolute_uri`, `request.user`, an ownership check in a
  `SerializerMethodField`). `render_spec_output` and `dispatch_spec`'s input
  validation passed the spec's `*_serializer_context` provider result *instead
  of* that baseline, and nothing at all when no provider was declared — so the
  same serializer that renders behind a view raised `KeyError: 'request'` when
  the spec was dispatched from the MCP server, a Pydantic-AI toolset, or a
  management command. Both directions now start from the DRF baseline (the new
  `base_serializer_context`) and merge the spec's provider **over** it, so a
  provider keeps the final say on every key. Off HTTP the values are the
  synthetic pair `build_offline_context` builds — `request.user` is the `user`
  you passed it — and `format` is `None`. The HTTP bulk path (which renders
  through `render_spec_output`) gains the same baseline, from the real view's
  `get_serializer_context()`.

### Added

- **`base_serializer_context`** — the DRF baseline context, exported for
  transports that build a serializer outside `dispatch_spec` /
  `render_spec_output` (as `djangorestframework-mcp-server` does for its
  selector, chain, and resource renderers). Prefers `view.get_serializer_context()`
  when the view has it, else synthesizes `{"request", "format": None, "view"}`.

## [0.28.1] — 2026-07-28

### Fixed

- **`validate_channel_names` was uncallable from a type-checked adapter.** Its
  internal declaration Protocol declared bare attributes (`name: str`), which
  declare *mutable* members — and both `UrlKwarg` and `QueryParam` are
  `@dataclass(frozen=True)`, whose fields are read-only. Every adapter passing
  its own tuple failed type-checking at the call site. The Protocol members are
  now read-only properties. Runtime behaviour is unchanged; this was visible only
  to a type checker, and only from a consumer — `ty` is scoped to the package
  here and skips `tests/`, so the package's own suite could not see it.

## [0.28.0] — 2026-07-28

### Added

- **`InputRequired` — declare a reflected input required without breaking Protocol
  conformance.** Since 0.26.0 a selector's `**extras: Unpack[SomeExtras]` keys are
  reflected into the input schema, and a *required* `TypedDict` key populates the
  schema's `required` list. In practice no consumer could use that: under
  [PEP 692](https://peps.python.org/pep-0692/) a required key in `Unpack[...]`
  makes the callable reject callers that omit it, so it stops being assignable to
  `ListSelector` / `RetrieveSelector` / the service Protocols — which is exactly
  why `HttpExtras` mandates `total=False`. Protocol conformance and an honest
  schema were mutually exclusive. `InputRequired` carries the signal in
  `Annotated` metadata, which has no effect on the type system:

  ```python
  class WidgetExtras(HttpExtras[MyUser], total=False):
      project_pk: Annotated[int, InputRequired]
  ```

  The key stays `NotRequired` to the type checker and joins the schema's
  `required` list. Applies to ordinary parameters as well as `TypedDict` keys.
- **`NotClientInput` — hide a provider-owned input from the schema.** The
  counterpart: a key a `spec.kwargs` provider fills in from request state (a
  scoping `team_role`) is dropped from `properties` entirely, so a caller is never
  told it exists. Such a key is also excluded from `declared_input_keys`, so
  `UnknownArguments.REJECT` treats a caller that supplies it as passing an unknown
  argument. Delivery is unchanged — the provider still resolves it, and the
  `SPREAD_AUTHOR_WINS` precedence remains the actual security property. Marking a
  key with both markers raises `ImproperlyConfigured`.
- **`UrlKwarg` / `QueryParam` — the shared transport-channel declarations.**
  Previously each adapter (`djangorestframework-mcp-server`,
  `djangorestframework-pydantic-ai`) carried its own copy, and the copies had
  already drifted into validating the same declaration against different
  reserved-name sets — `UrlKwarg("order")` was legal over MCP and rejected in the
  agent toolset, `UrlKwarg("user")` the reverse. One definition now lives here.
  `UrlKwarg` gains **`required: bool = False`**, the registered-declaration
  counterpart of the `InputRequired` marker. `QueryParam` deliberately has no
  `required` — a read-shaping param is optional by construction.
- **`validate_channel_names`** — shared fail-fast validation for a set of
  `UrlKwarg` / `QueryParam` declarations: reserved-name collisions (always
  including `RESERVED_POOL_SEEDS`, plus the transport's own pagination names),
  duplicates, and the contradictory `required=True` with a `default`.
- **`RESERVED_POOL_SEEDS` is now public** (`rest_framework_services.types`).
  Adapters need the same list — anything letting a caller route a value into the
  kwargs pool must refuse these names — and were keeping local copies of it.

### Changed

- **`dispatch_spec` / `adispatch_spec` now enforce `InputRequired`.** A marked key
  absent from every channel (`params`, `build_offline_context(kwargs=…)`, a
  `spec.kwargs` provider) raises `ServiceValidationError` before the callable
  runs. Previously the callable raised a bare `KeyError` from inside dispatch,
  which no transport maps: over MCP that surfaced as a 500 / JSON-RPC internal
  error rather than a failed tool result, and under an agent toolset it aborted
  the run instead of letting the model retry with the argument it omitted. A
  provider that declines with `UNSET` does **not** satisfy the requirement —
  declining removes the key from the pool. Enforcement is off-HTTP only: on the
  HTTP path the route is the guarantee, and a missing capture is a URLconf bug.
  Behaviour change, though it can only fire on specs that opt in by using the
  new marker.

### Fixed

- **`Annotated[...]` no longer erases a property's type.** `_python_type_to_schema`
  did not strip the wrapper, so `Annotated[int, "help text"]` reflected as an
  untyped `{}` where bare `int` gives `{"type": "integer"}`. Any consumer already
  using `Annotated` in an extras `TypedDict` or a parameter annotation was
  silently losing the type. Independent of the markers, which merely made the
  idiom load-bearing.

## [0.27.0] — 2026-07-27

### Added

- **`SpecRegistry` — one declaration site for a project's spec set.** A spec is
  already transport-neutral, but every transport has to be *told* which specs it
  exposes, so a project serving two or three of them writes the same list two or
  three times (`register_service_tool(...)`, `SpecToolset(specs={...})`, an
  HTTP viewset's `action_specs`). Those lists drift, and the same operation can
  end up named differently in each. `SpecRegistry` is a `name → spec` map with
  tags, registration order, and filtered views, which each transport reads
  instead of enumerating specs again. It holds only what is **invariant** across
  transports — which spec, its canonical name, its tags; per-transport knobs stay
  at the binding that already owns them, so a registry is a *source* for a
  transport's binding table rather than a layer between a caller and
  `dispatch_spec`.
  - **Adoption needs no adapter support**: `registry.specs()` returns the
    `dict[str, spec]` today's adapters already accept.
  - **There is no global registry.** The consumer owns its instances and may hold
    as many as it likes, with no shared state between them — either **independent
    registries** (separate name namespaces, one per mount) or **one registry with
    many projections** (`by_tag` / `subset` views feeding several bindings). Names
    are unique *within* a registry; `merge()` is the only place cross-registry
    reuse becomes a conflict.
  - **Derivations are snapshots.** `by_tag` / `subset` / `merge` each return a
    **new** registry holding the selected entries (spec objects shared, not
    copied), so a view never mutates its source and a later `register()` on the
    source does not appear in a view derived earlier. `by_tag` matches **any** of
    the tags given, so intersection composes from it
    (`reg.by_tag("read").by_tag("public")`) while a union would not compose from
    an intersection.
  - **Failures are loud, not silent.** A duplicate name raises instead of
    overwriting; `subset()` raises on a name that isn't registered; and
    `register()` rejects anything that is neither a `ServiceSpec` nor a
    `SelectorSpec` — which is what lets `mutations()` / `queries()` discriminate
    by type (never a stored flag that could drift) and still cover every entry. A
    `PolymorphicServiceSpec` is rejected with a pointer at the supported shape:
    register each variant under its own name, since a transport that projects one
    operation per name wants one operation per variant, not a union.
  - New public symbols: `SpecRegistry` (in `rest_framework_services.registry`)
    and its entry type `RegisteredSpec` (in `rest_framework_services.types`),
    both re-exported from the top-level package. Additive and opt-in — nothing
    existing changes, and every adapter keeps accepting a plain dict. See the
    recipe *Declare specs once, project them to many transports*.

### Removed

- **`rest_framework_services.conf` — the `REST_FRAMEWORK_SERVICES` settings dict
  and its `ATOMIC_BY_DEFAULT` key.** Nothing in the package ever imported this
  module, so the setting did nothing: the atomic default is the `ServiceSpec.atomic`
  field (`True`), and the supported opt-out is per-spec `atomic=False`. Setting
  `REST_FRAMEWORK_SERVICES = {"ATOMIC_BY_DEFAULT": False}` silently had no effect,
  which is worse than not offering it — removed rather than wired up, since the
  per-spec knob already covers the case and is what the docs teach.

### Documentation

- **`ServiceSpec` parameterisation examples were missing their third type
  argument.** `ServiceSpec` is `Generic[InputT, ResultT, ExtraT]`, but the README,
  the docs landing page, and the Typing guide all showed the two-argument form
  `ServiceSpec[AuthorIn, Author]` — which raises `TypeError` at runtime, since
  these are plain `TypeVar`s with no PEP 696 defaults. Corrected, with a note that
  it is all-or-nothing and `Any` is the right `ExtraT` for a spec with no
  `kwargs=` provider.
- **`filter_set`'s 400-on-invalid-filter contract is now documented.** Rejecting
  invalid filter input (rather than silently returning unfiltered rows, which is
  django-filter's default non-strict behaviour) is the half of "replaces
  `DjangoFilterBackend`" that most affects callers, and it appeared in neither the
  filtering recipe nor the `SelectorSpec.filter_set` reference. Both now cover it,
  including that it only applies when the duck-typed object exposes `is_valid`.
- **`PolymorphicServiceSpec` gained a reference entry.** It was the only public
  symbol with no autodoc entry on any reference page.

## [0.26.0] — 2026-07-24

### Added

- **URL-kwarg & provider-state parity over the off-HTTP path.** A spec
  that works over HTTP is now equally usable off-HTTP (`build_offline_context`
  `+` `dispatch_spec` — the path under every MCP tool, Pydantic-AI tool,
  management command, and task runner), closing two gaps where a spec depended on
  values that only existed on the HTTP transport:
  - **`Unpack[TypedDict]` extras are now reflected and discoverable.** A selector
    whose keyword surface is the blessed strict-typing idiom
    `**extras: Unpack[SomeExtras]` (see `HttpExtras`) previously contributed
    nothing to `spec_to_json_schema(phase="input")`, so a schema-driven caller
    (an LLM, an MCP client) could not learn that a URL kwarg the selector reads
    from its extras (`extras["parent_pk"]`) even existed — the call failed with a
    bare `KeyError`. The schema now **expands** the `TypedDict`: one property per
    key, with the `TypedDict`'s required keys populating `required`. The inherited
    `request` / `user` seeds (from `HttpExtras`) are excluded from the expansion,
    never advertised as tool inputs. Required-key detection is robust under
    `from __future__ import annotations` (PEP 563), where the raw
    `__required_keys__` misclassifies `NotRequired` fields.
  - **`build_offline_context(kwargs=…)` is now *the* channel for URL-derived
    values, and it actually delivers.** `dispatch_spec` / `adispatch_spec` spread
    the offline view's `kwargs` (a nested route's captures, e.g. `parent_pk`)
    into the selector and target-resolution pools — the off-HTTP counterpart of
    the HTTP `extra_url_kwargs=view.kwargs`. Route captures are **authoritative
    over `params`** on a key conflict (matching HTTP, where a route scope
    out-ranks client input) and sit **below a `spec.kwargs` provider** (also
    matching HTTP). Reserved pool seeds are stripped from the view kwargs.
    Empty by default → no behaviour change for callers that pass no `kwargs`.
- **`spec.kwargs` / provider hooks can decline a key with `UNSET`.** A provider
  that returns `UNSET` for a key is *declining* to set it (not setting it to
  `UNSET`); the key is dropped from the resolved kwargs on both transports
  (`resolve_provider` off-HTTP, the `get_*_kwargs` / `input_data` / context
  hook chain on HTTP). This lets a provider that can't resolve a value off-HTTP
  (middleware state absent on the synthetic request) step aside so a
  caller-supplied `params` value survives the `SPREAD_AUTHOR_WINS` merge, instead
  of a fallback `None` silently over-scoping the result. Declining is for
  *benign* keys only: a provider that owns a **scoping** key must always resolve
  it (off-HTTP, from `view.kwargs`), since declining there would let the caller's
  value through.

### Changed

- **`UnknownArguments.REJECT` now enforces on a `**extras: Unpack[TypedDict]`
  selector** (was previously a silent no-op). Because such a surface was treated
  as *open* (like a bare `**kwargs`), `REJECT` had nothing to reject and a URL
  kwarg was deliverable only by that accident of openness. The `TypedDict` now
  names the exact declared keyword surface, so `REJECT` **accepts** those keys
  and **rejects** genuinely unknown ones — the same contract every other closed
  spec already had. A bare / `Any` `**kwargs` selector stays open, unchanged.

## [0.25.1] — 2026-07-17

### Fixed

- **`SelectorSpec.filter_set` now receives the `request`, matching
  `DjangoFilterBackend`.** A `django-filter` `FilterSet` used as `filter_set` was
  constructed `filter_set(data=…, queryset=…)` with no `request`, so any FilterSet
  that read `self.request` — caller scoping via `self.request.user`, a request-aware
  `ModelChoiceFilter(queryset=lambda request: …)`, an `__init__` / `qs` override —
  saw `self.request is None` and raised `AttributeError` (a 500) once moved off a
  view's `DjangoFilterBackend` onto a spec. The dispatcher now forwards the
  `request` into the FilterSet whenever its constructor declares one
  (signature-gated, so the bare `(data, queryset) -> .qs` duck-typed contract is
  untouched). The request is the same object every other queryset-shaping hook
  already receives — real on the HTTP / MCP paths, a synthetic off-HTTP one whose
  `user` and `query_params` are faithful. See `docs/recipes/selector-filtering.md`.

## [0.25.0] — 2026-07-16

### Added

- **`PolymorphicServiceSpec` — one action, N mutually exclusive payload shapes.**
  A new spec accepted anywhere a `ServiceSpec` is (an `action_specs` entry and
  the `spec=` of `@service_action`). It bundles a `discriminator` callable —
  resolved through the keyword pool `{request, data, user, view}` and returning a
  variant key — with a `specs` mapping of full `ServiceSpec` variants, each with
  its own input serializer and service. Dispatch resolves the key (memoized once
  per request) and proceeds through the chosen spec exactly as today; a rejected
  payload is the discriminator's to raise on (`ServiceValidationError` → 400).
  `permission_strategy` (`"union"` default / `"discriminate"` /
  `"require_identical"`) controls how `get_permissions` treats the variants,
  since DRF runs permissions before the body is parsed — `"union"` requires the
  deduplicated union of every variant's `permission_classes` (the conservative
  default), `"discriminate"` reads the body early and applies only the chosen
  variant's, `"require_identical"` is validated to require matching classes. The
  resolved concrete spec flows through the shared action→spec chain, so the
  chosen variant's serializer context / kwargs / output pipeline all apply.
  OpenAPI renders the request body as the union of the variant input serializers
  (`PolymorphicProxySerializer`, `resource_type_field_name=None`).
- **`ServiceSpec.success_status` may now be a callable.** In addition to an
  `int` or `None`, it accepts a callable resolved through the framework keyword
  pool — declaring any subset of `result` / `instance` / `request` / `view`
  (or `**kwargs`) — that returns the status code. The callable keys on the
  *service's* return value (`result`), so an upsert can answer `201` for a
  freshly created row and `200` for an existing one without a hand-rolled action
  method. `None` still applies each surface's action-appropriate default (201
  create / 200 update / 204 destroy), and a plain `int` is unchanged. Resolved
  uniformly across the viewset mixins, standalone views, `@service_action`, and
  the transport-neutral `dispatch_spec` / `adispatch_spec` paths. A callable
  can't be resolved statically, so the generated OpenAPI schema documents the
  action default for the dynamic case. `@service_action` now resolves the
  status per-request (previously it was fixed at decoration time), which is what
  makes the callable form work on custom actions.
- **`call_service(..., map_errors=True)`** (and its async twin
  `acall_service`) translates a `ServiceError` raised by
  the delegated service into the same DRF exception the framework raises on the
  normal view path — `ServiceValidationError` → `ValidationError` (400), any
  other `ServiceError` → 422 — so DRF's exception handler renders it as a proper
  response instead of a 500. The default (`map_errors=False`) still propagates
  the raw `ServiceError` unchanged. Chosen over a top-level `map_service_error`
  export so the HTTP-to-error mapping stays a `call_service` concern (the helper
  is already HTTP-scoped). Internally `map_service_error` moved to its own leaf
  module `views/mutation/map_service_error.py` so `call_service` can reuse it
  without importing the heavy mutation-flow module (no public-API change).
- **`ServiceSpec.response_finalizer`** — a post-serialization hook for HTTP
  response side effects (cookies, headers, or swapping the response). It runs on
  the **2xx path only**, after the output serializer has built the `Response`
  and before it is returned (pre-render); error paths bypass it. Resolved through
  the framework keyword pool, it declares any subset of `response` / `result` /
  `request` / `view` / `instance` / `data` (or `**kwargs`) and returns a
  `Response` (which replaces the built one) or `None` (which keeps it) — so
  `lambda *, response: response.set_cookie(...) or response` attaches a cookie.
  `result` is the *service's* return value, so the idiomatic pattern keeps
  services DRF-free (the service returns domain flags on its result DTO; the
  finalizer translates flags → transport effects). Applies to both the single
  and bulk HTTP flows; **skipped on the transport-neutral path**
  (`dispatch_spec` / `call_service` / MCP), which builds no `Response`. Unlike
  the service/selector pool it deliberately receives `view`. `@service_action`
  forwards it automatically.

### Fixed

- **`collection_selector_spec` selectors now receive the view's URL kwargs.**
  On the HTTP bulk path a `collection_selector_spec` selector was resolved with
  a pool of `{user, request}` + query params + body but **not** the route's URL
  kwargs, unlike the instance / retrieve selectors (which get
  `extra_url_kwargs=view.kwargs`). A nested-route bulk such as
  `/parents/{parent_pk}/children/` therefore couldn't scope by `parent_pk`
  without an extra `kwargs` provider. The bulk view now folds `view.kwargs` into
  the flat `params` mapping it hands `dispatch_spec` (whose contract already
  documents that mapping as the union of `request.data` / `query_params` / URL
  kwargs), so a selector like `lambda *, parent_pk: Child.objects.filter(...)`
  resolves from the route. Route captures are authoritative — they win over a
  client-supplied query/body key of the same name, so a filter value can't
  override the route scope. The transport-neutral `dispatch_spec` path is
  unchanged (callers already pass URL kwargs in `params`).

## [0.24.1] — 2026-07-13

### Fixed

- **`SelectorSpec.filter_set` now validates the FilterSet before filtering.**
  `apply_queryset_shaping` read `.qs` without calling `is_valid()`, so in
  django-filter's default non-strict mode an invalid filter value (e.g. a
  `ChoiceFilter` value outside its choices) silently returned the *unfiltered*
  queryset — answering `200` with unfiltered results instead of the `400`
  DRF's `DjangoFilterBackend` gives by default. The FilterSet is now validated
  and a `ValidationError` (400) is raised on invalid input, restoring parity
  with the backend `filter_set` replaces. Validation is gated on the FilterSet
  exposing `is_valid()`, so a bare duck-typed `(data, queryset) -> .qs`
  stand-in keeps its pass-through behaviour; valid and absent filter values are
  unchanged. Applies on both the HTTP view path and the transport-neutral
  `dispatch_spec` path.
- **`input_data` no longer corrupts form-encoded / multipart request bodies.**
  When a spec had an `input_data` provider, `build_input_serializer` merged the
  extras with `{**request.data, **extra_data}`. For a form-encoded body
  `request.data` is a `QueryDict` (`{key: [values]}` internally), so unpacking
  it exposed those value lists — turning every scalar field into a one-element
  list and failing validation (a `ChoiceField` saw `['X']` → `invalid_choice`).
  The merge now copies the QueryDict and sets extras through its native API, so
  scalars stay scalars and multi-value fields keep their lists. JSON bodies
  (plain `dict`) are unaffected.

## [0.24.0] — 2026-07-08

### Added

- **Selector input schemas now reflect the selector callable's parameters.**
  `spec_to_json_schema(spec, phase="input")` for a `SelectorSpec`
  previously introspected only `spec.filter_set`, so a lookup selector like
  `get_widget(user, pk)` advertised a bare `{"type": "object"}` with no `pk` — the
  tool leaned entirely on its docstring. It now merges the callable's own
  parameters (names → JSON type from their annotations, skipping the `request` /
  `user` / `view` transport seeds) with the `filter_set` fields. An un-annotated
  parameter is still surfaced by name (untyped `{}`); a `filter_set` field wins
  over a callable parameter of the same name. Consumers building tool definitions
  off the schema (`djangorestframework-pydantic-ai`, the MCP server) inherit the
  fidelity for free — no code change on their side.

## [0.23.0] — 2026-07-08

### Added

- **`build_offline_context(query_params=…)`** — seed the synthetic request's
  `GET` `QueryDict` (the source `request.query_params` reads) when dispatching a
  spec off-HTTP. This is how read-shaping params that aren't spec inputs reach the
  serializer over the offline path: `SelectorSpec.filter_set` (when not handed
  `filter_data` another way), and any serializer that branches on
  `request.query_params` (django-restql field selection, custom serializers).
  Scalars are stringified as on HTTP; a list/tuple value becomes a multi-valued
  param (`getlist`); the seeded `GET` is immutable like a real request's. Defaults
  to empty → no behaviour change for existing callers. (The seam
  `djangorestframework-pydantic-ai`'s `SpecToolset` builds request-level param
  registration on.) It does **not** make DRF `filter_backends`
  (`SearchFilter`/`OrderingFilter`) run off-HTTP — the offline path never calls
  `filter_queryset`.

## [0.22.0] — 2026-07-03

### Fixed

- **`ServiceAutoSchema` now emits OpenAPI query parameters for a
  `SelectorSpec.filter_set`.** Moving a `django-filter` FilterSet off a
  view-level `filterset_class` + `DjangoFilterBackend` and onto `spec.filter_set`
  (which the `as_view()` guard requires dropping the backend for) previously
  dropped every filter query parameter — field filters, ordering, search — from
  the generated schema, silently regressing any client codegen or
  "schema must not change" gate even though runtime behaviour was unchanged. The
  schema now contributes those parameters from the spec by reusing
  drf-spectacular's own `DjangoFilterExtension`, so a view→spec FilterSet move
  yields a byte-identical OpenAPI document — same param names, types, enums,
  ordering enum + description, `style`/`explode`, and required flags. Applies to
  list selectors (detail operations document no filter params in either
  configuration, matching drf-spectacular's list-only gate);
  `@extend_schema(parameters=...)` overrides still win. Introspection is guarded
  and duck-typed — a no-op when `django-filter` isn't installed or the
  `filter_set` isn't a real FilterSet.

## [0.21.1] — 2026-07-02

### Fixed

- **`many=True` dispatch now honours `unknown_arguments` per list element.**
  `dispatch_spec` / `adispatch_spec` previously accepted
  `unknown_arguments` on a bulk (`many=True`) spec but silently ignored it — a
  caller passing `REJECT` on a bulk tool got `IGNORE`. The bulk path now applies
  the policy to each item: `REJECT` raises `ValidationError` on the first item
  carrying an undeclared key, `PASSTHROUGH` folds each item's extras into that
  item's data, `IGNORE` drops them (unchanged). `argument_binding` has no
  counterpart for a list payload — a bulk service receives the whole list as one
  `data` argument, so there is nothing to spread — and passing a non-default
  binding with `many=True` now raises `ValueError` rather than being silently
  ignored. The default (`AUTO` / `IGNORE`) behaviour is unchanged.
- **`output_selector_spec.filter_set` now applies on the HTTP mutation path
  too.** `dispatch_spec`'s output re-fetch applied the nested selector's
  `filter_set`, but the HTTP mutation view did not — the same spec produced
  different results per transport. The HTTP output re-fetch now applies
  `output_selector_spec.filter_set` as well, with `filter_data` falling back to
  `request.query_params` (the blessed `filter_set` source on HTTP), so a given
  spec filters its re-fetched output identically on both paths.

## [0.21.0] — 2026-07-02

### Added

- **`on_target_resolved` now fires on selector dispatch.** `dispatch_spec` /
  `adispatch_spec` previously invoked the object-permission hook only on the
  service (mutation) paths, so object-level permissions on **reads** were
  unenforceable through it. The guard now fires on `SelectorSpec` dispatch too —
  with the resolved instance for a `RETRIEVE` and the resolved queryset for a
  `LIST` — so `on_target_resolved=enforce_permissions` authorizes selector reads
  as well as mutations. The default (`None`) is unchanged, so existing callers
  are unaffected.

### Fixed

- **`enforce_permissions` is collection-safe.** When the resolved target is a
  collection (the queryset a bulk / `LIST` dispatch produces) rather than a
  single row, `enforce_permissions` now runs only the class-level
  `has_permission` check and skips `has_object_permission`. Object permissions
  are a per-row `Model` concept — the previous behaviour called
  `has_object_permission(request, view, <QuerySet>)`, which raised
  `AttributeError` (or silently mis-authorized) on a `collection_selector_spec`
  guarded with an ownership-style permission. This makes
  `on_target_resolved=enforce_permissions` the safe canonical guard for every
  dispatch mode; the per-set BULK authorization decision is unchanged.

## [0.20.0] — 2026-06-23

### Added

- **Caller-side input policies for `dispatch_spec` / `adispatch_spec`** —
  three optional, additive parameters that let a non-HTTP transport map its wire
  onto a spec, finishing `dispatch_spec` as the single transport-neutral
  entrypoint. The principle: a **spec declares *what*** (its inputs, filters,
  output shape, permissions); the **caller declares *how*** its flat input
  becomes callable arguments. Every default reproduces the pre-policy behaviour
  exactly, so existing callers are unaffected.
  - `argument_binding=ArgumentBinding.AUTO` — whether client input lands as a
    single `data` bundle (`BUNDLE`) or is spread as individual kwargs
    (`SPREAD_AUTHOR_WINS` / `SPREAD_CALLER_WINS`, differing in precedence against
    the author's `kwargs`). `AUTO` resolves per spec type — service → `BUNDLE`,
    selector → `SPREAD_AUTHOR_WINS` — matching what each did before. The
    `SPREAD_*` modes strip the reserved pool seeds (`request` / `user` / `data` /
    `serializer` / `instance` / `collection`) from the spread, so a client can't
    poison transport-controlled state by naming an argument after one.
  - `unknown_arguments=UnknownArguments.IGNORE` — strictness about `params` keys
    outside the spec's declared set: `IGNORE` drops them (today's behaviour),
    `REJECT` raises a `ValidationError`, `PASSTHROUGH` forwards them to the
    callable. The declared set is derived from the spec alone — a service's
    `input_serializer` fields plus the keys its nested target selectors consume; a
    selector's `selector` parameters — and a `**kwargs` callable or a duck-typed
    `filter_set` makes the set "open" (nothing is unknown).
  - `on_target_resolved=None` — an optional object-permission hook (the
    `TargetGuard` protocol) invoked with the resolved mutation target before the
    service runs. Its signature is identical to `enforce_permissions`, so that
    primitive is passed **directly by name** (no `lambda`): the guard receives the
    resolved row (update), the resolved set (bulk), or `None` (create), restoring
    `has_object_permission` parity for off-HTTP transports. `dispatch_spec` itself
    stays authorization-agnostic — it only invokes the supplied guard.
- **`ArgumentBinding`, `UnknownArguments`, `TargetGuard`** are new top-level
  exports (and live under `rest_framework_services.types`).

## [0.19.0] — 2026-06-21

### Added

- **View-free JSON Schema generation (`rest_framework_services.jsonschema`)** —
  the first part of the off-HTTP surface. Three new top-level helpers
  turn a spec (or a bare serializer / dataclass) into a JSON Schema dict, with
  **no** view, request, or `drf-spectacular` import in the path — what an
  alternate transport (a Pydantic-AI toolset, the MCP server) builds tool
  definitions from:
  - `serializer_to_json_schema(serializer, *, partial=False)` — input schema for
    a DRF `Serializer` subclass, a bare `@dataclass`, or `None`; `partial` drops
    the `required` list (mirroring `spec.partial`).
  - `output_to_json_schema(output_serializer, *, kind=None, paginate=False)` —
    output schema, or `None` when undeclared; `kind=LIST` wraps the item schema
    in an array, `paginate=True` in the `{items, page, totalPages, hasNext}`
    envelope.
  - `spec_to_json_schema(spec, *, phase="input"|"output")` — reads the right
    serializer / kind off a `ServiceSpec` or `SelectorSpec`. For a `SelectorSpec`
    carrying a `filter_set`, the input schema's `properties` are the filter
    fields.
  - `filterset_to_json_schema(filter_set)` — maps a `django-filter` `FilterSet`
    to JSON Schema properties. Behind a new **`[filter]` extra** (the core still
    imports no `django-filter` — `SelectorSpec.filter_set` is duck-typed); the
    import is lazy and only fires when a `filter_set` is actually introspected.

  drf-spectacular `@extend_schema_field` / `@extend_schema_serializer` overrides
  are honoured by reading the stamped attribute via `getattr`, so the support is
  cost-free whether or not spectacular is installed. Distinct from
  `rest_framework_services.openapi`, which emits DRF serializer classes for DRF's
  own OpenAPI generators.
- **Consumer-extensible mappings (`JsonSchemaRegistry`)** — every
  `*_to_json_schema` helper takes an optional `registry=` of consumer rules so a
  project can map its own DRF field / `django-filter` filter / Python types (or
  override a built-in) without forking the package. `JsonSchemaRegistry` is an
  immutable value carrier with three rule lists (`fields`, `filters`,
  `python_types`); build one with `DEFAULT_JSON_SCHEMA_REGISTRY.extend(...)` (rules
  are tried before the built-ins, first match wins) and pass it through. No global
  mutable state — there is nothing to register into and nothing to leak between
  callers or tests.
- **Off-HTTP dispatch context (`build_offline_context`, `OfflineContext`,
  `OfflineServiceView`)** — synthesize the `request` + `view` a spec's callables
  (`kwargs` providers, `extend_queryset`, context providers) expect, so a spec
  written for the HTTP transport can be driven from a Pydantic-AI toolset, the
  MCP server, a management command, or a task runner. `build_offline_context(user,
  params, *, http_request=None, action=None, kwargs=None)` sets `request.user`,
  seeds `request.data` from `params`, and returns the trio bundled as an
  `OfflineContext` ready to pass into `dispatch_spec(..., request=, view=)`.
- **`enforce_permissions(spec, context, *, instance=None)`** — run a spec's
  `permission_classes` off the HTTP path. `dispatch_spec` deliberately does not
  consult `permission_classes` (authorization is the view's job on HTTP), so an
  alternate transport must call this before dispatching or it would skip authz
  entirely. Mirrors a DRF view's `has_permission` check against the synthetic
  request/view, additionally checks `has_object_permission` when an `instance` is
  supplied (object-level parity the HTTP path has), and raises
  `rest_framework.exceptions.PermissionDenied` (carrying the permission's
  `message` / `code`) on the first failure. `permission_classes` of `None` or `[]`
  is a no-op.

## [0.18.0] — 2026-06-20

### Added

- **`SelectorSpec.filter_set` — transport-neutral filtering on the spec**.
  A `django-filter` `FilterSet` (or any object honouring the same
  `(data, queryset) -> .qs` contract) set on a selector spec is applied to
  the selector's queryset after the shaping fields, reading the values off
  `request.query_params`. It composes with `select_related` /
  `prefetch_related` / `annotations` / `extend_queryset`, works on both
  `LIST` and `RETRIEVE` selectors — closing the retrieve-path gap where
  `RetrieveModelMixin` runs no filter step — and requires the selector to
  return a `QuerySet` (same guards as the other shaping fields). Applied by
  duck typing, so it adds **no dependency** to the package. See the new
  *Filter a selector with `filter_set`* recipe.
- **Fail-fast guard for the list double-apply**. A `LIST` selector
  spec that carries `filter_set` while its view also lists
  `DjangoFilterBackend` in `filter_backends` now raises
  `ImproperlyConfigured` at `as_view()` time — `filter_set` *replaces*
  `DjangoFilterBackend` (they apply the same FilterSet), so configuring both
  would filter the queryset twice. Retrieve is unaffected (its selector path
  overrides `get_object()` and never calls `filter_queryset`).
- **`ServiceSpec.document_service_error`** — a tri-state OpenAPI flag
  (`bool | None`, default `None`) controlling whether the generated schema
  documents the `422` `ServiceError` response. `None` gates it on whether the
  operation validates input; `True` / `False` force it. Schema-only; runtime
  behaviour is unchanged.
- **Native nested / child-collection writes**. `create_from_input`
  and `update_from_input` (plus the `acreate`/`aupdate` async siblings) take a
  `children={relation: ChildSpec(...)}` argument that writes reverse-FK
  ("one-to-many") collections from `data[relation]` — create / update matched /
  reconcile orphans — so a parent and its children persist from one request
  without delegating to a writable-nested serializer's `save()`. `replace`
  (default) removes orphans (unlinking nullable-FK children like `SET_NULL`,
  deleting the rest like `CASCADE`); `merge` upserts only. A `ChildSpec` may
  nest its own `children` for grandchildren. The default `create_model` /
  `update_model` / `delete_model` services (and async siblings) forward
  `children=` for declarative nested writes with no hand-written service;
  `delete_model` removes the declared collections before the parent.
  `children` stays on the helpers, never on `ServiceSpec`. New public
  `ChildSpec` and `ChildCollectionChange` types; `ChangeResult` gains an
  additive `children` tuple of per-collection deltas
  (`created`/`updated`/`deleted`/`unlinked` child pks) plus
  `get_child_change(relation)`. See the new *Nested / child-collection writes*
  recipe.
- **Transport-neutral `dispatch_spec`**. A single, view-free execution
  path for a `ServiceSpec` or `SelectorSpec`:
  `dispatch_spec(spec, *, user, params, request=None, view=None)` (and the
  async `adispatch_spec`) validates input, resolves the mutation target via
  `instance_selector_spec`, runs the service / selector, applies queryset
  shaping (incl. `filter_set`, reading the new transport-supplied filter data),
  and materializes a `RETRIEVE` — returning a `DispatchResult`
  (`value` / `kind` / `status`) the caller formats for its wire. `render_spec_output`
  is the paired blessed render step (output serializer + context). Ordering,
  pagination, and the response envelope stay transport concerns. This is the
  shared path the MCP server and the AG-UI bridge adopt to delete their
  re-implementations (the surface half of the long-standing dispatch
  triplication); the HTTP view layer keeps its own request-coupled
  orchestration. New blessed surface: `dispatch_spec`, `adispatch_spec`,
  `render_spec_output`, `build_input_serializer_from_data`, and the
  `DispatchResult` type.
- **Bulk & collection mutations**. `ServiceSpec` gains two bulk
  shapes (mutually exclusive): **`many: bool`** validates a list request body
  (`input_serializer` runs `many=True`) and renders the result list — the
  service receives the validated list and loops itself (`bulk_create` /
  comprehension); and **`collection_selector_spec`** — the LIST-kind twin of
  `instance_selector_spec` — resolves a scoped set (queryset or any iterable,
  via the selector + `filter_set`) and seeds it into the service pool as
  `collection` for an instance-less bulk delete / update. An empty set is a
  no-op; authorization is per-set; both run all-or-nothing under `atomic=True`
  (per-item partial-success responses are a planned follow-up). Works through
  the HTTP mutation views **and** `dispatch_spec`. New default service
  `delete_collection` / `adelete_collection` (`collection.delete()`, with a
  `soft_delete` hook). Fail-fast validation: `many` xor
  `collection_selector_spec`, and the collection spec must be LIST-kind with a
  selector.
- **List-shaped output for collection mutations**. `output_selector_spec`
  now honours its `kind`: `RETRIEVE` (the default) re-fetches a single instance
  as before, while **`kind=LIST`** re-fetches and renders the affected *set*
  (`many=True`) — the output twin of `collection_selector_spec`, so a bulk
  update can respond with the rows it touched instead of only a summary. Valid
  only alongside `collection_selector_spec` (a single-instance mutation returns
  one representation); enforced at `as_view()` time. Runs through the HTTP
  mutation views **and** `dispatch_spec` / `adispatch_spec`. See the
  *Bulk & collection mutations* recipe.

### Changed

- `apply_queryset_shaping` (part of the stable dispatch surface) gained an
  optional `filter_set` keyword argument (default `None`) and now applies a
  spec's FilterSet as the final shaping step. Backward-compatible: existing
  callers that don't pass `filter_set` are unaffected.
- `apply_queryset_shaping` also gained an optional `filter_data` keyword
  (default `None`): when set, the FilterSet reads it instead of
  `request.query_params`, so a transport-neutral caller (`dispatch_spec`)
  supplies its own params. The HTTP view path passes `None` and keeps reading
  `request.query_params` — unchanged.
- `build_input_serializer`'s data-extraction core is now exposed as
  `build_input_serializer_from_data(data, …)` (blessed), so validation runs the
  same way on and off the HTTP path. `build_input_serializer(request, …)` is a
  thin wrapper over it — its signature is unchanged.
- **Unified provider invocation**. Every spec-level provider
  (`kwargs`, `input_data`, `input_serializer_context`,
  `output_serializer_context`) **and** the corresponding view hooks are now
  dispatched through the same signature-filtering keyword pool that services
  and selectors use. A provider declares only what it needs — any subset of
  `view` / `request` plus the resolved-data extras (`result` / `instance` /
  `page`), or `**kwargs` — instead of the previous mandatory positional
  `(view, request)`. Providers using the documented `(view, request)`
  signature are unaffected.
- **The 422 `ServiceError` response is now gated in the OpenAPI schema**.
  It was attached to *every* spec-driven mutation; it now defaults to
  operations that validate input (`spec.input_serializer is not None`), so a
  no-input mutation (e.g. a plain delete) no longer carries a spurious 422.
  Override per spec with `document_service_error`.

### Fixed

- **Stale prefetched data after a mutating service**. When a mutation
  target was resolved through a prefetching `instance_selector_spec` (or a
  prefetching `get_object()` queryset) and the service changed a related
  collection via a path Django doesn't self-invalidate (e.g. creating reverse-FK
  rows), the rendered response could reflect the stale prefetch cache. The flow
  now clears `_prefetched_objects_cache` on the resolved instance after the
  service runs, mirroring DRF's `UpdateModelMixin`. A no-op on create and when
  nothing was prefetched; an `output_selector_spec` re-fetch keeps its own
  intentional `prefetch_related`.

### Documentation

- New recipe: *Filter a selector with `filter_set`* — list and
  retrieve usage, the "replaces `DjangoFilterBackend`" rule, and the
  `kwargs`-vs-`filter_set` boundary for computed (non-queryset) results.
- OpenAPI guide documents the 422 gating + `document_service_error`; the
  serializer-context recipe notes the flexible provider-signature declaration.

## [0.17.0] — 2026-06-10

### Added

- **The stable dispatch surface**. The leaves an alternate
  transport builds on are now documented public API, re-exported from the
  top-level package with a semver stability promise — see the new
  *Dispatch (stable surface)* reference page: `resolve_callable_kwargs`,
  `is_async` (shared); `run_service`, `arun_service`,
  `build_input_serializer`, `validate_input`, `resolve_mutation_instance`
  (service side); `run_selector`, `arun_selector`, `is_queryset`,
  `apply_queryset_shaping` (selector side). Previously downstream packages
  (drf-mcp) imported these from private/`utils` paths, so a within-range
  refactor could break them silently. An import-surface test pins the
  blessed exports.

### Removed

- **The private `_compat` package.** `run_service` / `arun_service` moved
  to `services/` and `is_async` to the package root (all three are part of
  the blessed surface above). **Breaking for anything importing
  `rest_framework_services._compat.*`** — in practice only
  drf-mcp ≤0.7, whose dependency cap (`<0.17`) keeps it on 0.16; its next
  release re-points the imports and widens the pin.

### Documentation

- The view-coupled orchestrators (`dispatch_mutation_for_spec`,
  `dispatch_selector_for_spec`) are explicitly **not** part of the stable
  surface — a transport-neutral spec dispatcher is future work that will
  compose the blessed leaves.

## [0.16.0] — 2026-06-10

### Added

- **`ServiceSpec.instance_selector_spec`** — the input-side twin of
  `output_selector_spec`. A nested `SelectorSpec` (`kind=RETRIEVE`) embeds
  the update/destroy instance lookup in the spec itself, so a mutation spec
  is genuinely self-contained: standalone `ServiceUpdateView` /
  `ServiceDeleteView` subclasses need no `queryset` / `lookup_field`, and
  viewset `update` / `partial_update` / `destroy` actions and
  `@service_action(detail=True)` actions resolve the instance from the spec
  (precedence: spec selector → `action_specs["retrieve"]` selector → DRF
  default `get_object()`). The selector pool is `{request, user}` + URL
  kwargs + the selector extras chain; queryset shaping applies; `None` /
  missing → 404 (the nested spec's `allow_none` is ignored here); the
  nested `permission_classes` / `output_serializer` /
  `output_serializer_context` are ignored. Spec-resolved instances run
  object-level permissions (`check_object_permissions`) — closing a gap the
  selector-driven `get_object()` chain has. `kind=LIST` or configuring it
  on a create/non-detail action fails fast at `as_view()` time.
- **`ServiceSpec.partial`** — force or suppress partial validation
  regardless of HTTP verb. `None` (default) keeps the method-derived flag;
  `partial=False` on a PATCH action makes `required` fields enforce like a
  PUT; `partial=True` relaxes any verb (including create). Applied once at
  `dispatch_mutation_for_spec`, so viewset mixins, standalone views, and
  `@service_action` all honour it. The OpenAPI `ServiceAutoSchema` reflects
  the override (a forced-full PATCH keeps its `required` list instead of
  the `Patched*` component).
- **`action_specs["partial_update"]`** — PATCH now resolves a dedicated
  `"partial_update"` key first and falls back to `"update"`, uniformly at
  all resolution sites (dispatch, `get_permissions`,
  `ActionSerializerResolver`, and schema-time `resolve_spec`). Defining
  *only* `"partial_update"` yields a PATCH-only endpoint (PUT → 405).
- **`SelectorSpec.allow_none`** — `RETRIEVE`-only. `True` expresses a
  nullable-resource contract: a `None` / missing resolution renders `200`
  with a JSON `null` body (output serializer skipped) on
  `SelectorRetrieveView` and the retrieve viewset mixin. Default `False`
  keeps the `NotFound` behaviour. Ignored on nested specs
  (`output_selector_spec` keeps authoritative-`None` → 204;
  `instance_selector_spec` always 404s).
- **Bound serializer in the service kwarg pool** — services may declare a
  `serializer` parameter to receive the bound, validated input serializer
  (opt-in declare-to-receive, like `request` / `user` / `data`). With the
  instance-aware construction below, `serializer.save()` performs a
  DRF-correct create or update — the home for nested-write persistence
  that lives on the serializer. Declaring `serializer` with no
  `input_serializer` on the spec fails fast at `as_view()` time.
- `input_data` providers (spec-level and the `get_input_data` /
  `get_<action>_input_data` view hooks) may declare `instance` to receive
  the resolved mutation target (`None` on create) — passed only when
  declared; legacy providers unaffected.
- New public leaf helpers in `views/mutation/utils.py`:
  `build_input_serializer` (the bound-serializer sibling of
  `validate_input`) and `resolve_mutation_instance`.

### Changed

- **Behaviour change — instance-aware input validation.** On update /
  destroy flows the input serializer is now constructed DRF-style with the
  resolved instance: `serializer(instance, data=..., partial=...)`. Code
  inside `validate()` / field validators that reads `self.instance`
  (previously always `None` here) starts seeing the actual row — that is
  the fix, and instance-aware validators (e.g. `UniqueValidator`) now
  correctly exclude the current row. But if a serializer *branches* on
  `self.instance` to detect "create vs update", it now takes the update
  branch on update requests. Applies on both the spec-selector and legacy
  `get_object()` lookup paths.
- **Behaviour change / bugfix — PATCH permission fallback.** Permission and
  serializer resolution previously keyed on `self.action`
  (`"partial_update"` under PATCH) while dispatch resolved
  `action_specs["update"]` — so an `"update"`-keyed spec's
  `permission_classes` silently did **not** apply to PATCH requests unless
  the spec was duplicated under a second key. All resolution sites now
  share one fallback chain; PATCH is guarded by the `"update"` spec's
  permissions out of the box. If you relied on PATCH bypassing spec
  permissions (unlikely, but it was the old behaviour), add an explicit
  `"partial_update"` entry.

### Documentation

- Concepts page now documents the full **result-rendering matrix**
  (service return × output spec × `success_status` → response shape), the
  **stale fetch-time annotations** pitfall (annotations resolved by the
  instance lookup reflect pre-mutation state; re-fetch via
  `output_selector_spec` for the post-mutation truth), the
  PATCH-that-validates-like-PUT recipe, the standalone-no-queryset
  example, and an explicit non-goal: constant/no-logic endpoints are fine
  as plain DRF views.

## [0.15.1] — 2026-06-04

### Fixed

- A `destroy`/`DELETE` `ServiceSpec` with a custom `success_status` whose
  service returns `None` now renders an **empty body at the configured
  status**. Previously the update-in-place fallback misfired (it keyed off
  "status ≠ 204" as a proxy for "this is an update"), surfacing the stale
  post-delete instance as the response body — which then failed to serialize.
  The fallback is now driven by an explicit update-vs-destroy intent flag.
- No-output mutations (no `output_selector_spec`) whose service returns `None`
  now always render an empty body instead of attempting to serialize the raw
  in-memory model instance. This makes no-input/no-output `update` and
  `destroy` services work as expected. An explicitly-set `spec.success_status`
  is honored for the empty-body response; otherwise it falls back to `204`. A
  selector that returns `None` still renders `204` (its result is
  authoritative).

### Changed

- The internal "is this a Django QuerySet?" check used by the selector and
  mutation-output dispatch paths is now a single `is_queryset()` predicate
  (`isinstance` against `QuerySet`/`Manager`) instead of three scattered
  `hasattr(..., "first")` / `hasattr(..., "annotate")` duck-typing checks.
  More precise (a domain object that merely exposes `.first()` is no longer
  mistaken for a queryset) and self-documenting. No public API change.

### Documentation

- Clarified that `LIST` selectors may return **any iterable** (plain
  `list`, `tuple`, hand-built dataclass sequences, …), not only a
  QuerySet. The only constraints are inherent: the queryset-only shaping
  fields cannot be combined with a non-QuerySet return, and lazy
  generators do not survive slicing/counting paginators. See the
  [queryset-shaping recipe](https://artui.github.io/djangorestframework-services/recipes/queryset-shaping/).

## [0.15.0] — 2026-06-02

### Added

- Output serializer-context providers can now receive the resolved data
  about to be serialized, so they can populate context from a **single**
  batched query instead of re-fetching. A provider (the
  `get_output_serializer_context` directional hook, a
  `get_<action>_output_serializer_context` per-action hook, or a spec-level
  `output_serializer_context`) may declare an extra keyword parameter —
  `result` (mutation output), `instance` (retrieve), or `page` (list) — and
  it is passed only when declared. `view` and `request` stay positional, so
  existing `(view, request)` providers are unaffected. The output context is
  now resolved *after* the service and output selector run, guaranteeing the
  value reflects exactly what will be rendered. See the
  [serializer-context recipe](https://artui.github.io/djangorestframework-services/recipes/serializer-context/).

## [0.14.0] — 2026-05-31

### Added

- `UnsetType` is now part of the public API (re-exported from
  `rest_framework_services`). It is the type of the `UNSET` sentinel, exported
  so callers can annotate sentinel-defaulted fields cleanly —
  `bio: str | None | UnsetType = UNSET` — instead of suppressing the
  type-checker with `# type: ignore[assignment]`. The class was previously
  private (`_Unset`); it has been renamed and promoted (the old name was never
  exported, so this is not a breaking change).

## [0.13.0] — 2026-05-22

### Changed (breaking)

- `SelectorSpec` is now keyword-only and requires a `kind: SelectorKind` field
  (`SelectorKind.LIST` or `SelectorKind.RETRIEVE`). The kind tells the
  dispatcher whether to materialize the selector return as a list or as a
  single retrieve-shaped instance, and it is cross-checked against the
  mount point at `as_view()` time — a `LIST` spec on `SelectorRetrieveView`
  (or the `"retrieve"` `action_specs` entry) raises `ImproperlyConfigured`
  fail-fast. Making the kind explicit lets a spec be reused outside an HTTP
  request (management command, cron job, non-DRF caller) without the
  semantics living implicitly in the call site.
- `ServiceSpec`'s output pipeline is collapsed into a single nested field:
  `output_selector_spec: SelectorSpec | None`. The previous flat fields
  `output_selector`, `output_serializer`, `output_serializer_context`,
  `select_related`, `prefetch_related`, `annotations`, and
  `extend_queryset` are removed; they now live on the nested spec (whose
  `kind` must be `SelectorKind.RETRIEVE`). The mutation dispatch path
  validates and consumes the nested spec, and `ActionSerializerResolver`
  reads the response serializer from `output_selector_spec.output_serializer`.
- `OutputSelector` Protocol removed. The "post-mutation re-fetch" selector
  is now structurally a `RetrieveSelector` (nested under
  `ServiceSpec.output_selector_spec`) and the `result` kwarg, when needed,
  is declared directly on the user's function signature.
- `@selector_action`'s `detail=` parameter is removed. The DRF URL shape
  is pinned from `spec.kind` (`LIST → detail=False`, `RETRIEVE →
  detail=True`) — there is no longer a way to override it. If you need a
  URL shape that doesn't match the response shape (a detail action that
  returns a list, or a collection action that returns a single resource),
  fall back to DRF's plain `@action` and write the dispatch yourself.
  `@service_action` still takes `detail=` (mutation routing is decoupled
  from the read-shape distinction).
- The internal `dispatch_retrieve_selector` helper is folded into
  `dispatch_selector_for_spec`, which now branches on `spec.kind`. Callers
  outside the library should not be affected (the helpers live under
  `selectors/utils.py` and were never part of the public API), but the
  symbol no longer exists.

## [0.12.0] — 2026-05-20

### Added

- `permission_classes` field on `ServiceSpec` and `SelectorSpec`. Accepts a
  sequence of DRF `BasePermission` subclasses to override the calling view's
  class-level `permission_classes` for the action the spec backs. `None`
  (the default) inherits the view-level permissions; an empty sequence means
  "no permissions" explicitly. Honored by `_ActionSpecsMixin.get_permissions`
  (covers all viewset mixins and `ServiceViewSet` / `SelectorViewSet`), by
  `MutationFlowMixin.get_permissions` (standalone mutation views), by the
  selector views' new `get_permissions` overrides, and by the
  `@service_action` / `@selector_action` decorators which forward the value
  into DRF's `@action(permission_classes=...)`. Misconfigurations (non-
  `BasePermission` subclasses, permission instances rather than classes)
  fail fast at `as_view()` time with `ImproperlyConfigured`.
- `input_serializer_context` and `output_serializer_context` fields on
  `ServiceSpec`, plus `output_serializer_context` on `SelectorSpec`. Each
  takes a `Callable[[ServiceView, Request], Mapping[str, Any]]` returning
  extra context keys to merge into the serializer's `context=` dict. They
  sit at the most-specific layer of the existing resolution chain
  (DRF default → directional `get_<direction>_serializer_context` hook →
  per-action `get_<action>_<direction>_serializer_context` hook → spec
  callable), so the spec wins on overlapping keys. Wired through
  `dispatch_mutation_for_spec` (input + output), `selector_action`
  (output), and a new `get_serializer_context` override on
  `_ActionSpecsMixin` and the standalone `SelectorListView` /
  `SelectorRetrieveView` so the spec's output context is honored by
  `ListModelMixin` / `RetrieveModelMixin` dispatch. Closes the gap where
  the selector list/retrieve mixins previously ignored the per-action
  `get_<action>_output_serializer_context` hook entirely.
- Per-spec queryset shaping on **both** `SelectorSpec` and `ServiceSpec`.
  Four new fields cover the static and dynamic cases:
  - `select_related: Sequence[str] | None` — forwarded to
    `qs.select_related(*spec.select_related)`.
  - `prefetch_related: Sequence[str | Prefetch] | None` — forwarded to
    `qs.prefetch_related(*spec.prefetch_related)`; accepts plain names or
    full `Prefetch` objects.
  - `annotations: Mapping[str, Any] | None` — merged into a single
    `qs.annotate(**spec.annotations)` call.
  - `extend_queryset: Callable[[QuerySet, ServiceView, Request], QuerySet] |
    None` — dynamic escape hatch, invoked *after* the declarative fields
    so it always sees the fully statically-shaped queryset. Synchronous
    only (no DB I/O — manipulates the lazy expression tree).

  On `SelectorSpec`, shaping runs inside `dispatch_selector_for_spec`, so
  both list and retrieve flows pick it up. On `ServiceSpec`, shaping
  applies to the QuerySet returned by `output_selector` — a typical
  pattern is `output_selector=lambda result: Model.objects.filter(pk=result.pk)`
  with the spec declaring the eager-loading. After shaping, a QuerySet
  return is materialized via `.first()` (the matching `dispatch_retrieve_selector`
  behaviour also added below).

  Configuring shaping with no `selector` (on `SelectorSpec`) or no
  `output_selector` (on `ServiceSpec`) raises `ImproperlyConfigured` at
  `as_view()` time. A non-QuerySet return when shaping is set raises at
  request time.
- `dispatch_retrieve_selector` now materializes a QuerySet return via
  `.first()`, so retrieve selectors can return a filtered QuerySet and
  let the framework apply shaping before pulling the single object.
  Backward-compatible — selectors that already returned an instance
  continue to work unchanged.

### Changed

- The field order on `ServiceSpec` and `SelectorSpec` has been reorganized
  to group related fields together: the callable, then the input pipeline
  (`input_*`), then the output pipeline (`output_*` + the new shaping
  fields), then the cross-cutting `kwargs` and `permission_classes`.
  Backward-compatible for keyword-argument call sites (which is every
  documented usage); positional construction must follow the new order.

## [0.11.0] — 2026-05-19

### Added

- Default model service factories — six new top-level exports that
  return ready-made service callables for the common case where the
  entire body is a one-line wrapper over the mutation helpers:
  `create_model(Model, *, field_map=, exclude_fields=, m2m=)`,
  `update_model(Model, *, field_map=, exclude_fields=, m2m=,
  update_fields=)`, and `delete_model(Model, *, soft_delete=)`, plus
  the matching `acreate_model` / `aupdate_model` / `adelete_model`
  async variants that wrap `acreate_from_input` / `aupdate_from_input`
  / `await instance.adelete()`. `m2m` accepts either a static mapping
  or a callable receiving the validated `data` — the typical shape
  when M2M values live on the input itself. The returned callables
  conform to the unified `CreateService` / `UpdateService` /
  `DeleteService` Protocols, so they absorb arbitrary framework-pool
  keys (`request`, `user`, URL kwargs, `ServiceSpec.kwargs` returns)
  and the existing view layer routes them — sync or async — without
  changes.

### Removed

- The pre-merge `StrictCreateService` / `StrictUpdateService` /
  `StrictDeleteService` / `StrictListSelector` / `StrictRetrieveSelector`
  / `StrictOutputSelector` classes. Rename every import to its unified
  equivalent (`StrictCreateService` → `CreateService`, etc.) and drop
  the trailing `ExtraT` type argument from each call site (extras are
  now typed on the function signature instead — see *Changed* below).
  The `@implements(...)` decorator pattern keeps working unchanged
  once the names update.
- `NoKwargs` (the empty `TypedDict` previously used as the `ExtraT`
  slot of strict service Protocols). The slot no longer exists. Drop
  imports of `NoKwargs`; if you were also writing
  `**extras: Unpack[NoKwargs]` in a service body, replace it with
  `**extras: Any`.

### Changed

- `ChangeResult` is now generic over the model type. The four mutation
  helpers (`create_from_input`, `acreate_from_input`,
  `update_from_input`, `aupdate_from_input`) thread the model TypeVar
  through their signatures, so `create_from_input(Author, ...)` returns
  a `ChangeResult[Author]` whose `.instance` is typed as `Author` —
  removing the `cast(Author, result.instance)` boilerplate that used to
  be necessary. Bare `ChangeResult` (no parameter) keeps resolving to
  `ChangeResult[Model]`, so existing annotations continue to work
  unchanged. Runtime behaviour is identical; this is a typing-only
  change.
- **Breaking** (typing only): the lenient and strict service / selector
  Protocols are merged into a single shape per kind, with `**extras`
  typed as `Any`. The strict form's trailing `ExtraT` type argument is
  gone. Names and call sites:
  - `CreateService[InputT, ResultT]`.
  - `UpdateService[InputT, InstanceT, ResultT]`.
  - `DeleteService[InputT, InstanceT, ResultT]`.
  - `ListSelector[ResultT]`.
  - `RetrieveSelector[ResultT]`.
  - `OutputSelector[InT, OutT]`.

  Strict-typed extras stay possible on your *own* function signature:
  declare a `TypedDict` with `total=False` (or per-field `NotRequired`)
  and annotate `**extras: Unpack[YourKw]`. Inside the function body,
  `extras["foo"]` is typed by `YourKw`. The Protocol does not enforce
  a kwargs-shape match — that cross-check only worked under one minor
  version of one type checker (`ty` 0.0.32) and never under `mypy` or
  `pyright`. Putting the typing on the function instead works on every
  modern checker.

  Three migration notes:

  1. Drop the trailing `ExtraT` from every parameterised call site:
     `StrictCreateService[AuthorIn, MyKw, Author]` →
     `CreateService[AuthorIn, Author]`. Keep `**extras: Unpack[MyKw]`
     on your function for typed extras.
  2. Strict extras `TypedDict`s must declare keys as `NotRequired`
     (or set `total=False` on the class) — required keys would make
     the function reject callers that omit them, breaking Protocol
     conformance under PEP 692.
  3. The lenient Protocols no longer name `request` and `user` as
     fixed parameters — they flow through `**extras` like any other
     framework-pool key (matching the strict Protocols, which already
     dropped these in 0.9.0). Services that declared
     `def fn(*, data, request, user, **kwargs)` keep working at
     runtime; to satisfy the new Protocol annotation either drop the
     named `request` / `user` parameters and read them off `**extras`,
     or subclass `HttpExtras[YourUser]` (now `total=False`) and use
     it as the `Unpack` target.

  Runtime behaviour of every callable is unchanged. The merge unblocks
  the default model service factories above and restores Python 3.10+
  support (the previous design needed PEP 728's `extra_items=Any`,
  which is not in mypy yet and forced a 3.13 floor).
- `HttpExtras[UserT]` is now declared `total=False`: every key is
  optional, matching the framework's runtime contract (the kwargs
  pool may or may not contain `request` / `user` depending on the
  caller). Subclass with `total=False` (or annotate fields as
  `NotRequired`) for the same reason — see migration note 2 above.
- Version is now tracked in a single source of truth at
  `rest_framework_services/version.py`. `pyproject.toml` declares
  `dynamic = ["version"]` and hatchling reads the value from `version.py`
  at build time. `rest_framework_services.__version__` continues to
  re-export the same value. Pure refactor — no behaviour change.

## [0.10.0] — 2026-05-03

### Added

- DRF-style serializer context propagation for both input and output
  serializers in service-backed views, viewset mixins, `@service_action`,
  and `@selector_action`. Three layers, later wins on overlap: DRF's
  `get_serializer_context()` → directional fallback
  (`get_input_serializer_context` / `get_output_serializer_context`,
  defaulting to `get_serializer_context()`) → per-action override
  (`get_<action>_input_serializer_context` /
  `get_<action>_output_serializer_context`, viewsets only). Standalone
  `SelectorListView` / `SelectorRetrieveView` already received DRF
  context through `self.get_serializer(...)` and continue to do so.

## [0.9.0] — 2026-05-03

### Added

- `HttpExtras[UserT]` — generic `TypedDict` for strict services that want
  `request` / `user` from the framework pool. Subclass it (with your own
  user model as the parameter) instead of redeclaring those keys on every
  `ExtraT`. `default=Any` keeps the unparameterised form ergonomic.
- `call_service` / `acall_service` and `call_selector` / `acall_selector`
  — HTTP-scope helpers for invoking a service or selector from another
  view, middleware, or custom action. They build the framework's kwargs
  pool (deriving `user` from `request.user`) and dispatch via the same
  signature filter the framework uses internally. Async callables are
  bridged transparently via `async_to_sync` (sync helper) or awaited
  (async helper). `request` is required by type — outside HTTP scope,
  call the service callable directly.
- `@selector_action` — the GET-side companion to `@service_action`.
  Wraps a viewset method with selector dispatch (collection or detail),
  honours `spec.output_serializer` when set, falls back to
  `view.get_serializer(...)`, and integrates with DRF pagination on the
  collection path. `ObjectDoesNotExist` / `None` map to 404 on detail.
- `startserviceapp` now scaffolds a `specs/` package alongside
  `services/` and `selectors/` — a conventional home for
  `ServiceSpec` / `SelectorSpec` instances.

### Changed

- **Breaking** (typing only): the strict service / selector Protocols
  no longer include `request` and `user` in their fixed signatures.
  `request` and `user` are still placed in the kwargs pool by the
  framework — services that read them now declare them on their
  `ExtraT` `TypedDict` (most cleanly via `HttpExtras[YourUser]`), or
  omit them entirely if unused.
  - Migration: replace
    `def fn(*, data, request: Request, user: UserT, **extras: Unpack[MyKw])`
    with either
    `def fn(*, data, **extras: Unpack[MyKw])` (drop the params) or
    `class MyKw(HttpExtras[YourUser]): ...` followed by
    `def fn(*, data, **extras: Unpack[MyKw])` (extras carries
    `request` / `user`).
  - Lenient Protocols are unchanged — `**kwargs: Any` already lets
    services omit `request` / `user`.
- Stale doc reference removed: `docs/recipes/service-action.md` no
  longer lists `view` as a pool key (it was never in the pool — see
  `views/mutation/utils.py`).
- README and the install snippets in the docs now show `uv add`
  alongside `pip install`.

### CI

- The matrix `test` job now uploads a per-cell coverage artifact
  (`coverage-py<py>-django<dj>` with `coverage.xml` and the `htmlcov/`
  HTML report) for download from the workflow run. The 100% gate is
  unchanged.

## [0.8.1] — 2026-05-01

### Fixed

- `ServiceSpec` and `SelectorSpec` declared `ExtraT` with
  `bound=dict[str, Any]`, which (per PEP 589) rejects `TypedDict`
  subclasses as type arguments — exactly the shape the docs recommend.
  Both bounds are now `Mapping[str, object]`, so user-defined
  `TypedDict` kwargs (`SelectorSpec[QuerySet[Team], TeamScopeKwargs]`,
  etc.) type-check cleanly under `ty` and `mypy`.

## [0.8.0] — 2026-05-01

### Added

- `ServiceSpec.input_data` plus the symmetric three-tier resolver
  (`get_input_data` catch-all on `MutationFlowMixin`, per-action
  `get_<action>_input_data`, and the per-spec callable). Returns a
  mapping merged on top of `request.data` before the
  `input_serializer` validates — purpose-built for lifting URL kwargs
  (parent IDs from nested routes, etc.) into the serializer's input
  for cross-field validation. Server-supplied keys win on conflict.
- `data: InputT` parameter on the lenient `DeleteService` and strict
  `StrictDeleteService` Protocols. Pairs with `ServiceSpec.input_serializer`
  to type a delete-with-payload service end-to-end (e.g. a deletion
  reason). Default is `Ellipsis`, so services that don't read a body
  remain Protocol-compliant — bind `InputT` to the new `NoInput`
  sentinel for clarity.
- `NoKwargs` (empty `TypedDict`) and `NoInput` (sentinel class) under
  `rest_framework_services.types` and re-exported from the package
  root. Saves projects from re-defining the same empty stubs whenever
  a strict service has no extras (`NoKwargs`) or no body (`NoInput`).
- `ServiceAutoSchema` now emits `requestBody` for `DELETE` endpoints
  whose spec carries an `input_serializer`. drf-spectacular's default
  `AutoSchema` suppresses bodies on DELETE; this override keeps the
  generated schema honest for delete-with-payload routes.

### Changed (BREAKING)

- Type-parameter ordering on the delete service Protocols now leads
  with `InputT` to match `StrictCreateService` / `StrictUpdateService`:
  - `DeleteService[InputT, InstanceT, ResultT]` (was
    `DeleteService[InstanceT, ResultT]`).
  - `StrictDeleteService[InputT, InstanceT, ExtraT, ResultT]` (was
    `StrictDeleteService[InstanceT, ExtraT, ResultT]`).

  Migration: at each parameterization site, prepend the input type. For
  services that don't read a body, use the new `NoInput` sentinel:
  `StrictDeleteService[NoInput, Author, NoKwargs, None]`.

## [0.7.0] — 2026-04-30

### Added

- `implements(Protocol[...])` — identity decorator that attaches a strict
  service / selector Protocol shape directly to the decorated function.
  Replaces the `_: StrictCreateService[...] = create_author` shim as the
  recommended way to assert conformance: the assertion lives on the
  function definition (survives renames), reads naturally, and is what
  CI now exercises against the strict Protocols. The shim form continues
  to work.
- `tests/services/strict_drift_fixtures.py` plus a
  `make type-check-strict-fixtures` target — known-bad usages that must
  produce ``ty`` diagnostics, wired into the CI lint job to guard against
  regressions where strict-Protocol drift detection silently breaks.

### Changed (BREAKING)

- Reordered the generic parameters on every strict service / selector
  Protocol so `ExtraT` sits immediately before the result type — the
  parameter list now reads "input, extras, result" and mirrors the call
  shape:
  - `StrictCreateService[InputT, ExtraT, ResultT]`
  - `StrictUpdateService[InputT, InstanceT, ExtraT, ResultT]`
  - `StrictDeleteService[InstanceT, ExtraT, ResultT]`
  - `StrictListSelector[ExtraT, ResultT]`
  - `StrictRetrieveSelector[ExtraT, ResultT]`
  - `StrictOutputSelector[InT, ExtraT, OutT]`

  Migration: swap the last two type arguments at every parameterization
  site. The non-strict Protocols (`CreateService`, `ListSelector`, …)
  are unchanged.
- Dropped the `bound=dict[str, object]` constraint from `ExtraT` on every
  strict Protocol. `TypedDict` subclasses are now accepted as type
  arguments by both `ty` and `mypy` (previously rejected with
  "Type argument … must be a subtype of `dict[str, object]`"). This
  matches the documented intent of the strict Protocols.

## [0.6.1] — 2026-04-29

### Changed

- README and docs index now surface lenient and strict service / selector
  typing examples and link to the typing page. Minor wording fix:
  "opt-out per view" → "opt-out per spec" (atomic lives on
  `ServiceSpec`).

## [0.6.0] — 2026-04-28

### Added

- **OpenAPI / Swagger integration** via an opt-in
  `drf-spectacular` adapter. New optional install extra:
  ``pip install djangorestframework-services[spectacular]``.
  - `rest_framework_services.openapi.enable_openapi()` — wires
    `ServiceAutoSchema` onto every library view class. Call once from
    `AppConfig.ready()`.
  - `ServiceAutoSchema` reads each `ServiceSpec` to derive the request
    body (`input_serializer`), success response (`output_serializer`),
    success status, and a `422` response documenting `ServiceError`.
    `@extend_schema` annotations always win.
  - Bare `@dataclass` `input_serializer` is auto-wrapped in
    `DataclassSerializer` (mirroring the runtime), so dataclasses show
    up as typed schema components instead of bare `object`.
  - `@service_action` handlers stamp `_service_spec` on the wrapped
    handler so the schema generator can recover the spec.
  - `ServiceErrorSerializer` is exported for users who want to reuse the
    422 component shape.
  - New documentation page: `docs/openapi.md`.
- Lenient service / selector Protocols — opt-in shapes for IDE and
  type-checker support. New top-level exports: `CreateService`,
  `UpdateService`, `DeleteService`, `ListSelector`, `RetrieveSelector`,
  `OutputSelector`. Each is parameterized by input / instance / result
  type and keeps a `**kwargs: Any` escape hatch.
- Strict variants — `StrictCreateService`, `StrictUpdateService`,
  `StrictDeleteService`, `StrictListSelector`, `StrictRetrieveSelector`,
  `StrictOutputSelector` — use [PEP 692](https://peps.python.org/pep-0692/)
  `Unpack[TypedDict]` to pin the extras delivered by `ServiceSpec.kwargs`.
  Use these when you want signature drift to fail static checks.
- `ServiceSpec` and `SelectorSpec` are now generic over input / result and
  an optional `TypedDict` of extra kwargs. Unparameterized usage is
  unchanged (`Any` everywhere).
- `ServiceSpec.kwargs` and `SelectorSpec.kwargs` — per-spec callable
  returning extra kwargs for the call. Co-locating the contract with the
  spec replaces `if self.action == ...` branches in `get_service_kwargs`.
- Per-action hooks — `get_<action>_service_kwargs` and
  `get_<action>_selector_kwargs` are now consulted in the kwargs
  resolution chain, so multi-action viewsets can split contracts by
  method name instead of branching on `self.action`.
- `ServiceView` Protocol (in `rest_framework_services.views`) — narrow
  structural shape passed to per-spec kwargs providers, exposing
  `request`, `kwargs`, `action`.
- Fail-fast spec validation. `as_view()` now walks every spec and raises
  `django.core.exceptions.ImproperlyConfigured` on misconfigurations:
  service requires `data` without an `input_serializer`, requires
  `instance` on a create / list action, requires `result` outside an
  `output_selector`, or has a required parameter that no kwargs source
  can supply. `@service_action` validates at decoration time.
- New documentation page: `docs/typing.md`.

### Changed

- **Breaking.** Services and selectors no longer receive `view` in their
  kwargs pool. They are plain business logic; pipe view state through a
  per-spec `kwargs` provider (which receives the view typed as
  `ServiceView`) or `get_<action>_service_kwargs` instead. Migration:
  read whatever you used off `view` and surface it via one of the kwargs
  sources.

## [0.5.0] — 2026-04-28

### Added

- `SelectorSpec` (in `rest_framework_services.types`) — frozen dataclass
  bundling per-action read config: `selector` and `output_serializer`.
- `ActionSerializerResolver` viewset mixin — resolves
  `get_serializer_class()` from the active action's `action_specs` entry
  (`output_serializer` on either a `SelectorSpec` or `ServiceSpec`),
  replacing `MultiSerializerMixin`.
- `django-stubs` and `djangorestframework-stubs` added to dev dependencies
  so `ty` resolves Django/DRF types directly.

### Fixed

- `update_from_input` / `aupdate_from_input` now automatically include
  `auto_now=True` fields (e.g. `updated_at`) in the computed
  `update_fields` list when `update_fields=True` (default). Previously
  those columns were silently skipped because Django only updates
  `auto_now` fields when they are explicitly listed in `update_fields`.
  Explicit `update_fields=[...]` lists are still passed through
  unchanged; `update_fields=False` is unaffected.

### Changed

- **Breaking.** `service_specs` renamed to `action_specs` on all viewset
  mixins (`SelectorListMixin`, `SelectorRetrieveMixin`,
  `ServiceCreateMixin`, `ServiceUpdateMixin`, `ServiceDestroyMixin`) and
  on `ServiceViewSet` / `SelectorViewSet`.
- **Breaking.** Read-side entries in `action_specs` (`"list"`,
  `"retrieve"`) now require a `SelectorSpec` instance. Bare callables are
  no longer accepted and raise `ImproperlyConfigured` at request time with
  a migration message.
- **Breaking.** `serializer_classes` mapping and `MultiSerializerMixin`
  removed. Move per-action serializers into `action_specs` via
  `SelectorSpec(output_serializer=...)` or `ServiceSpec(output_serializer=...)`.
- **Breaking.** `SelectorListView` and `SelectorRetrieveView` now accept
  a single `spec: SelectorSpec` attribute instead of separate `selector`
  and `serializer_class` attributes. `spec.output_serializer` overrides
  `get_serializer_class()`; `spec.selector = None` keeps DRF's default
  `get_queryset()` / `get_object()`.
- A wrong-type `action_specs` entry (e.g. `SelectorSpec` on a write
  action, or `ServiceSpec` on a read action) now raises
  `ImproperlyConfigured` at request time instead of silently falling back.

### Migration from 0.4.x

| 0.4.x | 0.5.x |
|---|---|
| `service_specs = {...}` | `action_specs = {...}` |
| `serializer_classes = {"list": S}` | `action_specs["list"] = SelectorSpec(output_serializer=S)` |
| `service_specs["list"] = list_fn` | `action_specs["list"] = SelectorSpec(selector=list_fn)` |
| `service_specs["list"] = list_fn` + `serializer_classes["list"] = S` | `action_specs["list"] = SelectorSpec(selector=list_fn, output_serializer=S)` |
| `class V(SelectorListView): selector = fn; serializer_class = S` | `class V(SelectorListView): spec = SelectorSpec(selector=fn, output_serializer=S)` |
| `MultiSerializerMixin` in MRO | `ActionSerializerResolver` in MRO |

## [0.4.0] — 2026-04-28

### Added

- mkdocs-material documentation site under `docs/`, published to GitHub
  Pages on every tag release. Covers quickstart, concepts, mutation
  helpers, errors & atomic, async, recipes, and an autodoc reference
  section driven by `mkdocstrings`.
- Tag-driven release pipeline (`.github/workflows/release.yml`): on
  push of a `vX.Y.Z` tag, runs the test suite, asserts the tag matches
  both `pyproject.toml` and `__version__`, builds wheel + sdist,
  publishes to PyPI via OIDC trusted publishing, then deploys docs to
  the `gh-pages` branch.
- `docs` job in `tests.yml` runs `mkdocs build --strict` on every PR
  to catch broken links / missing autodoc targets before merge.
- `make help`, `make init`, `make docs-serve`, `make docs-build`
  Makefile targets.

### Changed

- `tests.yml` modernised: switched to `astral-sh/setup-uv@v6`, added a
  cancel-in-progress concurrency group, and split lint / docs / test
  into separate jobs.
- `CLAUDE.md` Releases section now describes the tag-driven pipeline
  and the one-time PyPI Trusted Publisher / GitHub Pages setup.

## [0.3.0] — 2026-04-27

### Added

- `ServiceSpec` (in `rest_framework_services.types`) — frozen dataclass
  bundling per-action mutation config: `service`, `input_serializer`,
  `output_serializer`, `output_selector`, `atomic`, `success_status`.
- `service_specs` class attribute on `ServiceViewSet` (and the per-action
  mixins) — a single action-keyed mapping replacing the per-action flat
  attributes. Read actions (`"list"`, `"retrieve"`) accept a bare callable
  (the selector); write actions (`"create"`, `"update"`, `"destroy"`)
  accept a `ServiceSpec`.

### Changed

- **Breaking.** Removed flat per-action attributes from viewset mixins:
  `list_selector`, `retrieve_selector`, `create_service`,
  `create_input_serializer`, `create_output_serializer`,
  `create_output_selector`, `create_atomic`, and the matching `update_*`
  / `destroy_*` triplets. Move the values into `service_specs` instead.
- **Breaking.** Standalone mutation views (`ServiceCreateView`,
  `ServiceUpdateView`, `ServiceDeleteView`) no longer accept individual
  flat attributes (`service`, `input_serializer`, …). They are now
  configured by setting a single `spec` class attribute to a
  `ServiceSpec`.
- **Breaking.** `@service_action` now takes a `ServiceSpec` as its first
  positional argument instead of `service=`/`input_serializer=`/etc.
  kwargs. DRF-action options (`detail`, `methods`, `url_path`,
  `url_name`, plus extras) are unchanged.

## [0.2.0] — 2026-04-27

### Changed

- Renamed `input_dataclass` → `input_serializer` everywhere it's configured
  (mutation views, viewset mixins, `@service_action`). The attribute now
  accepts a bare dataclass type (wrapped in `DataclassSerializer` on the
  fly), a `DataclassSerializer` subclass, or any other `Serializer`
  subclass (e.g. `ModelSerializer`).
- Services now receive the serializer's `validated_data` as `data` (a
  dataclass instance for dataclass-based serializers, a `dict` for plain
  `Serializer` / `ModelSerializer` subclasses) instead of the result of
  `serializer.save()`. Persistence is the service's responsibility.

## [0.1.0] — 2026-04-27

First public release. A service / selector layer for Django REST Framework:
views, viewsets, mutation helpers, and a scaffolding command, with
first-class sync + async support and 100% test coverage.

### Added

#### Mutation helpers (`rest_framework_services.mutations`)

- `apply_input(instance, data, ...)` — set attributes in memory, no save.
- `create_from_input(model, data, ...)` — build, save, optional M2M.
- `update_from_input(instance, data, ...)` — diff in-memory state vs. input,
  call `save(update_fields=[...])` with only the fields that actually
  changed. Defaults can be overridden per call.
- `acreate_from_input` / `aupdate_from_input` — async siblings using Django
  4.2+ `asave()` / `aset()`.
- All accept `data` as a dataclass / dict / `__dict__`-bearing object, with
  `field_map`, `exclude_fields`, and `m2m` kwargs.
- All return a `ChangeResult` (frozen dataclass) carrying `instance`,
  `created`, and a tuple of `FieldChange` records, plus a `changed_fields`
  property and `get_field_change(name)` lookup.
- The `UNSET` sentinel distinguishes "field omitted from input" from "field
  explicitly set to `None`".

#### Standalone views (`rest_framework_services.views`)

- `ServiceCreateView`, `ServiceUpdateView`, `ServiceDeleteView` — single-
  purpose `GenericAPIView` subclasses, each composing `MutationFlowMixin`.
  Configure via `service`, `input_serializer`, `output_serializer`,
  `output_selector`, `atomic`, `success_status`.
- `SelectorListView`, `SelectorRetrieveView` — built on DRF's
  `ListModelMixin` / `RetrieveModelMixin`. `selector` overrides
  `get_queryset()` / `get_object()`; everything else (filter backends,
  pagination, serialization) is vanilla DRF.

#### Viewsets (`rest_framework_services.viewsets`)

- `ServiceViewSet` — full-CRUD viewset composed of `ServiceCreateMixin`,
  `ServiceUpdateMixin`, `ServiceDestroyMixin`, `SelectorListMixin`,
  `SelectorRetrieveMixin`, and `MultiSerializerMixin`.
- `SelectorViewSet` — read-only viewset (list + retrieve only).
- All per-action mixins are exported so you can compose only the actions
  you need.
- `MultiSerializerMixin` — per-action serializer dispatch via a single
  `serializer_classes: dict[str, type[Serializer]]` mapping; falls back to
  DRF's `serializer_class` when the action isn't mapped.
- `MutationFlowMixin` — exported as the building block for service-backed
  action flow on bespoke shapes that don't fit the existing five mixins.
- `@service_action` — decorator wrapping DRF's `@action` and routing the
  custom action through the same validate-dispatch-render flow as the
  standard mutations.

#### Selector protocols (`rest_framework_services.selectors`)

- `Selector` and `AsyncSelector` — `runtime_checkable` Protocols for
  type-safe documentation of selector callables.

#### Shared value types (`rest_framework_services.types`)

- `ChangeResult`, `FieldChange`, `UNSET`. Framework-agnostic; live in their
  own package and are re-exported at the top level.

#### Exceptions (`rest_framework_services.exceptions`)

- `ServiceError` and `ServiceValidationError` — framework-agnostic
  exceptions raised from service code. The view boundary translates them:
  `ServiceValidationError` → DRF `ValidationError` (HTTP 400); `ServiceError`
  → `APIException` (HTTP 422).
- Services do not import from `rest_framework`.

#### Sync + async dispatch

- Services and selectors can be `async def`; `inspect.iscoroutinefunction`
  detects them and the view bridges to `async_to_sync` under sync
  dispatch.
- Atomic transactions wrap async services via
  `sync_to_async(thread_sensitive=True)` to keep ORM connection state on a
  consistent thread.

#### Atomic transactions

- Every mutation service runs inside `transaction.atomic()` by default.
- Opt-out per view via `atomic = False` (or per-action via
  `create_atomic` / `update_atomic` / `destroy_atomic`).

#### `startserviceapp` management command

- Subclass of `django.core.management.templates.TemplateCommand`.
- Scaffolds a service-oriented Django app with `services/`, `selectors/`,
  `validators/`, `serializers/`, `utils/` as packages, plus `models/` and
  `views/` as packages instead of single files.
- The bundled template lives at
  `rest_framework_services/management/templates/service_app/` and ships
  in the wheel. Add `"rest_framework_services"` to `INSTALLED_APPS` to
  make the command discoverable.

#### Examples and documentation

- `README.md` — quick start (DataclassSerializer-first, ModelSerializer
  shown as alternative), mental model, mutation helpers, views, viewsets,
  errors, atomic, async, `startserviceapp`.
- `examples/` — runnable Django project with an invoices app demonstrating
  the full surface and an end-to-end `APITestCase`.

### Compatibility

- Python 3.10 – 3.14
- Django 4.2, 5.0, 5.1, 5.2, 6.0
- Django REST Framework ≥ 3.14
- `djangorestframework-dataclasses` (hard dependency)

### Quality gates

- 100% line + branch coverage enforced via `pytest-cov`.
- Type-checked with [`ty`](https://github.com/astral-sh/ty).
- Linted and formatted with [`ruff`](https://github.com/astral-sh/ruff).
- CI matrix runs the full Python × Django product on every push.

[Unreleased]: https://github.com/Artui/djangorestframework-services/compare/v0.43.0...HEAD
[0.43.0]: https://github.com/Artui/djangorestframework-services/compare/v0.42.0...v0.43.0
[0.42.0]: https://github.com/Artui/djangorestframework-services/compare/v0.41.0...v0.42.0
[0.41.0]: https://github.com/Artui/djangorestframework-services/compare/v0.40.0...v0.41.0
[0.40.0]: https://github.com/Artui/djangorestframework-services/compare/v0.39.0...v0.40.0
[0.39.0]: https://github.com/Artui/djangorestframework-services/compare/v0.38.0...v0.39.0
[0.38.0]: https://github.com/Artui/djangorestframework-services/compare/v0.37.0...v0.38.0
[0.37.0]: https://github.com/Artui/djangorestframework-services/compare/v0.36.1...v0.37.0
[0.36.1]: https://github.com/Artui/djangorestframework-services/compare/v0.36.0...v0.36.1
[0.36.0]: https://github.com/Artui/djangorestframework-services/compare/v0.35.0...v0.36.0
[0.35.0]: https://github.com/Artui/djangorestframework-services/compare/v0.34.0...v0.35.0
[0.34.0]: https://github.com/Artui/djangorestframework-services/compare/v0.33.0...v0.34.0
[0.33.0]: https://github.com/Artui/djangorestframework-services/compare/v0.32.0...v0.33.0
[0.32.0]: https://github.com/Artui/djangorestframework-services/compare/v0.31.0...v0.32.0
[0.31.0]: https://github.com/Artui/djangorestframework-services/compare/v0.30.0...v0.31.0
[0.30.0]: https://github.com/Artui/djangorestframework-services/compare/v0.29.1...v0.30.0
[0.29.1]: https://github.com/Artui/djangorestframework-services/compare/v0.29.0...v0.29.1
[0.29.0]: https://github.com/Artui/djangorestframework-services/compare/v0.28.1...v0.29.0
[0.28.1]: https://github.com/Artui/djangorestframework-services/compare/v0.28.0...v0.28.1
[0.28.0]: https://github.com/Artui/djangorestframework-services/compare/v0.27.0...v0.28.0
[0.27.0]: https://github.com/Artui/djangorestframework-services/compare/v0.26.0...v0.27.0
[0.26.0]: https://github.com/Artui/djangorestframework-services/compare/v0.25.1...v0.26.0
[0.25.1]: https://github.com/Artui/djangorestframework-services/compare/v0.25.0...v0.25.1
[0.25.0]: https://github.com/Artui/djangorestframework-services/compare/v0.24.1...v0.25.0
[0.24.1]: https://github.com/Artui/djangorestframework-services/compare/v0.24.0...v0.24.1
[0.24.0]: https://github.com/Artui/djangorestframework-services/compare/v0.23.0...v0.24.0
[0.23.0]: https://github.com/Artui/djangorestframework-services/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/Artui/djangorestframework-services/compare/v0.21.1...v0.22.0
[0.21.1]: https://github.com/Artui/djangorestframework-services/compare/v0.21.0...v0.21.1
[0.21.0]: https://github.com/Artui/djangorestframework-services/compare/v0.20.0...v0.21.0
[0.20.0]: https://github.com/Artui/djangorestframework-services/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/Artui/djangorestframework-services/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/Artui/djangorestframework-services/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/Artui/djangorestframework-services/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/Artui/djangorestframework-services/compare/v0.15.1...v0.16.0
[0.15.1]: https://github.com/Artui/djangorestframework-services/compare/v0.15.0...v0.15.1
[0.15.0]: https://github.com/Artui/djangorestframework-services/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/Artui/djangorestframework-services/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/Artui/djangorestframework-services/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/Artui/djangorestframework-services/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/Artui/djangorestframework-services/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/Artui/djangorestframework-services/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/Artui/djangorestframework-services/compare/v0.8.1...v0.9.0
[0.8.1]: https://github.com/Artui/djangorestframework-services/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/Artui/djangorestframework-services/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/Artui/djangorestframework-services/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/Artui/djangorestframework-services/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Artui/djangorestframework-services/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Artui/djangorestframework-services/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Artui/djangorestframework-services/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Artui/djangorestframework-services/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Artui/djangorestframework-services/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Artui/djangorestframework-services/releases/tag/v0.1.0
