# GitHub wheel releases

The `MkChad wheels` workflow builds and tests pushes to `mkchad-0.5.2` without
publishing. Only an explicit `vMAJOR.MINOR.PATCH+mkchad.N` tag push publishes a
GitHub release. `N` is a positive maintenance counter. Ordinary upstream tags,
branch pushes and release-page events do not publish wheels through this workflow.
The PEP 440 package version keeps the local modifier, for example
`0.5.2+mkchad.1`; the corresponding tag is `v0.5.2+mkchad.1`.

## Release deliberately

1. Commit the intended package version in `jupyter_ai_tools/__init__.py` and push
   the maintenance branch. Wait for its `MkChad wheels` build to pass.
1. Tag the reviewed commit with the **exact** v-prefixed package version:

```bash
git tag -a 'v0.5.2+mkchad.1' <reviewed-commit> -m 'MkChad AI Tools 0.5.2+mkchad.1'
git push origin 'refs/tags/v0.5.2+mkchad.1'
```

The workflow independently checks version agreement, maintenance-branch ancestry,
tests, and both archives' package metadata. A successful tag run creates a draft,
attaches the wheel, sdist, `SHA256SUMS` and `build-provenance.json`, then publishes
it. The release is not promoted to GitHub's global Latest release. No PyPI/npm
publication, registry credentials or personal access token is needed; only the
release job receives `GITHUB_TOKEN` contents-write permission.

Checksums cover both archives and the provenance record. The record includes the
source commit, version and build tool versions. Download the assets and run
`sha256sum --check SHA256SUMS` before deployment. CI builds include the new release
files in their sdists; use their own published hashes, not a previous local build's
hashes.

## Tag rules

Two active GitHub tag rulesets target `refs/tags/v*+mkchad.*`:

- **Admin creation:** only repository administrators may create matching tags.
- **Immutability:** tags cannot be moved or deleted. This second ruleset has no
  bypass actor, including admins. Exact tag format/version agreement is enforced
  by the workflow; GitHub rejected a tag-name-pattern rule for these repositories.

The desired ruleset payloads are source-controlled in
`.github/mkchad-tag-creation.json` and `.github/mkchad-tag-integrity.json`.
They are GitHub repository settings, not automatically applied by pushing files.
An administrator can create them with:

```bash
gh api --method POST repos/krafczyk/jupyter-ai-tools/rulesets --input <payload-file>
```

Update the existing ruleset ID with PUT rather than creating duplicates on
subsequent changes.

Failed tags are not retargeted. Fix the source, increment `+mkchad.N`, and create
a new tag. If publication stopped after creating a draft, inspect that draft
before retrying: the workflow refuses to overwrite an existing release or its
assets. GitHub administrators can deliberately change repository settings; the
rules protect normal Git operations, not against the repository owner changing
the policy. These rules protect Git tags, not edits to release assets in the UI.

The inherited upstream Jupyter Releaser workflows are separate manual upstream
tooling and are not part of this GitHub-only maintenance path. Do not use them
for MkChad wheel publication.
