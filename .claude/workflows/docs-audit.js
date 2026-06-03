export const meta = {
  name: 'docs-audit',
  description: 'Audit the project docs for accuracy, currency, no-archeology, clarity, and coverage',
  whenToUse:
    'Before a release or after changing docs or the CLI, to confirm every documented flag, rule code, config key, and output example still matches the real tool, that prose is direct OSS prose (no changelog narration outside CHANGELOG, no bluff), and that nothing user-facing is undocumented. LaNorme PROSE-001/002/003 already gate em-dashes, British spelling, and emoji, so this fills the accuracy and clarity gap those style checks cannot see.',
  phases: [
    { title: 'Discover', detail: 'enumerate docs and snapshot the real CLI surface' },
    { title: 'Audit', detail: 'per doc: accuracy + style lenses, then adversarial verify' },
    { title: 'Coverage', detail: 'find user-facing surface that is undocumented' },
  ],
}

// Reusable across repos: pass { repo, cli } via args; sensible LaNorme defaults.
const REPO = (args && args.repo) || '.'
const CLI = (args && args.cli) || 'PYTHONPATH=src python3 -m lanorme.cli'
const base = (p) => p.split('/').pop()

const ENV = `
You are auditing the documentation of a software project (a stdlib-only Python
linter called LaNorme) checked out at: ${REPO}

CRITICAL: your shell and tools start in a DIFFERENT working directory, NOT ${REPO}.
Every file you read must use its ABSOLUTE path under ${REPO} (for example
${REPO}/README.md), never a bare name like README.md. Every shell command must be
prefixed with 'cd ${REPO} && '. If you find yourself reading a README that talks
about anything other than the LaNorme linter, you are in the wrong directory: stop
and re-read under ${REPO}.

The tool's own CLI is invoked as:
    ${CLI}
so its real surface is ground truth you can run, for example:
    cd ${REPO} && ${CLI} --help
    cd ${REPO} && ${CLI} check --help
    cd ${REPO} && ${CLI} rules
    cd ${REPO} && ${CLI} check . --show-config
Source lives under ${REPO}/src/. Read it; do not guess.
`

// --------------------------------------------------------------------------- //
// Phase 1: Discover
// --------------------------------------------------------------------------- //

phase('Discover')

const DISCOVER_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    docs: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          path: { type: 'string' },
          allow_history: { type: 'boolean' },
        },
        required: ['path', 'allow_history'],
      },
    },
    excluded: { type: 'array', items: { type: 'string' } },
    cli_help: { type: 'string' },
    rule_codes: { type: 'array', items: { type: 'string' } },
    config_keys: { type: 'array', items: { type: 'string' } },
  },
  required: ['docs'],
}

const discovery = await agent(
  `${ENV}

Enumerate the USER-FACING Markdown docs to audit. Include README.md, CONTRIBUTING.md,
CLAUDE.md, docs/RULES.md, and any other top-level or docs/ Markdown that documents
usage, configuration, or rules.

EXCLUDE generated or internal working notes (they are deliberately excluded from the
project's own linting): anything matching docs/*-design.md, docs/*-corpus*.md,
docs/audit/*, and any file listed under "exclude" in the [tool.lanorme] table of
pyproject.toml. Report those under "excluded".

For each included doc set allow_history=false, EXCEPT CHANGELOG.md (if present), which
is the ONE place historical "previously / now / renamed" narration is allowed; set
allow_history=true for it.

Then snapshot the real CLI surface as ground truth for the accuracy lens:
  - cli_help: the combined output of \`${CLI} --help\` and \`${CLI} check --help\`.
  - rule_codes: every rule code from \`${CLI} rules\`.
  - config_keys: every \`[tool.lanorme]\` / \`[tool.lanorme.<check>]\` key you can find
    in the source and pyproject.toml.`,
  { schema: DISCOVER_SCHEMA, label: 'discover' },
)

// Force every doc path to an absolute path under REPO so the per-doc agents read
// the right files no matter what working directory they start in.
const toAbs = (p) => (p.startsWith('/') ? p : `${REPO.replace(/\/$/, '')}/${p}`)
const docs = ((args && Array.isArray(args.docs) && args.docs.length)
  ? args.docs.map((p) => ({ path: p, allow_history: base(p).toUpperCase().startsWith('CHANGELOG') }))
  : discovery.docs || []
).map((d) => ({ path: toAbs(d.path), allow_history: d.allow_history }))

const GROUND_TRUTH = `
Ground-truth snapshot of the real tool (verify doc claims against this AND by
re-running commands yourself):
--- CLI help ---
${(discovery.cli_help || '(run --help yourself)').slice(0, 6000)}
--- rule codes ---
${(discovery.rule_codes || []).join(', ') || '(run "rules" yourself)'}
--- config keys ---
${(discovery.config_keys || []).join(', ') || '(grep the source yourself)'}
`

log(`Auditing ${docs.length} docs: ${docs.map((d) => base(d.path)).join(', ')}`)

// --------------------------------------------------------------------------- //
// Phase 2: Audit (per doc: accuracy + style, then verify)
// --------------------------------------------------------------------------- //

const FINDINGS_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    doc: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          severity: { type: 'string', enum: ['blocker', 'major', 'minor', 'nit'] },
          category: {
            type: 'string',
            enum: ['accuracy', 'archeology', 'bluff', 'clarity', 'redundancy', 'style'],
          },
          line: { type: 'integer' },
          quote: { type: 'string' },
          problem: { type: 'string' },
          fix: { type: 'string' },
          evidence: { type: 'string' },
        },
        required: ['severity', 'category', 'quote', 'problem', 'fix'],
      },
    },
  },
  required: ['doc', 'findings'],
}

