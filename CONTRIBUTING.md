# Contributing

1. Add protocol behavior to `specs/` first.
2. Add or update a shared fixture.
3. Implement the behavior in TypeScript and Python.
4. Run both language test suites and package builds.
5. Explain security and compatibility effects in the pull request.

Public APIs follow semantic versioning. Payment serialization, network constants, receipt fields,
and error codes must remain compatible across languages.
