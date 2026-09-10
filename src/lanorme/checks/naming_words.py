"""Vocabulary shared by the naming checks: which words read as verbs, which
class heads read as things, and the tokenizer that splits an identifier.

The lists are data, not theory. They were assembled from the words that open
function and class names in LaNorme's own source and in the third-party code
under ``benchmarks/.corpora`` (Flask, requests, rich, SQLAlchemy), then
measured; the numbers live in ``docs/RULES.md`` under NAMING-006..011.

Two lists pull in opposite directions and are kept apart on purpose:

- ``VERB_CAPABLE`` is recall-first. A word wrongly on it hides a finding and
  never creates one, so it can afford ``count``, ``match`` and ``flag``, which
  are nouns as often as verbs.
- ``VERB_ONLY`` is precision-first. It opens a class-name finding, so a word
  that doubles as a noun modifier (``Build``, ``Check``, ``Run``, ``Update``,
  ``Process``, ``Load``, ``Test``, ``Compute``, ``Render``) stays off it.
"""

from __future__ import annotations

import re

# Words that can be read as an imperative verb at the head of a function name.
VERB_CAPABLE: frozenset[str] = frozenset("""
abort accept access accumulate acquire activate adapt add adjust advance aggregate
alert align allocate allow alter analyse analyze anchor announce annotate append
apply approve archive are arrange assemble assert assign associate attach audit
auth authenticate authorise authorize average await awaken back backup balance
batch begin bind blank blend block boot bootstrap bound break broadcast broaden
browse buffer build bump bundle bypass cache calc calculate call can cancel capture
cast catch center centre chain change check choose chop chunk claim clamp classify
clean cleanup clear clip clone close cluster coerce collapse collect combine commit
compact compare compile complete compose compress compute concat concatenate
configure confirm connect consolidate construct consume contain continue convert
copy correct count cover crawl create crop cut darken deactivate deal dealloc debug
decide declare decode decompress decorate decr decrement decrypt dedent dedup dedupe
deduplicate deepen default defer define delegate delete deliver denormalise
denormalize deny depend deploy deprecate dequeue derive describe deserialise
deserialize destroy detach detect determine diff differ disable discard disconnect
discover dispatch display dispose dissolve distribute divide do document does
download downgrade drain draw drop dump duplicate echo edit elect emit enable encode
encrypt end enforce enqueue ensure enter enumerate equal erase escape establish
estimate evaluate evict examine exclude exec execute exist exit expand expect expire
explain explode export expose extend extract fail fake fall fasten fdel fetch fget
fill filter finalise finalize find finish fire fit fix flag flash flatten flip flush
fmt focus fold follow force forget fork format forward free freeze freshen fset
gather gen generate get give go grab grant group grow guard guess halt handle harden
has hash have head heal help hide highlight hit hold hook hydrate identify ignore
implement import include incr increment index infer inflate ingest inherit init
initialise initialize inject input insert inspect install instantiate instrument
interpolate interpret intersect introduce invalidate invert invoke is issue iter
iterate join jump keep kill label launch lazy leave lengthen lex lift lighten limit
link list listen load locate lock log login logout look lookup loop loosen lose lower
lstrip make manage mangle map mark marshal mask match materialise materialize may
measure merge migrate mirror mix mock modify monitor mount move multiply must mutate
name navigate need negate nest noop normalise normalize note notify observe obtain
offer open optimise optimize options order output overlap override pack pad page
paginate paint pair parse partition pass patch pause peek perform persist pick ping
pin pipe place plan plot pluck plug point poll pop populate position post pprint
predict prefer prep prepare prepend preprocess present preserve pretty print probe
process produce profile project promote prompt propagate protect provide provision
prune publish pull purge push put qualify query queue quicken quit quote raise
randomise randomize rank rate reach read rebase rebuild receive recompute reconcile
record recover recurse recv redirect redo reduce refactor refer refine reflect
refresh register reject relate relay release reload relocate remap remember remove
rename render repair repeat replace replay replicate report represent request
require rerun reset reshape resize resolve respond restart restore restrict resume
retain retrieve retry return reveal reverse revert review revoke rewind rewrite
rollback rotate round route rstrip run sample sanitise sanitize satisfy save scale
scan schedule scope score scrape scroll seal search seed seek select send separate
serialise serialize serve set setup settle shape share sharpen shift ship shorten
should show shrink shuffle shut shutdown sign signin signup simplify simulate skip
sleep slice slide snapshot soften solve sort spawn specify spin split spread spy
stage stamp start stash step stop store stream strengthen strip stub submit
subscribe substitute subtract succeed suggest sum summarise summarize supply
support suppress suspend swap switch sync synchronise synchronize tag take tally
target teardown tear tell terminate test throttle throw tidy tighten time toggle
tokenise tokenize touch trace track train transfer transform transition translate
transmit trap traverse treat trigger trim truncate trust try tune turn type unbind
uncomment undo unify uninstall unlink unload unlock unmarshal unmount unpack unquote
unregister unset unsubscribe unwrap unzip update upgrade upload upsert use vacuum
validate verify view visit wait wake walk want warm warn was wash watch weigh weight
welcome were widen will win wipe wish work wrap write yield zip
abbreviate accelerate accommodate ack acknowledge alternate amend anticipate
approximate arbitrate articulate automate background bisect blame blur branch
calibrate cat cd chdir chmod chown chroot circulate collaborate comment communicate
compensate concentrate control coordinate correlate cp crash cultivate daemonise
daemonize dedicate deepcopy defrag delineate demonstrate deprovision designate
deviate dictate die dial differentiate disambiguate discriminate dominate educate
elaborate elevate eliminate embed emulate encapsulate endswith equate escalate
evacuate exfiltrate extrapolate fabricate facilitate federate fluctuate formulate
getattr glob grep hasattr hydrate illustrate impersonate incorporate incubate
indicate initiate integrate interrogate interrupt investigate isolate kqueue lint
listdir ls makedirs manipulate mediate mitigate mk mkdir moderate mv navigate
negotiate nice nominate obfuscate omit operate orchestrate originate oscillate
paginate panic participate pickle play popitem postpone precipitate precompute
prefetch preload prewarm prioritise prioritize quantise quantize readline
readlines rebalance recalculate recompile recreate regenerate regulate rehash
reindex reinstate reiterate relaunch renew reparse resample reschedule reseed
resonate retire revalidate rm rmdir rmtree roll rollout rsync saturate scatter scp
secure sed segregate separate setattr setdefault shard shred sniff speculate
splitext splitlines squash ssh startswith stat stimulate stipulate submit sunset
symlink tabulate tail tee tick tolerate transcode transmit triage tunnel unpickle
unplug unseal untar version violate vote wget writelines zero zoom
checkin checkout del expunge reap roundtrip
""".split())

