# Semantic merge evidence: results

Can LaNorme give grounded evidence about whether the modules of a small
package belong together, so that a `SHALLOW-001` finding is better informed or
more precise, with the standard library only? This is the spike behind issue
69. `SPEC.md` has the design; this page has what came out.

## Headline

- **No evidence signal identifies the packages to merge.** Common Reuse,
  internal coupling, vocabulary, sibling similarity and co-change were measured
  on 135 packages from 34 projects. A pattern that looked strong on the dev
  split (over-split packages have the lower vocabulary cohesion, AUC 0.11)
  vanished on the holdout (0.49), and against independent blind ratings every
  signal's interval covers no separation.
- **The instruments work; the question is not the one they answer.** Across
  20 projects, vocabulary and fan-out similarity recover the authors' own
  packages well (median AUC 0.79 and 0.82). They detect modules that are the
  same kind of thing. A package of parallel siblings (one router per
  resource, one port per file) scores high on them whether raters call it an
  over-split or a convention.
- **The label itself is only moderately reliable.** Two blind raters agree on
  merge-or-not with a kappa of 0.32. An independent blind rater reproduces
  the holdout's labels (kappa 0.76) but not the dev split's (0.24): of the 13
  dev packages labelled over-split, it rates 3 as merge.
- **What does separate is size, which the rule already reads.** Packages rated
  merge have 20 lines per member at the median, those rated keep 59 (AUC
  0.18, interval 0.08 to 0.38).
- **Evidence in the message does not change decisions.** Agents acting as
  maintainers merged 8 of 72 times with the message as it is and 7 of 72 with
  a Common Reuse sentence added.
- **Clustering the import graph is not worth a tool.** A Bunch-style search
  is unstable between random starts (median adjusted Rand index 0.44) and
  almost uncorrelated with the authors' packages (0.05).

The recommendation is at the end: leave `SHALLOW-001`'s conditions and message
as they are, build no clustering tool, and take up two findings about the
rule's own corpus in separate changes.

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
is higher in over-split packages. Intervals are 95% cluster bootstraps over
projects.

## Directions

Each direction the issue lists, with its measured result or the reason it was
dropped.

| Direction | Result |
|---|---|
| Package design principles: Common Reuse, Common Closure [@martin2002agile, ch. 20] | Measured as the reuse and co-change signals below. Neither replicates. |
| Design criteria: shared information, used together [@ousterhout2018philosophy, ch. 9] | Measured as internal coupling and reuse. Neither replicates. |
| Semantic clustering [@kuhn2007semantic; @marcus2005conceptual] | Measured as vocabulary cohesion. Reversed on dev, absent on the holdout and against blind ratings. |
| Co-change [@gall1998logical; @zimmermann2005mining] | Measured. Defined for 60 of 135 packages; the largest effect seen, on 15 packages. |
| Search-based clustering [@mancoridis1998clustering; @mitchell2006bunch] | Measured. Unstable and not congruent with authors' packages. |
| Multi-objective clustering [@praditwong2011clustering] | Dropped. It optimises the same cohesion and coupling objectives the single-objective search already fails on here, and import coupling barely tracks Python packages (AUC 0.52). |
| Pattern-based clustering, ACDC [@tzerpos2000acdc] | Dropped. Its subsystem patterns are defined over a system's dependency graph; a candidate has two to ten modules, and the import graph they would be found in is the one the congruence check shows to carry little. |
| Structural plus semantic [@bavota2013modularization] | Dropped. A weighted combination adds a free parameter to fit on a few dozen packages, over two components that each fail to replicate. |
| Comparative evaluation [@garcia2013comparative; @lutellier2015comparing] | Used as the frame for the clustering result: both report low accuracy for every recovery technique against ground-truth architectures. |

## Signals against the corpus labels

Over-split against the rest, `structural` population. The holdout hypotheses
were committed before any holdout value was computed (`SPEC.md`).

