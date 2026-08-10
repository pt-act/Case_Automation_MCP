# Generalization & Open-Source Plan
### Turning Case Automation MCP into a domain-agnostic, portfolio-grade showcase

**Goal:** Decouple the engine from immigration law, ship it public on GitHub as a
*domain-configurable AI case-automation platform* with immigration as the first
reference "pack" — proving the architecture generalizes, and showcasing senior
engineering depth to future opportunities.

**Key insight:** The original build was *designed* to be domain-agnostic. The
domain model header already states *"immigration-specific categories are expressed
via config/rule data, never hard-coded."* This is a **decoupling and packaging
exercise, not a rewrite.** No phase below touches the core architecture.

---

## 1. Repositioning

| | Before (job application) | After (public showcase) |
|---|---|---|
| Pitch | "MCP server for a US immigration law firm" | "Domain-configurable engine for any caseworked, deadline-driven, document-heavy practice" |
| Immigration | The product | The **reference pack** that proves the model |
| Audience | One hiring manager | Recruiters, engineering leaders, OSS community |
| Headline | "I built your platform" | "Production-grade AI automation architecture — 424 tests, A− audited" |

The one-line README pitch:

> An MCP server that gives any professional-services practice — legal, accounting,
> claims, compliance — a permissioned, audited, reversible set of AI "hands" to
> draft, file, extract, route, remind, and verify across every system. Configure a
> domain pack; the engine stays the same.

---

## 2. Coupling map — exactly where immigration leaks in

Traced across the codebase. It is shallow and lives in five named places, all
already designed to be swappable:

| # | Location | What's immigration-specific | Generalization |
|---|---|---|---|
| 1 | **Vocabulary** | `Matter`, `privilege`, `practice_area`, the `cam` package name | Keep names (already generic in legal/consulting/insurance); genericize docstrings; alias where helpful |
| 2 | **`intake/config.py`** | `family-based`, `employment-based` case types; I-130 tasks | Move to `packs/immigration`; ship a neutral example pack |
| 3 | **`qc/checks/privilege.py`** + `Document.privileged` | Attorney–client privilege as the highest-stakes, fail-closed gate | Generalize to a configurable **restriction policy** (fail-closed by default); immigration maps privilege → restricted |
| 4 | **`obs/observability.py`** | PII regexes: A-number, SSN, passport (already a *mutable list* with a "configurable" comment) | Packs supply their own PII pattern set |
| 5 | **Rule/enum data** | Deadline rules (RFE/EOIR — already marked illustrative), document classes, QC packet kinds, RBAC roles | Move enumerations into pack config; engine reads them |

Everything else — orchestrator, audit chain, connectors, deadline math, RBAC
mechanism, crypto, observability plumbing — is already domain-neutral.

---

## 3. The "domain pack" abstraction

Introduce one new concept: a **domain pack** — a self-contained bundle of
everything domain-specific, loaded into the neutral engine at startup.

```
src/cam/packs/
├── base.py                 # DomainPack protocol (the contract)
├── immigration/            # reference pack (extracted from today's hard-coded values)
│   ├── pack.py             # assembles the pack
│   ├── terminology.py      # labels: "Matter", "Client", "Restricted"=Privileged
│   ├── case_types.py       # family-based, employment-based, ...
│   ├── deadline_rules.py   # RFE windows, EOIR (illustrative)
│   ├── document_classes.py # court_filing, id_document, form_filing, ...
│   ├── pii_patterns.py     # A-number, SSN, passport
│   ├── restriction.py      # attorney–client privilege policy (fail-closed)
│   └── rbac.py             # attorney, paralegal, intake_coordinator, ...
└── consulting/             # a SECOND minimal pack — proves agnosticism
    ├── pack.py
    ├── terminology.py      # "Engagement", "Client", "Confidential"
    ├── case_types.py       # e.g. audit, advisory, onboarding
    └── ...
```

```python
# src/cam/packs/base.py
class DomainPack(Protocol):
    name: str
    terminology: Terminology          # display labels for Matter/Contact/etc.
    case_types: dict[str, CaseTypeConfig]
    deadline_rules: list[DeadlineRule]
    document_classes: list[str]
    pii_patterns: list[re.Pattern[str]]
    restriction_policy: RestrictionPolicy  # the generalized privilege gate
    rbac_roles: dict[str, frozenset[str]]
```