# Words that open a class name as an imperative and almost never as a noun
# modifier. Precision-first: see the module docstring.
VERB_ONLY: frozenset[str] = frozenset("""
activate add allocate apply assign authenticate authorise authorize calculate
collect combine compile configure connect convert create deactivate decode decrypt
delete deserialise deserialize destroy disable disconnect discard do emit enable
encode encrypt ensure evaluate execute fetch find generate get hide initialise
initialize insert inspect install invoke iterate listen locate make manage migrate
modify normalise normalize notify parse perform persist populate prepare publish
put receive reduce register remove rename replace resize resolve retrieve rotate
sanitise sanitize save send serialise serialize subscribe synchronise synchronize
tokenise tokenize transform translate traverse truncate uninstall unregister
unsubscribe validate verify
""".split())

# Verbs that say something happens without saying what (Code Complete, 7.3:
# HandleCalculation, PerformServices, ProcessInput, DealWithOutput). ``do``
# joins them as the Python spelling of the same evasion.
WEAK_VERBS: frozenset[str] = frozenset({"handle", "process", "perform", "do", "manage", "deal"})

# Class-name words that name a job title rather than a thing (Clean Code, ch. 2:
# "Avoid words like Manager, Processor, Data, or Info in the name of a class"),
# plus the junk-drawer pair.
NOISE_WORDS: frozenset[str] = frozenset({"manager", "processor", "data", "info", "helper", "util", "utils"})

# Module and package names that promise nothing about their contents (Go,
# "Package names": avoid util, common, misc).
JUNK_MODULES: frozenset[str] = frozenset(
    {"util", "utils", "utilities", "helper", "helpers", "common", "misc", "stuff"}
)

# Suffixes that turn a verb-first class into a message object: a CQRS command
# or query, an event, the handler for one, or a request and response pair.
COMMAND_SUFFIXES: tuple[str, ...] = (
    "Command", "Query", "Event", "Handler", "UseCase", "Request", "Response", "Job", "Task",
)

