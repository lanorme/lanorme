# Semantic merge evidence: results

Can LaNorme give grounded evidence about whether the modules of a small
package belong together, so that a `SHALLOW-001` finding is better informed or
more precise, with the standard library only? This is the spike behind issue
69. `SPEC.md` has the design; this page has what came out.

## Headline

Three rounds were run. The first two tried to predict which packages raters
would merge, and failed; the third changed the criterion and holds.

- **Against raters, no signal identifies the packages to merge on fresh
  data.** Round one measured the signals of the literature (Common Reuse,
  internal coupling, vocabulary, sibling similarity, co-change) on 135
  packages from 34 projects. Round two invented signals for what a module
  boundary does, froze them with their hypotheses, and tested them on 122
  packages from 43 projects not read before. Every hypothesis failed: the
  primary measure separates merge from the rest at an AUC of 0.49 (interval
  0.36 to 0.62; 0.51 once a gap in the measurement was closed) where it had
  shown 0.71 on the data it was designed on. Each round's leads died in the
  next: vocabulary cohesion (dev 0.11, holdout 0.49), co-change (0.80, then
  0.51), and size once raters were not shown line counts (0.18, then 0.45).
- **The raters were the limit.** Two blind raters agree on merge-or-not with
  a kappa of 0.32 and 0.27, and one rater's rating predicts the other's
  merge at an AUC of 0.70 to 0.74.
- **What authors do is predictable, and that held on fresh data.** Round
  three took as ground truth where authors put their code: two definitions
  in one module, or in sibling modules. A logistic model of nine
  layout-invariant features, fitted with the standard library on 40
  projects, was frozen and then scored on 69 projects it had never seen. It
  tells same-module pairs from sibling-module pairs at a median AUC of 0.77,
  above chance in all 69, and tells a module split one definition per file
  from a package its author split at a median of 0.86. Three of its five
  hypotheses, fixed in advance, hold.
- **By that standard the agent packages the rule was built on are not
  over-split.** The hypothesis that the A/B arm told to use LaNorme draws
  boundaries through more related code failed in reverse: its boundaries
  separate less related pairs than the control arm's (0.42 against 0.46)
  and look like ordinary author boundaries (0.40). With the blind judge of
  the A/B experiment, the blind raters and the agent decisions below, that
  is four independent readings that those packages are not clear over-splits.
- **The model does not predict the raters either.** Its package score is at
  0.49 against the confirmation ratings. It measures whether a boundary
  cuts through code authors keep together, which is one kind of over-split
  and not what raters mostly meant.
