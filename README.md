# Telegram Bot API

This repository publishes a machine-readable snapshot of the official Telegram Bot API documentation.

Source: <https://core.telegram.org/bots/api>

`api.json` and `api.min.json` contain the same data. Each field's `types`
list preserves array nesting using Telegram's notation (for example,
`Array of Array of InlineKeyboardButton`). Consumers should not assume that
an array is represented by its element type alone.

The top-level `aliases` object records compatible renames detected between
snapshots: `aliases.methods` and `aliases.types` map an old name to the
current canonical name. Consumers should resolve an old name through this
map before generating calls or type references. Entity counts are deliberately
not stored as duplicated top-level metadata; consumers should derive them from
the `methods` and `types` collections.

The updater uses Python 3.13 in GitHub Actions and has no third-party dependencies.

```sh
python update_api.py          # Fetch Telegram docs and regenerate both JSON files
python update_api.py --check  # Validate both JSON files offline
python -m unittest -v        # Run the parser and artifact tests
```

To regenerate offline from a saved copy of the official page, run
`python update_api.py --source-html /path/to/telegram-api.html`.
