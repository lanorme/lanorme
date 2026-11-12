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
  changes what an agent does with it.