- **Evidence in the message shows no effect on decisions,** and
  **clustering the import graph is not worth a tool** (agreement with
  authors' packages 0.15, with itself between starts 0.57).
- **A merge precondition fails for 6 of the 63 packages the rule reports.**
  Two members define the same name, or a client uses a member as a
  namespace, so the suggested merge needs renames, whatever a rater thinks.

The recommendation is at the end: leave `SHALLOW-001`'s conditions and message
as they are, build no clustering tool, and take up the follow-ups listed
there in separate changes.

## What was measured

The corpus's third-party cases are structural replicas with neutral bodies, so
every signal is computed on the original tree a case replicates, at its pinned
commit, or on the case itself when it is verbatim (the 18 agent A/B
snapshots).

| Set | Projects | Merge candidates | Labels |
|---|---|---|---|
| dev | 16 (13 repositories, 3 A/B runs) | 48 | the corpus's, unblinded |
| holdout | 4 repositories | 33 | the corpus's, blind |
| extension | 14 repositories outside the corpus | 54 | two blind raters, this spike |

A merge candidate has at least two members within the rule's merge budget.
The `structural` population is the candidates the rule's structural
conditions pass, where only meaning is left to decide: 24 on dev, 18 on the
holdout, all 54 of the extension. Separation is the area under the receiver
operating characteristic curve (AUC): 0.5 is none, above 0.5 means the signal
is higher in over-split packages. Intervals are cluster bootstraps over
projects; with a handful of projects they are a range of the few possible
resamples, not a 95% interval.

## Directions

Each direction the issue lists, with its measured result or the reason it was
dropped.

| Direction | Result |
|---|---|
| Package design principles: Common Reuse, Common Closure [@martin2002agile, ch. 20] | Measured as the reuse and co-change signals below. Neither replicates. |
| Design criteria: shared information, used together [@ousterhout2018philosophy, ch. 9] | Measured as internal coupling and reuse. Neither replicates. |
| Semantic clustering [@kuhn2007semantic; @marcus2005conceptual] | Measured as vocabulary cohesion. Reversed on dev, absent on the holdout and against blind ratings. |
| Co-change [@gall1998logical; @zimmermann2005mining] | Measured. Defined for 65 of the 120 first-round packages in a git checkout; separated merge from keep on 17 of them and is at chance on the 44 of the confirmation set. |
| Search-based clustering [@mancoridis1998clustering; @mitchell2006bunch] | Measured. Weakly related to the authors' packages and only moderately stable. |
| Multi-objective clustering [@praditwong2011clustering] | Dropped. It optimises the same cohesion and coupling objectives, and the single-objective search already scores above the authors' own partition on them in all 20 repositories while agreeing with it at 0.15: a better optimiser of those objectives moves away from the packages, not towards them. |
| Pattern-based clustering, ACDC [@tzerpos2000acdc] | Dropped, not measured. Its patterns are defined over a system's whole dependency graph, the granularity of the clustering experiment and not of a package of two to ten modules; it would be a second clustering experiment under the same evaluation, and the first gave no reason for one. |
| Structural plus semantic [@bavota2013modularization] | Dropped. A weighted combination adds a free parameter to fit on a few dozen packages, over two components that each fail to replicate. |
| Comparative evaluation [@garcia2013comparative; @lutellier2015comparing] | Used as the frame for the clustering result: both report low accuracy for every recovery technique against ground-truth architectures. |

## Signals against the corpus labels

Over-split against the rest, `structural` population. The holdout hypotheses
were committed before any holdout value was computed (`SPEC.md`).

| Signal | Dev | Holdout | Hypothesis fixed on dev |
|---|---|---|---|
| `identifier_cohesion` | 0.11 [0.00, 0.31] | 0.49 [0.40, 0.83] | lower in over-split: not confirmed |
| `internal_density` | 0.18 [0.00, 0.52] | 0.48 [0.27, 1.00] | lower in over-split: not confirmed |
| `importer_similarity` | 0.46 [0.00, 0.72] | 0.70 [0.65, 0.80] | no separation: not confirmed |
| `used_together` | 0.51 [0.23, 0.73] | 0.75 [0.68, 0.90] | no separation: not confirmed |
| `mean_share_used` | 0.29 [0.08, 0.52] | 0.85 [0.79, 1.00] | none stated |
| `fan_out_similarity` | 0.47 [0.12, 0.73] | 0.51 [0.45, 0.67] | none stated |
| `role_similarity` | 0.37 [0.07, 0.62] | 0.41 [0.08, 0.55] | none stated |
| `cochange_similarity` | 0.50 [0.00, 1.00], 7 packages | 0.25 [0.00, 1.00], 6 packages | none stated |

The holdout shows that the dev pattern is not general; it cannot establish
another. Its structural population is 18 packages from four repositories, and
only one of them holds both classes (fastapi-clean-example, 5 over-split and
5 not; polar and zulip give one over-split package each, python-ddd six that
are not). The reuse signals are defined for three of the four, so their
holdout intervals are the range of a few dozen resamples. On dev the
over-split packages are grab-bags (a domain layer of unrelated tiny modules)
and the rest are cohesive conventions, so cohesion marks what to keep. On the
holdout several over-split packages are parallel one-Protocol-per-file ports,
which are cohesive and used together.

The parallel-sibling guard fixed on dev (treat `identifier_cohesion` of at
least 0.176 as a deliberate convention) removed all 5 of the rule's false
positives and 3 of 13 true positives on dev. On the holdout it removes 2 of 3
false positives and 3 of 4 true positives. It is not a usable condition.

Co-change needs history. It is defined for 25 of the 33 dev candidates that
sit in a git checkout, 16 of 33 on the holdout and 24 of 54 in the extension.
The rest have fewer than three counted commits: the package is young, its
files were moved with edits (only exact renames are followed), or it changed
only in commits of more than 30 files. The 15 A/B candidates have no history
in the corpus, so nothing was measured on them; code an agent has just
written has none by nature.

## Blind ratings

`prompts/label.md` asked independent agents for one of three levels per
package: `merge`, `contested` (a recognisable convention reasonable
maintainers disagree about) or `keep`. They saw no rule output, signal or
corpus label; they did see each member's line count, and the prompt names
the things a merge must not break. Corpus candidates were rated once, each
A/B package at its last stage; extension candidates twice, by two different
models (`results/ratings.json` records which).

**Reliability**, as Cohen's kappa [@cohen1960coefficient]. The corpus label
is binary and counts a contested convention as false, so the first cut is the
corpus's own:

| Comparison | Packages | Kappa, merge or not | Kappa, keep or not |
|---|---|---|---|
| dev label against one blind rating | 40 | 0.24 | 0.52 |
| holdout label against one blind rating | 33 | 0.76 | 0.21 |
| two blind raters | 54 | 0.32 | 0.67 |

Of the 13 dev packages the corpus labels over-split, the blind rater calls 3
merge, 7 contested and 3 keep; of the holdout's 9, it calls 7 merge and 2
contested. The disagreement is over where the contested middle falls, and it
falls on opposite sides in the two splits. This is one rater, and two blind
raters agree on merge-or-not no better than 0.32 (on three levels, 0.56), so
the dev figure is within the noise of the yardstick.

**Signals against the ratings**, on the 88 rated packages the structural
conditions pass (17 merge, 47 contested, 24 keep; a package is merge if any
rater says so, keep if all do):

| Measure | Merge against keep | Merge against contested | Contested against keep |
|---|---|---|---|
| lines per member | 0.18 [0.08, 0.38] | 0.41 [0.26, 0.61] | 0.19 [0.10, 0.41] |
| lines | 0.16 [0.07, 0.32] | 0.37 [0.24, 0.54] | 0.26 [0.16, 0.44] |
| `identifier_cohesion` | 0.59 [0.37, 0.77] | 0.55 [0.39, 0.73] | 0.54 [0.27, 0.77] |
| `internal_density` | 0.48 [0.27, 0.71] | 0.55 [0.40, 0.69] | 0.42 [0.24, 0.63] |
| `importer_similarity` | 0.52 [0.35, 0.71] | 0.52 [0.39, 0.65] | 0.50 [0.39, 0.65] |
| `used_together` | 0.60 [0.43, 0.78] | 0.61 [0.48, 0.74] | 0.50 [0.38, 0.64] |
| `fan_out_similarity` | 0.54 [0.31, 0.71] | 0.54 [0.37, 0.74] | 0.51 [0.28, 0.66] |
| `role_similarity` | 0.45 [0.29, 0.59] | 0.40 [0.25, 0.57] | 0.56 [0.39, 0.73] |
| `cochange_similarity` | 0.80 [0.63, 1.00], 17 packages | 0.60 [0.38, 0.85], 29 packages | 0.67 [0.43, 0.87], 28 packages |

In this first round size separates keep from both other levels and does not
separate merge from contested, and co-change is the only evidence signal
with an interval clear of 0.5, merge against keep, on the 17 packages that
have it. Neither survives the second round (see the invented signals,
below). `used_together` and
`role_similarity` keep one direction across both splits and both merge
comparisons, without an interval that clears 0.5 in more than one of them.

**The rule against the ratings.** Of the 36 rated packages `SHALLOW-001`
reports on the original trees, 9 are rated merge, 22 contested and 5 keep.

## Congruence with the authors' packages

For each of the 20 corpus source projects, how well a pairwise signal tells
two modules of one package from two modules of sibling packages. The pair
comparison is this spike's own; the question is the one Beck and Diehl put to
16 Java projects with a different congruence measure [@beck2011congruence].
Migration directories are left out, and each directory contributes at most 30
pairs.

| Signal | Median AUC | Lowest | Highest |
|---|---|---|---|
| shared importers | 0.72 | 0.56 | 0.93 |
| vocabulary cosine | 0.71 | 0.10 | 0.84 |
| fan-out similarity | 0.68 | 0.11 | 0.84 |
| co-change | 0.64 | 0.50 | 0.73 |
| direct import | 0.59 | 0.46 | 0.70 |

A direct import is a rare yes or no, so its AUC stays near 0.5 whatever it
tracks: at the median, 21% of same-package pairs are joined by an import
against 2.5% of sibling-package pairs. Every signal carries information about
the packages authors make, none strongly. The lowest values of vocabulary and
fan-out are one feature-sliced project (clean-architecture), where a package
holds one feature's different roles and its sibling the same roles for
another feature: there similar modules are parallel siblings in different
packages. That is the case the merge question turns on, and it is why
similarity cannot answer it: a convention's parallel siblings resemble each
other, and the parts of one idea need not. The Java study found structural
dependencies, internal fan-out similarity and co-change the most congruent
and vocabulary second-rate though above its baseline.

## Clustering

`harness/modularization_quality.py` is a Bunch-style hill climb maximising
Modularization Quality [@mitchell2006bunch], 20 random starts per tree, over
the modules that import or are imported by another (an isolated module cannot
be placed by an import graph), migrations left out.

| Measure | 20 repositories | 18 A/B snapshots |
|---|---|---|
| Stability: median adjusted Rand index [@hubert1985comparing] between the local optima of different starts | 0.57 | 0.41 |
| Agreement of the best partition with the authors' directories, median | 0.15 | 0.07 (the 8 with several directories) |
| Trees where the search scores above the authors' own partition | 20 | 15 |

Across both, the best partition puts 28% of the member pairs of a labelled
over-split package in one cluster (22 packages) and 31% of those of the other
labelled candidates (51 packages). The search finds partitions that beat the
authors' on its own objective, different ones from different starts, weakly
related to the packages, and it does not group the members of over-split
packages more than those of deliberate ones. At stage 3 it splits the control
arm's 7 flat modules into 2 or 3 clusters and the LaNorme arm's 13 to 22 into
5 to 8. This is in line with the literature: recovery techniques score low
against ground-truth architectures [@garcia2013comparative;
@lutellier2015comparing], coupling and cohesion do not seem to be the
dominant drivers of practitioners' modularization [@abreu2001coupling],
whether developers pursue that balance at all is an open question
[@candela2016cohesion], and automated remodularisation that ignores domain
knowledge can give results that are nonsensical to developers
[@hall2018expert].

## Agent decisions

`prompts/decide.md` put agents in the place of each project's maintainer with
the 36 findings `SHALLOW-001` reports on the rated packages: merge as the fix
suggests, or keep the split and silence the finding. Each finding was decided
twice with the rule's message and fix as they are, and twice with the Common
Reuse sentence of `reuse_signals.describe_reuse` appended ("Only 'app/main.py'
imports them (4 of 5), and some import one another."). No agent saw both
arms. An agent decided a batch of 12 findings in one session, so each arm is
six sessions, not 72 independent decisions.

| Arm | Merges | Replicates agreeing | Merges on findings rated merge, contested, keep |
|---|---|---|---|
| message as it is | 8 of 72 | 32 of 36 | 6 of 18, 2 of 44, 0 of 10 |
| with the evidence sentence | 7 of 72 | 33 of 36 | 7 of 18, 0 of 44, 0 of 10 |

Five findings were decided differently between the arms, each by one
replicate; within an arm the two replicates differ on four and three. No
effect is detectable, and the experiment is too small to rule one out. The
five A/B findings (the packages the rule was built on) were kept in all 20
decisions. This is a maintainer's decision about existing code; it does not
measure whether the rule changes how an agent writes new code, which needs
the full A/B harness. Its raw runs live in a repository this spike could not
reach, and a re-run is 18 agent builds.

## Invented signals: boundary work

The signals above ask whether the members of a package resemble each other.
A second family asks what each module boundary does, by simulating the merge
on the names the members define and the names their clients import
(`harness/member_surfaces.py`, `boundary_signals.py`, `idle_boundaries.py`):

- `idle_share`: the share of members whose boundary does no work. A member
  is idle when no function body in it holds a statement (it hides nothing
  [@parnas1972criteria; @ousterhout2018philosophy, ch. 4]), no client imports
  it as a module object, and it defines no name another member defines. It
  has no threshold.
- `scope_co_use`: of the client functions that reference any member's
  names, the share referencing two or more.
- `merge_evidence`: the sum of the two.

They were designed with all 135 packages of the first round in view, then
committed with six hypotheses and the analysis script before the
confirmation set was rated (`SPEC.md`). That set is 122 packages from 43
repositories nothing earlier had read, rated by two blind raters who were
not shown line counts: 30 merge, 47 contested, 45 keep.

| Measure, merge against the rest | Designed on (88 packages) | Confirmation (122) | Fixed in advance |
|---|---|---|---|
| `merge_evidence` | 0.71 [0.55, 0.84] | 0.49 [0.36, 0.62], corrected 0.51 [0.38, 0.65] | primary, above 0.5: not confirmed |
| `idle_share`, merge against keep | 0.74 [0.57, 0.84] | 0.49 [0.35, 0.63] | above 0.5: not confirmed |
| `scope_co_use`, merge against keep | 0.79 [0.62, 0.92] | 0.47 [0.31, 0.60], corrected 0.51 [0.35, 0.64] | above 0.5: not confirmed |
| `cochange_similarity`, merge against keep | 0.80 [0.63, 1.00] | 0.51 [0.33, 0.67] | above 0.5: not confirmed |
| lines per member, merge against keep | 0.18 [0.08, 0.38] | 0.45 [0.31, 0.60] | below 0.5: not confirmed |
| `identifier_cohesion` | 0.57 [0.44, 0.71] | 0.42 [0.30, 0.57] | no separation: holds |
| `used_together` | 0.61 [0.47, 0.73] | 0.46 [0.31, 0.61] | no separation: holds |

The namespace hypothesis fails too: 1 of the 8 packages with a member used
as a namespace is rated merge, against 29 of the other 114, where the first
round had 0 of 10 against 17 of 78. Counting a package as merge only when
both raters say so leaves 6 and changes no verdict.

The first figure of a cell is the registered run. After it, the name-level
reading of imports was found to miss a name defined in a package's own
`__init__.py`, so it saw no client for 14 of the 105 confirmation packages
that have one (3 of 119 in the first round). With that closed (1 of 105)
the corrected figures move by at most 0.04 and no verdict changes. The
result files hold the corrected measurement.

The confirmation set is not the first set's population. Two thirds of its
packages have two members (43% in the first set), and they come mostly from
libraries and large applications where the first set leaned on small layered
examples. On it the first rater's reasons for merge are most often a helper
used only by the module beside it, or a package every client reaches
through its re-exports: not what `idle_share` measures.

**What a second reader can do.** Between the two raters, kappa is 0.27 for
merge-or-not, 0.38 on three levels and 0.51 for keep-or-not. One rater's
three-level rating predicts the other's merge at an AUC of 0.70 on the
confirmation set (0.74 on the first). No signal that reads the code
mechanically should be expected to beat a second reader of it.

**Across both rated sets**, exploratory (`harness/report_exploration.py`),
merge against the rest:

| Signal | First set | Confirmation | Pooled (210 packages, 47 merge) |
|---|---|---|---|
| `sibling_only_names` | 0.57 [0.47, 0.71] | 0.66 [0.56, 0.78] | 0.62 [0.55, 0.71] |
| `internal_density` | 0.53 [0.37, 0.69] | 0.61 [0.51, 0.72] | 0.58 [0.49, 0.67] |
| `idle_share` | 0.69 [0.55, 0.79] | 0.51 [0.40, 0.62] | 0.58 [0.49, 0.66] |
| `merge_evidence` | 0.71 [0.55, 0.84] | 0.51 [0.38, 0.65] | 0.58 [0.47, 0.69] |
| `scope_co_use` | 0.71 [0.59, 0.84] | 0.48 [0.35, 0.61] | 0.55 [0.44, 0.66] |
| `role_similarity` | 0.42 [0.28, 0.56] | 0.64 [0.52, 0.75] | 0.54 [0.45, 0.63] |
| `cochange_similarity` | 0.66 [0.47, 0.87] | 0.44 [0.29, 0.59] | 0.51 [0.39, 0.65] |
| `identifier_cohesion` | 0.57 [0.44, 0.71] | 0.42 [0.30, 0.57] | 0.47 [0.38, 0.58] |

`sibling_only_names`, the share of public names only sibling members import,
is the one signal above 0.5 in both sets with a pooled interval clear of it.
It was a secondary measure, not a hypothesis, and it was picked out after
seeing both sets.

**Merge preconditions.** Of the 63 packages `SHALLOW-001` reports across
all four sets, 2 have a name two members define and 5 have a member some
client imports as a module object (6 packages in all). For those the fix
the finding suggests is not a move of code: it needs renames, at the
definitions or at every reference [@opdyke1992refactoring].

## Co-location model

Rounds one and two failed against a criterion two readers agree on at a
kappa near 0.3. Round three uses one nobody disputes: what authors did. A
module is a set of top-level definitions (functions, classes, constants) its
author kept together, and a boundary between sibling modules is one the
author drew, so for two definitions of one package "same module or not" is
known by construction. A module split mechanically, one definition per file,
is a package whose every cross-module pair was a same-module pair: a model
that recognises same-module pairs recognises that over-split, at the unit (a
pair) the raters' reasons pointed at.

The features of a pair do not depend on the file layout
(`harness/definition_pairs.py`): whether one names the other, the overlap of
the package's definitions naming each, the overlap of the client scopes
naming each, vocabulary similarity, fan-out similarity, same kind, a shared
base class, shared name words, and whether either has a client. The model
(`harness/logistic.py`) is a ridge-penalised logistic regression fitted with
the standard library: nine weights and an intercept, stored in
`results/colocation_model.json`. The weights that carry it are a direct
reference (0.64), shared referrers (0.45) and vocabulary (0.27).

It was developed on 58,932 pairs from the 40 repositories of round one, each
repository scored by a model fitted on the other 39. It was then frozen, its
five hypotheses were committed (`SPEC.md`), and it was scored once on the 70
repositories of the confirmation set (69 have pairs; 95,727 pairs) and on
the 18 A/B snapshots.

| Hypothesis, fixed in advance | Development (40 repositories) | Test (69 repositories) | Verdict |
|---|---|---|---|
| Same-module against sibling-module pairs: median AUC at least 0.65, above 0.5 in four of five repositories | 0.74, 39 of 40 | 0.77 (quartiles 0.74 and 0.79), 69 of 69 | holds |
| The same on packages of at most 200 lines | 0.76 | 0.79, 52 of 52 | holds |
| Small modules split one definition per file against small packages as authored: AUC above 0.5 in three of four repositories | median 0.80, 35 of 37 | median 0.86, 47 of 52 | holds |
| The LaNorme arm's snapshots have a higher boundary score than the control arm's (AUC at least 0.75) | | 0.04 | fails, reversed |
| Boundary score higher in packages rated merge than in the rest (secondary) | 0.55 [0.39, 0.72] | 0.49 [0.33, 0.66] | fails |

The best single feature, vocabulary, has a median AUC of 0.70 on the test
repositories; the model adds to it. The lowest test repository is at 0.55.

**The agent arms.** The boundary score is the mean probability the model
gives a package's cross-module pairs of being kept in one module; the
cohesion score is the same over a module's own pairs.

| Group | Modules | Median module lines | Pairs within a module | Pairs across modules |
|---|---|---|---|---|
| Authors, 40 repositories | 10,161 | 77 | 0.56 | 0.40 |
| A/B control arm, 9 snapshots | 54 | 58 | 0.60 | 0.46 |
| A/B LaNorme arm, 9 snapshots | 112 | 40 | 0.58 | 0.42 |

The arm told to use LaNorme wrote twice the modules, and its boundaries
separate pairs that look like the pairs authors separate, not like the pairs
authors keep together. The control arm's fewer, larger modules are the more
entangled. So the agent packages are not fragments of cohesive code. If they
are over-split it is in granularity (a median module of 40 lines against the
authors' 77), which is what `SHALLOW-001` already measures, and not in
meaning, which is why two rounds of looking for evidence that their modules
belong together found none.

**What the model is and is not.** It answers "would authors keep these two
definitions in one module" well enough to tell a mechanical over-split from
an authored package. It does not answer "would a reviewer merge this
package": against both sets of ratings its score is at chance. Those are
different questions, and only the first has a reliable answer.

## Recommendation

1. **Do not add a semantic condition, evidence sentence, co-change reading
   or tighter size condition to `SHALLOW-001`.** Nothing of rounds one and
   two separates the packages raters would merge on data it was not designed
   on, and the co-location score does not either.
2. **Do not build a clustering tool.**
3. **Re-examine the rule's premise before investing more in it.** The A/B
   packages it was built on are not over-split by four independent readings:
   the A/B judge preferred that arm's design in all nine pairs, blind raters
   call most of them contested or keep, agents acting as maintainers kept
   all five in all 20 decisions, and their boundaries look like authors'.
   The rule is advisory and off by default, which fits. Its dev labels, which
   one blind rater reproduces for 3 of 13, should be rated blind by more
   than one rater before its dev numbers are trusted.
4. **Check the merge precondition in the fix, in its own change.** When two
   members define a name in use, or a client uses a member as a namespace,
   the suggested merge needs renames. It fails for 6 of 63 findings, and
   `docs/RULES.md` already lists distinct names as a limit the rule does not
   check.
5. **Consider the co-location model for a different check.** It is small
   enough to ship (ten numbers, no dependency) and it holds on unseen
   projects. What it can support is a finding about a boundary that cuts
   through code authors keep together: a definition that sits apart from the
   ones it is used with. That is a move suggestion, not a merge of a small
   package, and it needs its own measurement of precision at the threshold
   it would report at before it is a rule.
6. **If merge is to be rated again, define it by what a rater can point
   at** (a helper only its neighbour imports, a package reached only through
   its re-exports). `sibling_only_names`, the one signal of round two above
   chance in both rated sets, states such a fact.

## Reading it honestly

- Every rater and every deciding agent is a Claude model, as the corpus's
  labellers were. Agreement between them is agreement within one family of
  judges, not with maintainers of these projects.
- The samples are small and clustered: 257 packages from 77 projects over
  both rounds, and five of the holdout's seven structural over-split
  packages from one repository.
- The holdout's labels and notes were read while mapping the corpus onto the
  original trees, before any holdout signal was computed. The extension set,
  the blind ratings, the size measures and the decision experiment were
  added after the holdout result, so they are exploratory; `SPEC.md` lists
  them and the measurement corrections made after an adversarial review.
- The first round's raters saw line counts and a prompt framing each package
  as fitting one module of about 200 lines. The confirmation raters saw
  neither, and the size effect is gone there, so it was at least partly the
  raters' own input.
- The co-location model's ground truth is where authors put code, which is
  an observed decision and not a judgement of quality: an author's module
  can be badly organised. Its test is on open-source repositories, mostly
  human-written; the only agent-written code is three A/B runs.
- The reading that the A/B packages are not over-split came after the
  hypothesis about them failed, so it is an interpretation, on three runs.
- The invented signals were confirmed on one fresh set and failed. The
  comparison across both rated sets, the rater ceiling and the
  `sibling_only_names` lead were looked at afterwards and are exploratory.
- The confirmation set is a different population from the first set, so its
  failure says the signals do not generalise, not that they are noise on
  the kind of package they were designed on.
- Vocabulary uses a weighted vector space, not latent semantic indexing,
  which needs a decomposition the standard library lacks. Against Java
  package structure the indexed variant did worse than the plain one
  [@beck2011congruence].
- An import of a package's `__init__.py` is attributed to a member by the
  name it re-exports, a heuristic.
- Co-change follows exact renames only, and extension repositories are
  measured at the commits recorded in `results/extension_candidates.json`.
- Partitions are compared with the adjusted Rand index, not the MoJoFM
  measure the recovery literature uses. The Modularization Quality formula
  is the cluster-factor form as the Bunch literature gives it, written from
  memory and not checked against the paper's text.
- Of the cited works, only the Beck and Diehl paper was read in full; what
  is said of the others rests on their abstracts.

## Layout

- `SPEC.md`: the design and the holdout hypotheses.
- `harness/`: the signals (`reuse_signals.py`, `vocabulary_signals.py`,
  `sibling_signals.py`, `cochange_signals.py`), the statistics
  (`separation.py`), the search (`modularization_quality.py`) and one script
  per experiment.
- For round three: `definition_pairs.py`, `logistic.py`,
  `measure_colocation.py`, `report_colocation.py`, `score_packages.py` and
  `report_package_scores.py`, with `results/colocation_model.json`,
  `colocation_dev.json`, `colocation_test.json`, `package_scores_dev.json`
  and `package_scores_test.json`. The pair and score files themselves are
  large and are rebuilt by the commands below.
- `prompts/`: the blind labelling and decision prompts;
  `label_confirmation.md` is the labelling prompt without line counts.
- `results/`: `packages_*.json` (one record per package),
  `separation_*.json`, `ratings.json` and `ratings_report.json`,
  `congruence.json`, `clustering.json`, `decisions.json` and
  `decisions_report.json`; for the second round
  `confirmation_candidates.json`, `ratings_confirmation.json`,
  `confirmation_report.json` and `exploration_report.json`.

## Reproducing it

Clone each repository the corpus names (the `source` of its labels) at its
pinned commit into one directory, with history, and the extension
repositories at the commits in `results/extension_candidates.json` into
another, and the confirmation repositories at the commits in
`results/confirmation_candidates.json` into a third. Then, from the
repository root:

```console
H=experiments/semantic-merge/harness; R=experiments/semantic-merge/results
mkdir ab-trees
for case in "$PWD"/evals/corpora/shallow_modules/dev/cases/ab-*; do ln -s "$case" ab-trees/; done
uv run python $H/measure_packages.py --repositories corpus-trees --split dev --out $R/packages_dev.json
uv run python $H/report_separation.py $R/packages_dev.json --out $R/separation_dev.json
uv run python $H/measure_packages.py --repositories corpus-trees --split holdout --out $R/packages_holdout.json
uv run python $H/report_separation.py $R/packages_holdout.json --out $R/separation_holdout.json
uv run python $H/select_candidates.py --trees extension-trees --out $R/extension_candidates.json
uv run python $H/measure_packages.py --repositories extension-trees --candidates $R/extension_candidates.json --out $R/packages_extension.json
uv run python $H/report_ratings.py --out $R/ratings_report.json
uv run python $H/report_decisions.py --out $R/decisions_report.json
uv run python $H/measure_congruence.py --trees corpus-trees --out $R/congruence.json
uv run python $H/select_candidates.py --trees confirmation-trees --per-tree 6 --out $R/confirmation_candidates.json
uv run python $H/measure_packages.py --repositories confirmation-trees --candidates $R/confirmation_candidates.json --out $R/packages_confirmation.json
uv run python $H/report_confirmation.py --out $R/confirmation_report.json
uv run python $H/report_exploration.py --out $R/exploration_report.json
uv run python $H/measure_colocation.py --trees corpus-trees --trees extension-trees --out pairs_dev.json
uv run python $H/report_colocation.py pairs_dev.json --fit $R/colocation_model.json --out $R/colocation_dev.json
uv run python $H/measure_colocation.py --trees confirmation-trees --out pairs_test.json
uv run python $H/report_colocation.py pairs_test.json --model $R/colocation_model.json --out $R/colocation_test.json
uv run python $H/score_packages.py --trees corpus-trees --trees extension-trees --trees ab-trees --model $R/colocation_model.json --out scores_dev.json
uv run python $H/report_package_scores.py --scores scores_dev.json --ratings $R/ratings.json --out $R/package_scores_dev.json
uv run python $H/score_packages.py --trees confirmation-trees --model $R/colocation_model.json --out scores_test.json
uv run python $H/score_packages.py --trees confirmation-trees --model $R/colocation_model.json --only $R/packages_confirmation.json --out scores_test_rated.json
uv run python $H/report_package_scores.py --scores scores_test.json --scores scores_test_rated.json --ratings $R/ratings_confirmation.json --out $R/package_scores_test.json
uv run python $H/measure_clustering.py --trees ab-trees --trees corpus-trees --records $R/packages_dev.json --records $R/packages_holdout.json --out $R/clustering.json
```

The ratings and decisions are agent
output and are kept as recorded; a new labelling or decision run fills
`prompts/label.md` or `prompts/decide.md` with a batch path and an output
path.
