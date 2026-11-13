# Blind labelling: is this small package over-split?

You are labelling small Python packages for a study of module structure. Work
alone, from the code only. Do not use the web. Do not read anything under
`/home/user/lanorme/`: it holds material that would bias your labels.

## The batch

Read the JSON file at `{batch_path}`. It is a list of packages. Each entry has
an `id`, the `root` of a project checkout, the `package` directory inside that
root, and the file names of the package's `members`.

## What to decide

Each package is a directory of a few modules. For each one, decide what a careful
maintainer of that project would conclude if asked: "Is each of these modules
really its own thing, or should they be one module?"

Give exactly one of three labels:

- `merge`: the package is over-split. Merging its modules into one module
  would leave the design no worse for the project's maintainers and would
  break nothing: no public import path that outside users rely on, no module
  a framework loads by its name or location, no layout that sibling packages
  repeat on purpose.
- `contested`: the split follows a recognisable convention that reasonable
  maintainers disagree about (for example one router per resource, one use
  case per module, one value object per module, one settings module per
  environment). It is defensible, so it is not a clear over-split, but it is
  not clearly necessary either.
- `keep`: the modules are clearly separate things, or merging would break
  something (a public path, a module loaded by name, a plugin or command
  directory, a per-feature layout its siblings repeat).

To decide, read every member of the package, and look at who imports the
members elsewhere in the project (search the checkout). Judge the package as
it is in this project, not in general.

## Output

Write a JSON list to `{output_path}`, one object per package, in the batch's
order:

```json
{"id": "...", "label": "merge", "confidence": "high", "reason": "one sentence"}
```

`confidence` is `high`, `medium` or `low`. Keep each reason to one sentence
that names the deciding fact. Then reply with one line: the number of packages
labelled and the count of each label. Do not include anything else in your
reply.