# A class whose last word is one of these is a noun phrase whatever its first
# word: ``FetchOptions`` holds options, ``ConnectTimeout`` is a timeout. They
# are an action's attributes and artefacts, so a verb in front is a modifier.
# Words a verb acts on (``User``, ``Order``, ``Token``) stay off the list, or
# ``CreateUser`` would pass; that is also why the agent nouns at the end are
# listed by word and not by an ``-er`` ending that ``Order`` shares.
THING_WORDS: frozenset[str] = frozenset("""
action adapter address args array base batch binding box button case check client
command condition confirmation config context count data declaration decoding
default definition dialog dict encoding engine error event exception factory
failure fake filter fixture flag form handler hook host index info instance
instruction interface interval job kind kwargs label level list listing logging map
mapping mark menu middleware mixin mock mode node operation option output pane
panel params pass path phase plugin point policy pool port position proto protocol
provider query queue range repository request result service set setting signal
size spec spy stack stage state statement status step strategy stub style suite
target task terminal test time timeout traversal tree tuple type usecase view
warning window worker wrapper
adapter allocator balancer builder calculator collector compiler connector consumer
container controller converter decoder decorator deserialiser deserializer dispatcher
driver emitter encoder evaluator executor fetcher formatter generator helper inspector
installer invoker iterator listener loader locator logger manager migrator monitor
notifier observer parser populator processor producer publisher reader receiver
registrar renderer resolver runner scheduler sender serialiser serializer subscriber
tokeniser tokenizer tracker transformer translator validator verifier visitor watcher
writer
""".split())

# Leading words that qualify the verb after them: ``bulk_insert``, ``safe_delete``,
# ``re_apply``, ``atomic_write``. Skipped before the verb test.
MODIFIERS: frozenset[str] = frozenset("""
always async atomic auto batch best blind bulk cold cross deep double dry eager
fast first force full gently hard hot last lazy live manual mass maybe multi never
non partial quick quiet raw re really safe self shallow silent slow soft softly
strict sync try warm
""".split())

# Fused prefixes: ``reload``, ``unquote``, ``deregister``, ``preload``, ``autobegin``,
# ``aclose``. The remainder must itself be a listed verb.
FUSED_PREFIXES: tuple[str, ...] = (
    "re", "un", "de", "pre", "post", "auto", "dis", "mis", "over", "under", "co",
    "inter", "sub", "super", "out", "up", "down", "back", "fore", "self", "a",
)

# Verbs Python idiom fuses onto the next word with no underscore: ``getheaders``,
# ``setdefault``, ``isdigit``, ``iteritems``. Only these heads fuse; ``pass|word``,
# ``end|point`` and ``check|sum`` are nouns that happen to open with a verb.
FUSED_VERB_HEADS: frozenset[str] = frozenset(
    {"get", "set", "is", "has", "add", "del", "iter", "send", "recv", "make", "mk", "rm",
     "put", "load", "dump", "find", "fetch", "walk", "emit", "print", "parse"}
)

# Hooks are named for the moment they run, not for what they do.
HOOK_PREFIXES: tuple[str, ...] = ("on_", "pre_", "post_", "before_", "after_", "pytest_")
HOOK_SUFFIXES: tuple[str, ...] = ("_hook", "_handler", "_callback", "_listener", "_receiver")

# Conversions and alternate constructors are named for their product
# (Rust C-CONV; Clean Code's ``Complex.FromRealNumber``).
CONVERSION_PREFIXES: tuple[str, ...] = ("from_", "to_", "as_", "into_", "with_")
CONVERSION_INFIXES: tuple[str, ...] = ("_to_", "_from_", "_as_")

# Names a script or package reserves for its entry point.
ENTRY_POINTS: frozenset[str] = frozenset({"main", "async_main", "cli"})

# Method names fixed by a standard-library protocol: a class that implements
# one keeps the protocol's name, verb or not.
PROTOCOL_NAMES: frozenset[str] = frozenset("""
acquire callproc clear close commit copy critical cursor debug default
difference_update dst emit error exception executemany fetchall fetchmany fetchone
fileno filter flush format fromkeys fromutc get handle info intersection_update isatty
items join keys nextset notify pop popitem read readable readline readlines release
rollback run seek seekable send setdefault setinputsizes setoutputsize start stop
symmetric_difference_update tell throw truncate tzname update utcoffset values wait
warning writable write writelines
""".split())

# Framework hooks on plain classes: Django middleware and Scrapy pipelines are
# ordinary classes whose method names the framework dictates.
FRAMEWORK_HOOKS: frozenset[str] = frozenset("""
handle_error handle_noargs process_exception process_item process_request
process_response process_spider_input process_spider_output process_start_requests
process_template_response process_view
""".split())

# A function carrying one of these reads as an assertion about its subject
# (``line_has_noqa``, ``path_is_ancestor``), the predicate form every school
# of naming accepts.
PREDICATE_WORDS: frozenset[str] = frozenset(
    {"is", "has", "can", "should", "does", "was", "were", "are", "must", "may", "will"}
)

_CAMEL_SPLIT = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def is_pascal_case(*, name: str) -> bool:
    """True for a conventional class name; ``CONSOLE_INFO`` and ``ctypes_struct`` are not."""
    bare = name.lstrip("_")
    return bare[:1].isupper() and not bare.isupper()