| Signal | Dev | Holdout | Hypothesis |
|---|---|---|---|
| `identifier_cohesion` | 0.11 [0.00, 0.31] | 0.49 [0.40, 0.83] | lower in over-split: not confirmed |
| `internal_density` | 0.18 [0.00, 0.52] | 0.48 [0.27, 1.00] | lower in over-split: not confirmed |
| `importer_similarity` | 0.46 [0.00, 0.72] | 0.70 [0.65, 0.80] | no separation: not confirmed |
| `used_together` | 0.51 [0.23, 0.73] | 0.75 [0.68, 0.90] | no separation: not confirmed |
| `mean_share_used` | 0.29 [0.08, 0.52] | 0.85 [0.79, 1.00] | none stated |
| `fan_out_similarity` | 0.47 [0.12, 0.73] | 0.51 [0.45, 0.67] | none stated |
| `role_similarity` | 0.37 [0.07, 0.62] | 0.41 [0.08, 0.55] | none stated |

The two splits disagree in direction. On dev the over-split packages are
grab-bags (a domain layer of unrelated tiny modules) and the rest are cohesive
conventions, so cohesion marks what to keep. On the holdout six of the nine
over-split packages come from one repository and several are parallel
one-Protocol-per-file ports, which are cohesive and used together. The
Common Reuse signals separate there, in the direction the issue expected, but
on four projects and with dev showing the opposite.

The parallel-sibling guard fixed on dev (treat `identifier_cohesion` of at
least 0.176 as a deliberate convention) removed all 5 of the rule's false
positives and 3 of 13 true positives on dev. On the holdout it removes 2 of 3
false positives and 3 of 4 true positives. It is not a usable condition.

Co-change is defined for 22 of 48 dev candidates, 15 of 33 on the holdout
and 23 of 54 in the extension: a small package is young or rarely changed. It
is absent for every A/B snapshot, the code the rule was built for.

## Blind ratings

`prompts/label.md` asked independent agents for one of three levels per
package, without the rule, a signal or a corpus label: `merge`, `contested`
(a recognisable convention reasonable maintainers disagree about) or `keep`.
Corpus candidates were rated once, each A/B package at its last stage;
extension candidates twice, by two different models.

**Reliability**, as Cohen's kappa [@cohen1960coefficient]:

| Comparison | Packages | Agreement | Kappa |
|---|---|---|---|
| dev label against blind rating, merge or not | 40 | 0.72 | 0.24 |
| holdout label against blind rating, merge or not | 33 | 0.91 | 0.76 |
| two blind raters, three levels | 54 | 0.74 | 0.56 |
| two blind raters, merge or not | 54 | 0.87 | 0.32 |

Of the 13 dev packages the corpus labels over-split, the blind rater calls 3
merge, 7 contested and 3 keep; of the holdout's 9, it calls 7 merge. The dev
split's positive labels came from the rule's design document, unblinded, and
do not survive a blind reading; the holdout's do.

**Signals against the ratings**, on the 88 rated packages the structural
conditions pass (17 merge, 47 contested, 24 keep; a package is merge if any
rater says so, keep if all do):

| Measure | Merge against keep | Merge against contested |
|---|---|---|
| lines per member | 0.18 [0.08, 0.38] | 0.41 [0.26, 0.61] |
| lines | 0.16 [0.07, 0.32] | 0.37 [0.24, 0.54] |
| `identifier_cohesion` | 0.59 [0.37, 0.77] | 0.55 [0.39, 0.73] |
| `internal_density` | 0.48 [0.27, 0.71] | 0.55 [0.40, 0.69] |
| `importer_similarity` | 0.49 [0.32, 0.72] | 0.53 [0.40, 0.65] |
| `used_together` | 0.56 [0.39, 0.77] | 0.61 [0.48, 0.74] |
| `fan_out_similarity` | 0.54 [0.31, 0.71] | 0.54 [0.37, 0.74] |
| `role_similarity` | 0.45 [0.29, 0.59] | 0.40 [0.25, 0.57] |
| `cochange_similarity` | 0.79 [0.52, 1.00], 15 packages | 0.57 [0.32, 0.84], 26 packages |

