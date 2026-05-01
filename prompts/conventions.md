openLab is a Docsify-rendered documentation site.

Navigation:
- `_sidebar.md` is the navigation source of truth.
- The curated page layer is the root markdown files listed in `_sidebar.md`.
- New root markdown files that are not linked from `_sidebar.md` are likely orphans and should be flagged.

Content organization:
- Root markdown pages use kebab-case filenames.
- `tools.md` and `methods.md` are already large. Flag changes that substantially grow either file when a split-out page or existing page would keep the site easier to navigate.
- Re-uploads that delete one asset and add a very similar filename are usually mistakes and should be flagged.

Asset directories:
- `img/`, `docs/`, `manuals/`, `ref/`, and `pano/` are asset directories, not navigable page collections.
- `manuals/`, `docs/`, and `ref/` often expose naming drift to readers. Compare new filenames with nearby siblings.

Naming patterns:
- Historical asset filenames are inconsistent. Prefer the most common recent pattern in the same directory when suggesting a correction.
- `manuals/` has mixed forms such as `2025 Manual - X`, `2025 manual - x`, and `Manual - X`; flag new drift when the local sibling pattern is clear.
