# Architecture Profiles for layer_deps

The `layer_deps` check ships three named presets that cover the most common
layered-architecture styles. Activate one with the `profile` key in your
`lanorme.toml`; any `layers` or `allowed` keys you add in the same section
override the preset defaults.

```toml
[tool.lanorme.layer_deps]
profile = "hexagonal"
```

---

## hexagonal

Classic hexagonal (ports-and-adapters) architecture. The application layer
depends only on the domain; infrastructure and api are outermost and both
depend inward on the domain and application layers respectively.

Layers (inside-out): `domain`, `application`, `infrastructure`, `api`

| Layer          | May import from                |
|----------------|-------------------------------|
| domain         | nothing                        |
| application    | domain                         |
| infrastructure | domain                         |
| api            | domain, application            |

Note: `infrastructure` does not import `application` in this preset. Use
`four-layer` if your adapters need application-layer ports directly.

---

## four-layer

A relaxed four-layer variant where infrastructure adapters are allowed to
import the application layer (for example to implement ports defined there).

Layers (inside-out): `domain`, `application`, `infrastructure`, `api`

| Layer          | May import from                |
|----------------|-------------------------------|
| domain         | nothing                        |
| application    | domain, infrastructure         |
| infrastructure | domain                         |
| api            | domain, application            |

Example config (without using the profile key):
`examples/four-layer/lanorme.toml`

---

## clean

Clean Architecture as described by Robert C. Martin. Concentric circles with
entities at the centre.

Layers (inside-out): `entities`, `use_cases`, `interface_adapters`, `frameworks`

| Layer               | May import from                                   |
|---------------------|--------------------------------------------------|
| entities            | nothing                                           |
| use_cases           | entities                                          |
| interface_adapters  | use_cases, entities                               |
| frameworks          | interface_adapters, use_cases, entities           |

---

## Overriding a preset

Profile defaults are applied first; explicit keys in your config override them.

```toml
[tool.lanorme.layer_deps]
profile = "hexagonal"
# Allow application to also import from shared/ (an extra layer not in the preset).
layers = ["domain", "shared", "application", "infrastructure", "api"]

[tool.lanorme.layer_deps.allowed]
application    = ["domain", "shared"]
infrastructure = ["domain", "shared"]
api            = ["domain", "application"]
```

---

## Unknown profile

If you specify a `profile` value that does not match any built-in preset,
`layer_deps` emits a `LAYER-CFG-001` warning and falls back to the default
layer rules. No findings are suppressed silently.
