# Nexus V3 production overlay — 2026-11-10

This directory contains the checksummed A and B+C overlay inputs used for the production candidates. Large OCI image archives are intentionally not stored in Git.

## Images

| Service | OCI manifest digest | NAS image config ID | Archive SHA-256 |
|---|---|---|---|
| Bridge | `sha256:ae971cf48ac0fedc0e3e0115e25d8abfffe4b27eca82be56cbeaf97a3d9ff11b` | `sha256:960ddef86c86419338dc414e25d3a590a251e1af422a15e0c1d568024bd7e0ab` | `af1cc56eacb5c2f721526e6cd90dc5425b07d55c36b3e0aeb1c96889c69a6893` |
| Core | `sha256:bdc471947e6fdcae78dd5a70bff4a206794a69b4afca690ea584c0aae98c5b79` | `sha256:9a6b125ee195a46a7ebd3ab3deca13e108053b321c16f40001acbc33a90ceb7e` | `ca2470832ef609e4565eb569a0c030a66e6ac3489193333a3722a7e0341636f2` |

Run `sha256sum -c A/bridge/SHA256SUMS` from `A/bridge` and `sha256sum -c BC/SHA256SUMS` from `BC` to verify the source overlays. Production base tags are `nexus-bridge:m0` and `nexus-core:vision4-labels-20261108`.

## Production state verified

- Bridge and Core candidates are running in the `nexus` Compose project; Core and PostgreSQL are healthy, with zero restarts at verification time.
- PostgreSQL 16.15 counts remained 4 sources, 9 rules, 10 bindings, 2,599 raw messages, and 1,033 listings. All 4 sources remain in `legacy` mode.
- The four additive migrations are present. Active Router is not enabled.
- `ocr_classifier_engine` remains `vision4`; the existing model mount is unchanged.
- The pre-deploy dump and config bundle remain in the NAS-only `release-backups/v3-predeploy-20261110T073109` directory. Its checksum manifest and isolated restore were verified. No production secrets or database data are included here.

Migrations are ordered by filename in `BC/scripts/migrations/`. The schema initialization fix under `BC/schema-init/` is for initialization/recovery only; do not apply it over an existing production database.
