# Maintained Tag Manager feed

This fork starts from upstream commit `d28de98bdde0270d7b2a9f885aa5c9f1eb81fff7` in [carrotwaxr/stash-plugins](https://github.com/carrotwaxr/stash-plugins). Upstream history and MIT notices remain intact. Only Tag Manager is maintained and packaged; other upstream plugin source remains for provenance.

## VEX-94: invalid UTF-8 response handling

Tag Manager 0.7.1 strictly decodes UTF-8. Invalid responses share the existing HTTP/connection retry budget (four attempts by default, with 1/2/4 second backoff), then raise `StashDBResponseError`, a `StashDBAPIError` subtype. No replacement decoding or corrupt metadata acceptance is used. Diagnostic fields are normalized status, byte count, content type/encoding/length, decode offsets and gzip signature; body text, arbitrary headers, URLs and request variables are omitted from these request diagnostics.

Sync counts every scene in a failed batch or failed individual lookup, continues other lookups, and reports `partial: true` with an incomplete result when errors occur. Earlier successful scene updates and their durable history are retained. The `tagManager.yml` runtime ID, `PLUGIN_ID`, settings and `plugin_data/tagManager` layout stay compatible. Tests use synthetic response bytes, fake local clients and temporary history databases. The original response bytes and headers were not retained; the original cause remains unknown.

## Review before publication

1. Review the immutable draft PR commit and its offline test results.
2. Build the reviewed commit twice with `bash build_site.sh <output directory>`. Verify identical zip/index bytes and run `python .github/scripts/check_tagmanager_package.py <output directory>`.
3. Record the full commit, package SHA-256, feed version, upstream base and CI result. If merging changes the commit, rebuild and record that resulting commit before tagging.
4. After independent review, merge the draft PR into this fork's `main`. Enable GitHub Pages with the Actions build source if necessary. Push an explicit tag named `tagManager-v0.7.1` at the reviewed release commit. Branch pushes and merges alone do not publish this fork's new workflow.

The tiny CI job runs only Tag Manager's offline Python/JavaScript tests, manifest lint and package verification. The Pages release retains the existing zip/index layout, fixed timestamps, permissions and file order. The root MIT license ships at `LICENSE` inside the plugin zip. Tests, samples, caches, environment files and persisted plugin state are excluded.

Expected feed URLs after the first reviewed publication:

- `https://rzp-labs.github.io/stash-plugins/stable/index.yml`
- `https://rzp-labs.github.io/stash-plugins/develop/index.yml` (same package)
- `https://rzp-labs.github.io/stash-plugins/stable/tagManager.zip`

Feed ID and zip manifest remain `tagManager` and `tagManager.yml`. The index version is `<manifest version>-<last shipped-file commit>`; `sha256` authenticates the exact package bytes. Package source is the committed tree, including `LICENSE`, so local uncommitted files never ship.

## Installation ownership

Publication is separate from live installation. Migration belongs to the deployment owner: first back up the existing plugin directory, source/settings configuration, and `plugin_data/tagManager` history/cache with their permissions; verify the new feed hash and replace the same runtime ID. Roll back the package and source/settings configuration from those backups if needed. Preserve history and do not run the Reset Scene Tag Sync History task or library sync as part of migration. This repository's development and CI do not contact live Stash or stash-box instances.
