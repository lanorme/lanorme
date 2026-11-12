# Semantic merge evidence: results

Can LaNorme give grounded evidence about whether the modules of a small
package belong together, so that a `SHALLOW-001` finding is better informed or
more precise, with the standard library only? This is the spike behind issue
69. `SPEC.md` has the design; this page has what came out.

## Headline

- **No evidence signal reliably identifies the packages to merge.** Common
  Reuse, internal coupling, vocabulary, sibling similarity and co-change were
  measured on 135 packages from 34 projects. A pattern that looked strong on
  the dev split (over-split packages have the lower vocabulary cohesion, AUC
  0.11) is absent on the holdout (0.49), and the guard fixed on dev fails
  there.
- **Co-change is the one lead.** Against independent blind ratings it is the
  only evidence signal whose interval clears no separation: packages rated
  merge co-change more than packages rated keep (AUC 0.80, interval 0.63 to
  1.00). That rests on 17 packages, and the corpus labels do not confirm it
  on the seven and six packages where they can.
- **The signals do track how authors group modules, which is another
  question.** Across 20 projects every pairwise signal tells modules of one
  package from modules of sibling packages better than chance (median AUC
  0.59 to 0.72). A package of parallel siblings (one router per resource, one
  port per file) scores high on similarity whether raters call it an
  over-split or a convention.
- **The label itself is only moderately reliable.** Two blind raters agree on
  merge-or-not with a kappa of 0.32. One blind rater reproduces the
  holdout's over-split labels (7 of 9) but few of the dev split's (3 of 13).
- **Size separates what to keep from the rest, and the rule already reads
  it.** Packages rated keep have 59 lines per member at the median, those
  rated merge 20 (AUC 0.18, interval 0.08 to 0.38). The raters were shown the
  line counts, so this is not independent of them.
- **Evidence in the message shows no effect on decisions.** Agents acting as
  maintainers merged 8 of 72 times with the message as it is and 7 of 72
  with a Common Reuse sentence added, in six sessions per arm.
- **Clustering the import graph is not worth a tool.** A Bunch-style search
  agrees with the authors' packages at an adjusted Rand index of 0.15 and
  with itself, between random starts, at 0.57.

The recommendation is at the end: leave `SHALLOW-001`'s conditions and message
as they are, build no clustering tool, and take up three follow-ups in
separate changes.

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
| Package design principles: Common Reuse, Common Closure [@martin2002agile, ch. 20] | Measured as the reuse and co-change signals below. Reuse does not replicate; co-change is the one lead. |
| Design criteria: shared information, used together [@ousterhout2018philosophy, ch. 9] | Measured as internal coupling and reuse. Neither replicates. |
| Semantic clustering [@kuhn2007semantic; @marcus2005conceptual] | Measured as vocabulary cohesion. Reversed on dev, absent on the holdout and against blind ratings. |
| Co-change [@gall1998logical; @zimmermann2005mining] | Measured. Defined for 65 of the 120 packages in a git checkout; separates merge from keep on 17 rated packages. |
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

Size separates keep from both other levels and does not separate merge from
contested. Co-change is the only evidence signal with an interval clear of
0.5, merge against keep, on the 17 packages that have it. `used_together` and
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

## Recommendation

1. **Do not add a semantic condition or filter to `SHALLOW-001`.** No signal
   separates the packages to merge on both the dev split and the holdout, and
   the one guard fixed in advance fails on the holdout.
2. **Do not add evidence to the message.** The Common Reuse sentence is true
   of the package but does not predict the label, and it shows no effect on
   decisions; it would lengthen every finding for nothing measured.
3. **Do not build a clustering tool.** The search is weakly related to the
   packages authors make and does not single out over-split ones.
4. **Follow up co-change on its own, on repositories with history.** It is
   the only evidence signal that separates merge from keep, on 17 packages.
   That is enough to justify a dedicated measurement on a larger rated
   sample and not enough to act on. It can never speak about code with no
   history, which is where the rule is aimed, so at most it would be an
   optional line in the message.
5. **Have the dev split's over-split labels rated blind by more than one
   rater, in its own change.** One blind rater reproduces 3 of 13; if others
   agree, re-label dev (the corpus discipline allows it there; the holdout
   stays sealed), since the rule was tuned to those labels.
6. **Consider a tighter size condition, measured on its own.** Lines per
   member separates keep from the rest. A lower tiny threshold or merge
   budget would mostly remove findings rated keep (5 of the 36 reported), not
   the contested ones (22). Whether that is worth its recall is a change to
   the rule, to be chosen on dev and confirmed on the holdout, with raters
   who are not shown line counts.

## Reading it honestly

- Every rater and every deciding agent is a Claude model, as the corpus's
  labellers were. Agreement between them is agreement within one family of
  judges, not with maintainers of these projects.
- The samples are small and clustered: 135 packages, 34 projects, and five of
  the holdout's seven structural over-split packages from one repository.
- The holdout's labels and notes were read while mapping the corpus onto the
  original trees, before any holdout signal was computed. The extension set,
  the blind ratings, the size measures and the decision experiment were
  added after the holdout result, so they are exploratory; `SPEC.md` lists
  them and the measurement corrections made after an adversarial review.
- The raters saw line counts and the prompt framed each package as fitting
  one module of about 200 lines, so the size result is partly the raters'
  own input.
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
- `prompts/`: the blind labelling and decision prompts.
- `results/`: `packages_*.json` (one record per package),
  `separation_*.json`, `ratings.json` and `ratings_report.json`,
  `congruence.json`, `clustering.json`, `decisions.json` and
  `decisions_report.json`.

## Reproducing it

Clone each repository the corpus names (the `source` of its labels) at its
pinned commit into one directory, with history, and the extension
repositories at the commits in `results/extension_candidates.json` into
another. Then, from the repository root:

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
uv run python $H/measure_clustering.py --trees ab-trees --trees corpus-trees --records $R/packages_dev.json --records $R/packages_holdout.json --out $R/clustering.json
```

The ratings and decisions are agent
output and are kept as recorded; a new labelling or decision run fills
`prompts/label.md` or `prompts/decide.md` with a batch path and an output
path.
