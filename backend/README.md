# Backend

Production backend for one cooperative clearing node.

The package is a modular monolith. Domain modules do not import HTTP or ORM
frameworks. Runtime configuration contains paths to secret files, never secret
values.

Common commands:

```text
coopctl init-node
coopctl seed-demo
coopctl worker
coopctl worker-health
coopctl export-openapi --output /tmp/openapi.json
```
