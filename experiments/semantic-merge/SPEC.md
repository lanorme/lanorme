# Semantic merge evidence: design

The design of the spike behind issue 69: can LaNorme give grounded evidence
about whether the modules of a package belong together, so that a
`SHALLOW-001` finding is better informed or more precise, with the standard
library only? `README.md` has what came out.

## Why the corpus alone cannot answer it

`SHALLOW-001` is scored on `evals/corpora/shallow_modules/`. Its third-party
cases are structural replicas: file names, imports, line counts and docstring
spans are kept, bodies are replaced with neutral code, and a project's
importers are folded into one `case_importers.py`. A replica therefore has no
vocabulary, no real importer sets and no history. Every signal here is
measured on the original tree a case replicates, at the commit its label
names, or on the case itself when it is verbatim (the 18 agent A/B snapshots).
Hand-written trap cases hold neutral code by design and are left out, as is
the one standard-library case.

## Population

A labelled package is a merge candidate when a merge is physically possible:
at least two members, as `SHALLOW-001` counts them, totalling at most the
rule's merge budget (200 lines at the defaults). The label is the corpus's:
true when merging the package leaves the design no worse and breaks nothing,
false otherwise. Four populations are compared:

| Population | Packages | What the comparison asks |
|---|---|---|
| `candidates` | every labelled merge candidate | does the signal track the label at all |
| `structural` | candidates the rule's structural conditions pass | where only meaning is left to decide, does the signal decide |
| `reported` | packages the rule reports on the original trees | does the signal tell the rule's true positives from its false ones |
| `latest` | candidates, each A/B run's package once | do repeated stages carry the result |

## Signals

Each is a number per package, computed in `harness/`.

| Family | Signal | Definition | Grounding |
|---|---|---|---|
| Common Reuse | `used_together`, `mean_share_used` | of the production modules outside the package that import a member, the share importing two or more, and the mean share of the members each imports | [@martin2002agile, ch. 20] |
| Common Reuse | `importer_similarity` | mean pairwise Jaccard similarity of the members' importer sets | [@martin2002agile, ch. 20] |
| Internal coupling | `internal_density`, `connected_share` | share of member pairs joined by an import; share of members in the largest group so joined | [@ousterhout2018philosophy, ch. 9] |
| Vocabulary | `identifier_cohesion` | mean pairwise cosine similarity of the members' identifier terms, weighted by term frequency and inverse document frequency | [@marcus2005conceptual; @kuhn2007semantic; @salton1988term] |
| Vocabulary | `identifier_lift`, `identifier_overlap` | the cohesion minus the mean similarity to modules outside the package; the unweighted Jaccard overlap of term sets | [@kuhn2007semantic] |
| Vocabulary | `prose_cohesion`, `prose_lift` | the same with the words of docstrings and comments added | [@kuhn2007semantic] |
| Siblings | `fan_out_similarity` | mean pairwise Jaccard similarity of what the members import from outside the package | [@beck2011congruence] |
| Siblings | `role_similarity` | mean pairwise Jaccard similarity of the kinds of top-level definition each member holds; exploratory, this spike's own | none |
| Co-change | `changed_together`, `cochange_similarity` | of the commits changing a member, the share changing two or more; mean pairwise Jaccard similarity of commit sets | [@gall1998logical; @zimmermann2005mining] |

An import of the package itself reaches a member through `__init__.py` and is
attributed to the member the name is re-exported from. Co-change leaves out
merge commits, commits touching more than 30 files and each file's own
addition, and is undefined below three remaining commits.

## Statistics

Separation is the area under the receiver operating characteristic curve
[@hanley1982meaning]: the probability that an over-split package scores higher
than one of the rest. The packages are clustered (several per repository, one
A/B package at three stages), so the 95% interval is a cluster bootstrap over
lineages [@efron1993bootstrap], 2,000 resamples.

## Discipline

The dev split was measured first and every definition above was fixed on it.
The holdout was measured once, afterwards, with the hypotheses below already
committed. The holdout's labels and notes were read while mapping the corpus
onto the original trees; no holdout signal value was computed before this file
was committed. No corpus file or label is changed by this spike.

## Holdout hypotheses, fixed on dev

The issue expected evidence of belonging together to mark the packages to
merge. On dev the direction is the reverse: over-split packages are the less
cohesive ones.

1. **Vocabulary.** `identifier_cohesion` is lower in over-split packages than
   in the rest of the `structural` population (AUC below 0.5; dev 0.11).
2. **Internal coupling.** `internal_density` is lower in over-split packages
   in the `structural` population (dev 0.18).
3. **Common Reuse.** `importer_similarity` and `used_together` do not
   separate the groups in the `structural` population (interval covering 0.5;
   dev 0.46 and 0.51).
