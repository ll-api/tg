import json
import tempfile
import unittest
from pathlib import Path

from update_api import check_artifacts, count_entries, extract_returns, merge_aliases, parse, parse_types, validate, write_artifacts

FIXTURE = b"""<h3>Recent changes</h3><p>November 22, 2026</p><p>Bot API 11.0</p>
<h4>sendMessage</h4><p>Sends a message.</p>
<table><tr><th>Parameter</th><th>Type</th><th>Required</th><th>Description</th></tr>
<tr><td>chat_id</td><td>Integer or String</td><td>Yes</td><td>Chat ID.</td></tr>
<tr><td>reply_markup</td><td>InlineKeyboardMarkup</td><td>No</td><td>Keyboard.</td></tr></table>
<h4>getMe</h4><p>Requires no parameters. Returns a BotFullInfo object.</p>
<h4>Update</h4><p>An update.</p>
<table><tr><th>Field</th><th>Type</th><th>Description</th></tr>
<tr><td>update_id</td><td>Integer</td><td>Update ID.</td></tr>
<tr><td>message</td><td>Message</td><td><em>Optional</em>. Message.</td></tr></table>
<h4>InlineKeyboardMarkup</h4><p>A keyboard.</p>
<table><tr><th>Field</th><th>Type</th><th>Description</th></tr>
<tr><td>inline_keyboard</td><td>Array of Array of <a>InlineKeyboardButton</a></td><td>Rows.</td></tr></table>
<h4>BotFullInfo</h4><p>Bot information.</p>
<h4>InputMedia</h4><p>It can be one of:</p><ul><li>InputMediaPhoto</li><li>InputMediaVideo</li></ul>
<h4>InputMediaPhoto</h4><p>Photo media.</p>
<h4>InputMediaVideo</h4><p>Video media.</p>"""


class UpdateAPITest(unittest.TestCase):
    def test_parses_new_release_with_no_hardcoded_counts(self):
        doc = parse(FIXTURE)
        self.assertEqual(doc["version"], "Bot API 11.0")
        self.assertEqual(doc["release_date"], "November 22, 2026")
        self.assertNotIn("counts", doc)
        self.assertEqual(count_entries(doc), {"methods": 2, "parameters": 2, "types": 6, "fields": 3})
        self.assertIsNone(doc["methods"]["getMe"]["fields"])
        chat_id, keyboard = doc["methods"]["sendMessage"]["fields"]
        self.assertEqual(chat_id["types"], ["Integer", "String"])
        self.assertTrue(chat_id["required"])
        self.assertFalse(keyboard["required"])
        self.assertFalse(doc["types"]["Update"]["fields"][1]["required"])
        self.assertEqual(
            doc["types"]["InlineKeyboardMarkup"]["fields"][0]["types"],
            ["Array of Array of InlineKeyboardButton"],
        )
        self.assertEqual(doc["methods"]["getMe"]["returns"], ["BotFullInfo"])
        self.assertEqual(doc["types"]["InputMedia"]["subtypes"], ["InputMediaPhoto", "InputMediaVideo"])
        self.assertEqual(doc["types"]["InputMediaPhoto"]["subtype_of"], ["InputMedia"])

    def test_parses_comma_and_and_array_unions(self):
        self.assertEqual(
            parse_types("Array of InputMediaAudio , InputMediaDocument and InputMediaVideo"),
            ["Array of InputMediaAudio", "Array of InputMediaDocument", "Array of InputMediaVideo"],
        )
        self.assertEqual(extract_returns("On success, an Array of Message objects is returned.", {"Message"}), ["Array of Message"])

    def test_rejects_partial_schema(self):
        with self.assertRaisesRegex(ValueError, "missing methods"):
            parse(b"<p>Bot API 11.0</p>")
        doc = parse(FIXTURE)
        doc["counts"] = {"methods": 2, "parameters": 2, "types": 2, "fields": 4}
        with self.assertRaisesRegex(ValueError, "counts"):
            validate(doc)

    def test_rejects_snapshot_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            document = parse(FIXTURE)
            write_artifacts(document, directory)
            check_artifacts(directory)
            self.assertNotIn("counts", json.loads((directory / "api.json").read_text(encoding="utf-8")))
            document["release_date"] = "unknown"
            (directory / "api.min.json").write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differ"):
                check_artifacts(directory)

    def test_preserves_aliases_for_method_and_type_renames(self):
        previous = parse(FIXTURE)
        method_renamed = parse(FIXTURE.replace(b"sendMessage", b"deliverMessage"))
        merged_method = merge_aliases(previous, method_renamed)
        self.assertEqual(merged_method["aliases"]["methods"], {"sendMessage": "deliverMessage"})

        type_renamed = parse(FIXTURE.replace(b"InlineKeyboardMarkup", b"KeyboardMarkup"))
        merged_type = merge_aliases(previous, type_renamed)
        self.assertEqual(merged_type["aliases"]["types"], {"InlineKeyboardMarkup": "KeyboardMarkup"})
        validate(merged_method)
        validate(merged_type)

    def test_alias_chain_points_to_latest_name(self):
        previous = parse(FIXTURE)
        previous["aliases"] = {"methods": {"legacySend": "sendMessage"}, "types": {}}
        current = parse(FIXTURE.replace(b"sendMessage", b"deliverMessage"))
        merged = merge_aliases(previous, current)
        self.assertEqual(merged["aliases"]["methods"], {
            "legacySend": "deliverMessage",
            "sendMessage": "deliverMessage",
        })


if __name__ == "__main__":
    unittest.main()
