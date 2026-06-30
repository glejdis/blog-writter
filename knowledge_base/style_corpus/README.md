# Style corpus

Drop example blog posts here that represent the **house writing style** you want
the agents to imitate — e.g. *The Cloud Wire* posts.

The **Stylist agent** reads every `.md` file in this folder (except this
`README.md`), distills a reusable **Style Card** (voice, structure, sentence
rules, diction), and the **Writer** follows that card when drafting and improving
posts. The card shapes *how* the post reads — it never overrides citation or
factual-accuracy rules.

## How to add examples

1. Save each example post as its own `.md` file in this folder. Real published
   posts in the target voice work best.
2. Keep filenames descriptive, e.g. `cloud-wire-landing-zones.md`.
3. Files beginning with `_` (like `_structural-patterns.md`) sort first and act
   as shared references the Stylist always sees before the example posts.

## Notes

- An empty corpus (no `.md` files besides this README) makes the Stylist stage a
  no-op — the pipeline simply skips it and the Writer uses its built-in style.
- Large drops are fine: the loader caps total and per-file size so the model
  context window is never blown (see `tools/style_corpus.py`).
- Toggle the whole stage off with `BLOG_WRITER_STYLE=false`.