4. **A parallel-sibling guard.** Treating a package whose
   `identifier_cohesion` is at least 0.176 as a deliberate convention, the
   threshold with the best balanced accuracy on the dev `structural`
   population, removes more of the rule's false positives than true positives
   on the holdout `reported` population (dev: all 5 false positives, 3 of 13
   true positives). The secondary guard is `identifier_overlap` at least
   0.158 (dev: 5 of 5 and 1 of 13).
5. **Co-change.** Fewer than half the holdout candidates have enough history
   for a co-change signal (dev: 22 of 48, 4 of the 17 over-split).

## Added after the holdout

The holdout did not confirm the dev pattern, so the evidence base was
widened. These parts were designed after the holdout result and are
exploratory:

- **Extension set.** Every structurally passing merge candidate, at most
  eight per project by a seeded draw, from 20 application repositories the
  corpus does not cover (14 have any).
- **Blind ratings.** Independent agents rate each corpus candidate once and
  each extension candidate twice on three levels (`merge`, `contested`,
  `keep`) with `prompts/label.md`, seeing no rule, signal or corpus label.
  Agreement is Cohen's kappa [@cohen1960coefficient]; the signals, and the
  two size measures the rule already reads, are compared against the
  ratings.
- **Agent decisions.** The findings `SHALLOW-001` reports on the rated
  packages are decided by agents acting as each project's maintainer
  (`prompts/decide.md`), twice with the message as it is and twice with one
  sentence of Common Reuse evidence appended; no agent sees both arms.

## Corrected after review

An independent adversarial review of the first full run reproduced every
reported number and found four measurement faults, corrected before the
results in `README.md`:

- **Reuse.** The import graph records every `__init__.py` Python runs on the
  way to a submodule, so a package file holding code was counted as used by
  every importer of any member. It now counts only for an import that names
  something the file defines. The dev values of the hypotheses' signals are
  unchanged to two decimals.
- **Co-change.** History now follows a file through exact renames; without
  that, a package moved as a whole lost its history.
- **Congruence.** Migration directories are left out and each directory
  contributes at most 30 pairs; before, one flat migration directory
  supplied most of a tree's pairs and inflated the similarity signals. A
  direct import's rate is reported beside its AUC, which cannot leave 0.5
  for a rare yes or no.
- **Clustering.** Modules with no import edge are left out; the climb never
  moves them, so they reported the noise of the random starts as
  instability. Snapshots and repositories are summarised apart.

## Invented signals: boundary work

The signals above ask whether the members of a package resemble each other,
and resemblance is as true of a deliberate convention as of an over-split.
After the first results a second family was designed that asks what each
module boundary does. A module is a namespace and an information-hiding unit
[@parnas1972criteria], so merging a package is simulated on the names its
members define and the names their clients import:

| Signal | Definition | Grounding |
|---|---|---|
| `idle_share` | share of members whose boundary does no work: no statement in any function body (nothing hidden), no client importing the member as a module object (no namespace in use), no name another member also defines (nothing kept apart) | [@parnas1972criteria; @ousterhout2018philosophy, ch. 4] |
| `scope_co_use` | of the client functions that reference any member's names, the share referencing two or more members | Common Reuse at the granularity of a function [@martin2002agile, ch. 20] |
| `merge_evidence` | `idle_share` plus `scope_co_use` (0 when no client function references the package) | this spike's own |
| `has_qualified_use`, `has_collision` | some client imports a member as a module object; two members define a name in use | a merge precondition [@opdyke1992refactoring] |
| `depth`, `facade_use_share`, `sibling_only_names`, `merge_saving`, `interface_shrink`, `hops`, `signature_coupling`, `name_echo` | exploratory variants, defined in `harness/member_surfaces.py`, `boundary_signals.py` and `idle_boundaries.py` | see those modules |

These were designed with every package of the dev split, the holdout and the
extension in view, so those 135 packages are development data for them. On
the 88 rated packages the structural conditions pass (17 merge, 47
contested, 24 keep):

| Measure | Merge against keep | Merge against contested | Merge against the rest |
|---|---|---|---|
| `merge_evidence` | 0.80 [0.62, 0.91] | 0.67 [0.48, 0.82] | 0.71 [0.55, 0.84] |
| `idle_share` | 0.74 [0.57, 0.84] | 0.66 [0.51, 0.78] | 0.69 [0.55, 0.79] |
| `scope_co_use` | 0.79 [0.62, 0.92] | 0.68 [0.56, 0.82] | 0.71 [0.58, 0.84] |
| lines per member | 0.18 [0.08, 0.38] | 0.41 [0.26, 0.61] | 0.33 [0.21, 0.52] |

None of the 10 packages with a member used as a namespace is rated merge,
against 17 of the other 78. `interface_shrink`, `signature_coupling` and
`name_echo` do not separate and are kept only as recorded.

## Confirmation set

Fixed before any of its ratings exist; the analysis is
`harness/report_confirmation.py`, committed with this section.