const VERIFY_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    doc: { type: 'string' },
    confirmed: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          severity: { type: 'string', enum: ['blocker', 'major', 'minor', 'nit'] },
          category: { type: 'string' },
          line: { type: 'integer' },
          quote: { type: 'string' },
          problem: { type: 'string' },
          fix: { type: 'string' },
        },
        required: ['severity', 'category', 'problem', 'fix'],
      },
    },
    dropped: { type: 'integer' },
    verdict: { type: 'string', enum: ['solid', 'needs_work'] },
  },
  required: ['doc', 'confirmed', 'verdict'],
}

const accuracyPrompt = (doc) => `${ENV}\n${GROUND_TRUTH}

Lens: ACCURACY and CURRENCY of ${doc.path}. Read it in full. For EVERY concrete,
checkable claim - a command, flag, subcommand, rule code, config key, default value,
output example, file path, install line, or version - verify it against the real tool
by RUNNING it (or grepping src/). Flag anything wrong, stale, or not reproducible:
a renamed or removed flag, a rule code that no longer exists, an output block that
does not match real output, a default that drifted, a dead link or path. Quote the
exact doc line and state what the tool actually does. Accuracy issues are blocker or
major. Return only real, evidenced findings (set evidence to the command you ran).`

const stylePrompt = (doc) => `${ENV}

Lens: PROSE QUALITY of ${doc.path}. Read it in full. This project wants direct,
idiomatic open-source documentation: clear, grounded, to the point, no bluff.
Flag:
- ${doc.allow_history
    ? 'NOTHING on history grounds: this file is the changelog, where "previously / now / renamed" narration is expected.'
    : 'ARCHEOLOGY: any "previously / now / used to / renamed / no longer / we changed" narration. History belongs only in CHANGELOG; the doc should describe the current state directly.'}
- BLUFF: marketing language, hype, vague superlatives ("blazingly fast", "simply the best"), claims without grounding.
- REDUNDANCY: waffle, repetition, sentences that add nothing.
- CLARITY: confusing, ambiguous, or non-idiomatic phrasing; burying the point.
Do not re-flag em-dashes, British vs American spelling, or emoji: the project's own
PROSE-001/002/003 checks already gate those (only note them if you happen to see one).
Quote the exact line. Most prose issues are minor or nit; only genuinely misleading
prose is major.`

phase('Audit')

const audited = await pipeline(
  docs,
  (doc) =>
    parallel([
      () => agent(accuracyPrompt(doc), { label: `accuracy:${base(doc.path)}`, phase: 'Audit', schema: FINDINGS_SCHEMA }),
      () => agent(stylePrompt(doc), { label: `style:${base(doc.path)}`, phase: 'Audit', schema: FINDINGS_SCHEMA }),
    ]).then((rs) => ({
      doc,
      findings: rs.filter(Boolean).flatMap((r) => r.findings || []),
    })),
  (res, doc) =>
    res.findings.length === 0
      ? { doc: doc.path, confirmed: [], dropped: 0, verdict: 'solid' }
      : agent(
          `${ENV}\n${GROUND_TRUTH}

Adversarially VERIFY these candidate issues for ${doc.path}. For each one, independently
re-check it: re-run the cited command, or re-read the cited line in the file. KEEP only
issues that are genuinely real and would matter to a reader; DROP false positives,
nitpicks that are actually fine, and anything you cannot reproduce. Re-grade severity:
blocker = wrong or misleading information a user would act on; major = a real inaccuracy
or an important missing explanation; minor = clarity or redundancy; nit = wording.
Set verdict "needs_work" only if a confirmed blocker or major remains, else "solid".

Candidate issues (JSON):
${JSON.stringify(res.findings).slice(0, 12000)}`,
          { label: `verify:${base(doc.path)}`, phase: 'Audit', schema: VERIFY_SCHEMA },
        ),
)

// --------------------------------------------------------------------------- //
// Phase 3: Coverage (undocumented surface)
// --------------------------------------------------------------------------- //

phase('Coverage')

const COVERAGE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    gaps: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          kind: {
            type: 'string',
            enum: ['flag', 'rule-code', 'output-format', 'config-key', 'subcommand', 'other'],
          },
          item: { type: 'string' },
          problem: { type: 'string' },
          suggested_doc: { type: 'string' },
        },
        required: ['kind', 'item', 'problem'],
      },
    },
  },
  required: ['gaps'],
}

const coverage = await agent(
  `${ENV}\n${GROUND_TRUTH}

Lens: COVERAGE. Compare the tool's full user-facing surface against what the docs
(${docs.map((d) => d.path).join(', ')}) actually document. Enumerate the real surface
(every CLI flag and subcommand from --help, every rule code from \`rules\`, every output
format, every [tool.lanorme] config key) and list what a user CANNOT learn from the docs:
genuinely undocumented or under-documented items. Skip things that are intentionally
internal. For each gap name the item, why it matters, and which doc should cover it.`,
  { schema: COVERAGE_SCHEMA, label: 'coverage' },
)

const results = audited.filter(Boolean)
const totalConfirmed = results.reduce((n, r) => n + (r.confirmed ? r.confirmed.length : 0), 0)
log(`Audit complete: ${totalConfirmed} confirmed doc issues, ${(coverage.gaps || []).length} coverage gaps.`)

return {
  audited: results,
  coverage_gaps: coverage.gaps || [],
  excluded: discovery.excluded || [],
  summary: {
    docs_audited: docs.map((d) => d.path),
    confirmed_issues: totalConfirmed,
    coverage_gaps: (coverage.gaps || []).length,
    needs_work: results.filter((r) => r.verdict === 'needs_work').map((r) => r.doc),
  },
}