def split_name(*, name: str) -> list[str]:
    """Lower-case words of an identifier: ``getHTTPResponse`` and ``get_http_response`` agree.

    A name with non-ASCII letters splits to nothing, so no rule judges it.
    """
    if not name.isascii():
        return []
    words: list[str] = []
    for part in name.strip("_").split("_"):
        words.extend(token.lower() for token in _CAMEL_SPLIT.findall(part))
    return [word for word in words if word]


def _is_listed_verb(*, word: str, verbs: frozenset[str]) -> bool:
    """*word* or its third-person form is on *verbs*: ``match``, ``matches``, ``applies``."""
    if word in verbs:
        return True
    if word.endswith("ies") and f"{word[:-3]}y" in verbs:
        return True
    return any(word.endswith(ending) and word[: -len(ending)] in verbs for ending in ("es", "s"))


# What is left after a verb in a gerund, an agent noun or a participle
# (``find|ing``, ``log|ger``, ``sort|ed``): an inflection, not a fused word.
_INFLECTIONS: frozenset[str] = frozenset(
    {"ing", "ings", "er", "ers", "or", "ors", "ed", "es", "s", "ter", "ters", "ger",
     "gers", "ting", "tings", "ling", "lings", "ping", "ning", "ding", "ies", "ied", "ier"}
)


def _has_fused_prefix(*, word: str, verbs: frozenset[str]) -> bool:
    """A verb behind a fused prefix or head: ``reload``, ``unquote``, ``getheaders``."""
    for prefix in FUSED_PREFIXES:
        rest = word[len(prefix):]
        if word.startswith(prefix) and len(rest) >= 3 and rest in verbs:
            return True
    for head in FUSED_VERB_HEADS:
        rest = word[len(head):]
        if word.startswith(head) and len(rest) >= 2 and rest not in _INFLECTIONS:
            return True
    return False


def is_verb_capable(*, word: str, extra: frozenset[str] = frozenset()) -> bool:
    """True if *word* can be read as a verb: listed, inflected, suffixed or fused."""
    verbs = VERB_CAPABLE | extra
    if _is_listed_verb(word=word, verbs=verbs):
        return True
    if word.endswith("ify") and len(word) > 4:
        return True
    if word.endswith(("ise", "ize")) and len(word) >= 6:
        return True
    return _has_fused_prefix(word=word, verbs=verbs)


def modifier_count(*, tokens: list[str]) -> int:
    """How many leading tokens are modifiers of the word after them: ``bulk_insert`` gives 1."""
    count = 0
    while count < len(tokens) - 1 and tokens[count] in MODIFIERS:
        count += 1
    return count


def leading_verb_index(*, tokens: list[str], extra: frozenset[str] = frozenset()) -> int:
    """Index of the verb that opens *tokens* once leading modifiers are skipped, or -1.

    ``["bulk", "insert", "rows"]`` gives 1; ``["cert", "verify"]`` gives -1 because
    the verb does not lead.
    """
    index = modifier_count(tokens=tokens)
    return index if is_verb_capable(word=tokens[index], extra=extra) else -1


def postposed_verb_index(*, tokens: list[str], extra: frozenset[str] = frozenset()) -> int:
    """Index of the last listed verb sitting after the first judged word, or -1.

    ``["cert", "verify"]`` gives 1 and ``["user", "count", "update"]`` gives 2:
    a name that trails its verb puts the object first, so the verb is the last
    listed word. Exact membership only, so a rename never promotes a word that
    merely might be a verb (``ports``, ``finding``).
    """
    verbs = VERB_CAPABLE | extra
    start = modifier_count(tokens=tokens)
    for index in range(len(tokens) - 1, start, -1):
        if tokens[index] in verbs:
            return index
    return -1


def verb_first(*, name: str, tokens: list[str], index: int) -> str:
    """*name* rebuilt with the verb at *index* ahead of everything but its modifiers.

    Leading underscores are kept and a digit token is reattached to the word
    before it: ``_cert_verify2`` gives ``_verify_cert2``, ``bulk_cert_verify``
    gives ``bulk_verify_cert``.
    """
    prefix = name[: len(name) - len(name.lstrip("_"))]
    start = modifier_count(tokens=tokens)
    order = [*tokens[:start], tokens[index], *tokens[start:index], *tokens[index + 1:]]
    joined = ""
    for token in order:
        joined += token if token.isdigit() or not joined else f"_{token}"
    return prefix + joined


def is_predicate(*, tokens: list[str]) -> bool:
    """True if the name reads as an assertion: ``is_empty``, ``line_has_noqa``."""
    return any(token in PREDICATE_WORDS for token in tokens)


def is_noun_phrase(*, tokens: list[str]) -> bool:
    """True if the last word of a class name is an action's artefact, so the name is a thing."""
    words = [token for token in tokens if not token.isdigit()] or list(tokens)
    head = words[-1]
    return head in THING_WORDS or (head.endswith("s") and head[:-1] in THING_WORDS)
