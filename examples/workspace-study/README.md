# Workspace study example

Two experiment folders, each with its own `runevidence.json`, composed by `rungraph.json`:

```
workspace-study/
  rungraph.json          workspace document
  baseline/              member manifest and metrics
  tuned/                 member manifest and metrics
```

```bash
rungraph validate examples/workspace-study --strict
rungraph report examples/workspace-study
rungraph impact examples/workspace-study --from result:tuned#tuned-accuracy
rungraph compare examples/workspace-study --metric test_accuracy
```

The workspace claim `tuned-higher` cites a result in the `tuned` member. All commands are
read-only and work offline; none rewrites a member manifest.