Only the two size measures have an interval clear of 0.5, and only against
keep. Co-change has the largest effect of any evidence signal, on the 15
packages that have it.

**The rule against the ratings.** Of the 36 rated packages `SHALLOW-001`
reports on the original trees, 9 are rated merge, 22 contested and 5 keep.

## Congruence with the authors' packages

For each of the 20 corpus source projects, how well a pairwise signal tells
two modules of one package from two modules of sibling packages, as Beck and
Diehl did for 16 Java projects [@beck2011congruence]:

| Signal | Median AUC | Lowest | Highest |
|---|---|---|---|
| fan-out similarity | 0.82 | 0.11 | 0.97 |
| vocabulary cosine | 0.79 | 0.10 | 0.96 |
| shared importers | 0.70 | 0.53 | 0.94 |
| co-change | 0.54 | 0.48 | 0.70 |
| direct import | 0.52 | 0.43 | 0.70 |

Python packages group modules that import the same things and use the same
words, not modules that import one another. The lowest values are one
feature-sliced project (clean-architecture), where a package holds one
feature's different roles and its sibling holds the same roles for another
feature. That is the distinction the merge question turns on, and similarity
reads it backwards: similar modules are parallel siblings, which a
maintainer may well keep apart, while the parts of one idea need not resemble
each other. The Java study found structural dependencies and co-change among
the most congruent concepts and vocabulary second-rate; on Python imports the
order is the reverse.

## Clustering

`harness/modularization_quality.py` is a Bunch-style hill climb maximising
Modularization Quality [@mitchell2006bunch], 20 random starts per tree, on
the 18 A/B snapshots and the 18 corpus source projects of at most 1,500
modules.

| Measure | Value |
|---|---|
| Stability: median adjusted Rand index [@hubert1985comparing] between the local optima of different starts | 0.44 |
| Agreement of the best partition with the authors' directories, median | 0.05 |
| Trees where the search scores above the authors' own partition | 33 of 36 |
| Member pairs of a labelled over-split package placed in one cluster, mean | 0.20 (25 packages) |
| The same for the other labelled candidates | 0.25 (51 packages) |

The search finds partitions that score higher than the authors' on its own
objective, different ones on each start, none resembling the packages, and it
does not group the members of over-split packages more than those of
deliberate ones. At stage 3 it splits the control arm's 7 flat modules into 2
or 3 clusters and the LaNorme arm's 13 to 22 into 5 to 8. This matches the
literature: recovery techniques score low against ground-truth architectures
[@garcia2013comparative; @lutellier2015comparing], coupling and cohesion are
not what developers appear to optimise [@abreu2001coupling;
@candela2016cohesion], and automated remodularisation that ignores domain
knowledge produces results developers reject [@hall2018expert].

## Agent decisions

