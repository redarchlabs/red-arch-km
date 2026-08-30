# Working in this repository

KM2 is a general-purpose, multi-tenant configuration platform: entities, views,
workflows, agents and knowledge, all defined as data per organisation. It is open
source, and it is not any one customer's product.

## Never commit tenant-specific artifacts

**Nothing that belongs to a specific customer, org or engagement goes in this
repository — not code, not data, not names, not example paths, not test
fixtures, not comments.** Tenant content lives in that project's own private
repository and reaches a running stack through the public API, the org asset
store, or a migration bundle.

The platform is the general capability. Everything a particular customer builds
on it is configuration, and configuration is theirs.

### The test

Before committing, ask: **would this make sense to an unrelated org adopting the
platform?**

- Yes → it belongs here. Generic capability, generic vocabulary.
- No → it belongs in the project's private repository.

If something is genuinely useful but currently written in a customer's terms,
the answer is not to drop it. Genericise it and keep the capability: a 3D model
element is platform work, a specific customer's vehicles are not.

### What counts as tenant-specific

Easy to spot: a customer's org name, product names, asset identifiers, seed
scripts, screenshots, geometry, branding.

Easy to MISS, and where it actually accumulates:

- **Example paths in help text and placeholders.** An author copies what the UI
  shows them, so an example path is effectively shipped configuration.
- **Test fixtures.** Sample record names, org names and document ids.
- **Comments and docstrings.** A comment explaining a decision by reference to
  the customer that motivated it.
- **Prompt strings.** These are the worst case: text reaching a model is
  behaviour, not documentation, and a customer's vocabulary there biases output
  for every other org.
- **Commit messages and branch names.** Both are published. A clean diff under a
  message naming a customer's project is still a disclosure.
- **Demo artwork** under `ui/public/`. Ships in every build, for every tenant.

Words like "fleet" (of agents), "simulator" (the LMS role-play feature) and
"ships" (the verb) are the platform's own vocabulary. Genericising is about
whose *domain* the text describes, not banning words.

### Auditing

```bash
git grep -inE "<customer>|<their product names>|<their identifiers>"
```

A clean working tree is not a clean repository — history is published too. If
something tenant-specific was already committed, say so rather than quietly
scrubbing forward: removing it from history means rewriting and force-pushing,
which is a decision for the repository's owner, and a rewrite does not
un-publish what others may already have cloned.

## Demo and example content

Generic demo material is welcome — it is how people learn the platform. Keep it
recognisably synthetic and domain-neutral (a pump, a turbine, a work order, a
generic org name), and put shared artwork under `ui/public/demo/`.