- **Repositories.** 70 public repositories no earlier step of this spike
  read; the 43 that hold a candidate are listed with their commits in
  `results/confirmation_candidates.json`.
- **Candidates.** Every merge candidate the structural conditions pass, at
  most six per repository by the seeded draw of `select_candidates.py`:
  122 packages.
- **Ratings.** Two blind raters, two different models, with
  `prompts/label_confirmation.md`: the first prompt without the members'
  line counts and without the sentence framing the package as fitting one
  module of about 200 lines. A package is merge if either rater says so and
  keep if both do; as a check, merge only if both say so.

Hypotheses:

1. **Primary.** `merge_evidence` is higher in packages rated merge than in
   the rest: AUC above 0.5 with the interval's lower end above 0.5.
2. `idle_share` is higher in merge than in keep.
3. `scope_co_use` is higher in merge than in keep.
4. Packages with a member used as a namespace are rated merge less often
   than the others.
5. `cochange_similarity` is higher in merge than in keep, on the packages
   that have it.
6. The comparators behave as before: `identifier_cohesion` and
   `used_together` do not separate merge from the rest (interval covering
   0.5), and lines per member is lower in merge than in keep.

If the primary hypothesis holds, the proposal for `SHALLOW-001`, to be made
as its own change, is to state the evidence in the finding and to leave out a
package whose merge precondition fails. If it does not, the boundary-work
signals join the others as not confirmed.

After the registered run a gap was found in the measurement and closed: a
name a client imports from the package, defined in the package's own
`__init__.py`, was attributed to no member. `README.md` gives both the
registered and the corrected figures; no verdict depends on which.

## Co-location model

Two rounds failed against raters who agree at a kappa near 0.3. The third
changes the criterion to one nobody disputes: what authors did. A module is a
set of top-level definitions (functions, classes, constants) its author kept
together, and a boundary between sibling modules is one the author drew. For
two definitions of one package, "same module or not" is known by
construction. A module split mechanically, one definition per file, is a
package whose every cross-module pair was a same-module pair, so a model
that recognises same-module pairs recognises that over-split, at the unit
(a pair) the raters' reasons pointed at.

- **Features** of a pair, none depending on the file layout
  (`harness/definition_pairs.py`): whether one names the other, the overlap
  of the package definitions naming each, the overlap of the client scopes
  naming each, vocabulary similarity, fan-out similarity, same kind, a
  shared base class, shared name words, and whether either has a client.
- **Model** (`harness/logistic.py`): a ridge-penalised logistic regression,
  fitted with the standard library, nine weights and an intercept.
- **Development data**: 58,932 pairs from the 40 repositories of the first
  round, capped per module, per package and per repository. Leaving each
  repository out of the fit in turn, the model's AUC on the left-out
  repository has a median of 0.74 (quartiles 0.71 and 0.77), above 0.5 in 39
  of 40; 0.76 on the pairs of packages of at most 200 lines. The best single
  feature, vocabulary, has a median of 0.69.
- **Frozen model**: `results/colocation_model.json`, fitted on all 40.
- **Package score**: the mean probability the model gives a package's
  cross-module pairs (`harness/score_packages.py`).

Test, fixed before the frozen model has scored any of it: the 70
repositories of the confirmation set, none used to fit or choose the model,
and the 18 agent A/B snapshots.

1. **Primary.** On the test repositories the frozen model tells same-module
   pairs from sibling-module pairs with a median AUC of at least 0.65, and
   above 0.5 in at least four of five repositories.
2. The same holds on the pairs of packages of at most 200 lines.
3. **Over-splits by construction.** In at least three of four test
   repositories, the cohesion score of small modules (their boundary score if
   split one definition per file) is above the boundary score of small
   packages as their authors split them (AUC above 0.5).
4. **Agent over-splits.** The snapshots of the A/B arm told to use LaNorme
   have a higher boundary score than those of the control arm (AUC of the
   arms at least 0.75).
5. **Raters, secondary.** On the confirmation ratings the boundary score is
   higher in packages rated merge than in the rest. The ratings are an
   unreliable criterion (one rater predicts the other at 0.70), so this is
   reported whichever way it falls and decides nothing alone.

## Further experiments

- **Congruence at scale.** For every original tree, how well each pairwise
  signal predicts that two modules share a package, the authors' own
  grouping, as in [@beck2011congruence]. It validates the instruments on
  thousands of pairs where the labelled packages are a few dozen.
- **Clustering.** A Bunch-style search maximising Modularization Quality over
  the import graph [@mitchell2006bunch], on the A/B snapshots and the
  benchmark corpora: its stability across restarts, its agreement with the
  authors' packages, and whether it groups the members of labelled over-split
  packages.
- **Agent decisions.** Whether the evidence, added to the finding's message,
  changes what an agent does with it (designed above, after the holdout).