This reuses machinery the codebase **already has**:
- The roadmap's Phase 5.2 *tenant-scoped config* and 5.3 *entry-point connector
  marketplace* are the same loading pattern — a pack can be a Python entry point.
- `set_intake_config()`, the mutable `_pii_patterns`, and the `@register_check`
  registry already accept injected config. Packs just become the injector.

---

## 4. The one piece that needs real care: the restriction gate

`PrivilegeCheck` is the highest-stakes check — fail-closed, never warns, blocks any
privileged document reaching an external recipient. **Generalizing must not weaken
it.** The rule:

- Rename the *concept* from `privileged` → `restricted` (a neutral, configurable flag).
- The gate becomes a `RestrictionPolicy` that **defaults to fail-closed** exactly as
  today. A pack supplies the label and any extra semantics.
- Immigration pack: `restricted == attorney–client privilege`, identical behavior.
- Consulting pack: `restricted == client-confidential`. Same fail-closed mechanics.

Net: identical safety guarantees, now domain-labeled. Keep the property-based test
that enforces "any external-bound restricted doc → block" — it's your strongest
correctness signal and a great thing for reviewers to see.

---

## 5. Execution phases

### Phase A — Decouple (the core work, ~3–5 focused days)
1. Add `packs/base.py` (`DomainPack`, `Terminology`, `RestrictionPolicy`).
2. Move immigration values out of `intake/config.py`, `observability.py`,
   `privilege.py`, deadline/routing config → `packs/immigration/`.
3. Generalize `Document.privileged` → `restricted` (+ compat alias); rewire
   `PrivilegeCheck` to read the pack's restriction policy.
4. Wire pack selection at startup (`CAM_DOMAIN_PACK=immigration` env/setting).
5. Keep all 424 tests green; immigration pack must reproduce today's behavior 1:1.

### Phase B — Prove agnosticism (~1–2 days)
6. Build a second minimal pack (`consulting` or `claims`) — case types, terminology,
   one deadline rule, a confidentiality policy.
7. Add a small test suite running the same intake/QC/deadline flows under the second
   pack. **This is the headline proof for reviewers:** same engine, two domains.

### Phase C — Showcase & open-source (~1–2 days)
8. Rewrite README around the domain-agnostic pitch (§1) + the quality scorecard
   (91/100, A−), architecture diagram, and the two-pack proof.
9. Add `examples/` with a runnable end-to-end demo (intake → QC → routing) per pack,
   ideally a short terminal GIF or asciinema.
10. Write a `docs/CASE_STUDY.md` / blog post: the problem, the architecture decisions
    (ports-and-adapters, durable state machine, hash-chained audit, fail-closed
    gates), and the generalization. This is the artifact recruiters actually read.
11. Polish repo hygiene: pin it on your GitHub profile, topics/tags, a clean
    `CONTRIBUTING.md` (already present), badges (CI, license, coverage), and link the
    live docs site.

---

## 6. Naming decision (low stakes, do it once)

`cam` / "Case Automation MCP" is fine and already neutral. If you want a stronger
public brand, options: **CaseFlow**, **MatterMCP**, **Caseworks**, **OpenCase**.
Renaming the Python package is mechanical (one find-replace + import update) but
touches every file — decide before Phase C so the public history is clean. My
recommendation: keep `cam` internally, present it publicly as a named project in the
README. Don't let a rename block the launch.

---

## 7. What to deliberately NOT do

- **Don't chase "any domain" literally.** Two well-built packs (immigration +
  one more) prove generality far better than a vague config-for-everything. Depth reads
  as senior; breadth-without-depth reads as a toy.
- **Don't weaken the restriction/audit/gate guarantees** to make things generic.
  Those guarantees *are* the portfolio story.
- **Don't strip the immigration pack.** It's your most complete, realistic example —
  keep it as the flagship reference.

---

*Engine stays the same. Domain becomes data. Immigration becomes the proof, not the cage.*
