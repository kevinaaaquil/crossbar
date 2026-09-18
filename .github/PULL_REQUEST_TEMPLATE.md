**What this changes**

**Why**

What problem does it solve? If it changes a number crossbar prints, say which.

**How it was verified**

- [ ] `pytest` is green locally
- [ ] New behaviour has a test that fails without the change
- [ ] The test does not mock the MCP layer, the harness loop, or the scorer
- [ ] Anything random is seeded reproducibly across processes (`crc32`, not `hash()`)
- [ ] Docs updated in this PR if behaviour or configuration changed

**Before / after** (if the report output changed)

```
```

**Licence**

By submitting this, you agree your contribution is licensed under the
[PolyForm Noncommercial License 1.0.0](../LICENSE.md), the same terms as the
rest of the project.