`prompts/decide.md` put agents in the place of each project's maintainer with
the 36 findings `SHALLOW-001` reports on the rated packages: merge as the fix
suggests, or keep the split and silence the finding. Each finding was decided
twice with the rule's message and fix as they are, and twice with the Common
Reuse sentence of `reuse_signals.describe_reuse` appended ("Only 'app/main.py'
imports them (3 of 3), and some import one another."). No agent saw both arms.

| Arm | Merges | Replicates agreeing | Merges on findings rated merge, contested, keep |
|---|---|---|---|
| message as it is | 8 of 72 | 32 of 36 | 6 of 18, 2 of 44, 0 of 10 |
| with the evidence sentence | 7 of 72 | 33 of 36 | 7 of 18, 0 of 44, 0 of 10 |

Five findings were decided differently between the arms, no more than
differ between two replicates of one arm. The five A/B findings (the
packages the rule was built on) were kept in all 20 decisions. This is a
maintainer's decision about existing code; it does not measure whether the
rule changes how an agent writes new code, which needs the full A/B harness.
Its raw runs live in a repository this spike could not reach, and a re-run is
18 agent builds.

## Recommendation

1. **Do not add a semantic condition or filter to `SHALLOW-001`.** No signal
   keeps its direction across the dev split, the holdout and the blind
   ratings, and the one guard fixed in advance fails on the holdout.
2. **Do not add evidence to the message.** The Common Reuse sentence is true
   of the package but does not predict the label and does not change
   decisions; it would lengthen every finding for nothing measured. The
   message already carries what does separate: each member's size.
3. **Do not read co-change.** It is absent where the rule is aimed (new,
   agent-written code) and defined for under half of everything else.
4. **Do not build a clustering tool.** The search is unstable and unrelated to
   the packages authors make.
5. **Re-label the dev split blind, in its own change.** Its over-split labels
   do not reproduce (kappa 0.24 against 0.76 for the holdout), and the rule
   was tuned to them. The corpus discipline allows it for dev; the holdout
   stays sealed.
6. **Consider a tighter size condition, measured on its own.** Lines per
   member is the one measure that separates merge from keep. Whether a lower
   tiny threshold or merge budget buys precision worth its recall is a
   change to the rule, to be chosen on the re-labelled dev split and
   confirmed on the holdout.

## Reading it honestly

- Every rater and every deciding agent is a Claude model, as the corpus's
  labellers were. Agreement between them is agreement within one family of
  judges, not with maintainers of these projects.
- The samples are small and clustered: 135 packages, 34 projects, and six of
  the holdout's nine over-split packages from one repository. Intervals
  resample projects, and several span most of the scale.
- The holdout's labels and notes were read while mapping the corpus onto the
  original trees, before any holdout signal was computed. The extension set,
  the blind ratings and the size measures were added after the holdout
  result, so they are exploratory.
- Vocabulary uses a weighted vector space, not latent semantic indexing,
  which needs a decomposition the standard library lacks. Against Java
  package structure the indexed variant did worse than the plain one
  [@beck2011congruence].
- An import of a package's `__init__.py` is attributed to a member by the
  name it re-exports, a heuristic; imports of the package that name nothing
  re-exported are attributed to no member.
- Co-change follows no renames, and extension repositories are measured at
  the commits recorded in `results/extension_candidates.json`.
- Partitions are compared with the adjusted Rand index, not the MoJoFM
  measure the recovery literature uses. The Modularization Quality formula
  is the cluster-factor form as the Bunch literature gives it, written from
  memory and not checked against the paper's text.

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
uv run python $H/measure_packages.py --repositories corpus-trees --split dev --out $R/packages_dev.json
uv run python $H/report_separation.py $R/packages_dev.json --out $R/separation_dev.json
uv run python $H/measure_packages.py --repositories corpus-trees --split holdout --out $R/packages_holdout.json
uv run python $H/report_separation.py $R/packages_holdout.json --out $R/separation_holdout.json
uv run python $H/select_candidates.py --trees extension-trees --out $R/extension_candidates.json
uv run python $H/measure_packages.py --repositories extension-trees --candidates $R/extension_candidates.json --out $R/packages_extension.json
uv run python $H/report_ratings.py --out $R/ratings_report.json
uv run python $H/report_decisions.py --out $R/decisions_report.json
uv run python $H/measure_congruence.py --trees corpus-trees --out $R/congruence.json
uv run python $H/measure_clustering.py --trees corpus-trees --records $R/packages_dev.json --records $R/packages_holdout.json --out $R/clustering.json
```

The ratings and decisions are agent output and are kept as recorded; a new
labelling or decision run fills `prompts/label.md` or `prompts/decide.md`
with a batch path and an output path.
