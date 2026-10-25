import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

from PySide6.QtGui import QImage

from visor.activity_document import (
    ActivityPolicy,
    _relative_image,
    list_replies,
    read_document,
    resolve_image,
    save_reply,
)


class ActivityDocumentCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "board.json"
        self.question = {"id": "decision", "kind": "question", "title": "Choose next step",
                         "prompt": "What should we review?", "choices": ["Public release", "Income"]}
        self.document = {"schema_version": 1, "id": "board-1", "title": "Local activity",
                         "revision": 3, "session_link": {"id": "codex:unverified", "label": "Display hint"},
                         "parent_record": "private/owning-record.json", "cards": [self.question]}

    def write(self, value=None):
        self.path.write_text(json.dumps(self.document if value is None else value), encoding="utf-8")
        return self.path

    def image(self, path=None, width=8, height=6):
        path = path or self.root / "image.png"
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(0xFF008080)
        self.assertTrue(image.save(str(path)))
        return path

    def test_all_card_kinds_preserve_fields_and_display_hint(self):
        self.document["cards"] += [
            {"id": "text", "kind": "text", "title": "Notes", "html": "<p>Hello</p>", "geometry": [-2, 0, 300, 150]},
            {"id": "image", "kind": "image", "title": "Example", "path": "image.png", "caption": "Local"},
            {"id": "diagram", "kind": "diagram", "title": "Flow",
             "nodes": [{"id": "a", "label": "A", "x": 0, "y": 1, "w": 80, "h": 40, "color": "#008080"},
                       {"id": "b", "label": "B", "x": 100, "y": 1, "w": 80, "h": 40}],
             "edges": [{"from": "a", "to": "b", "label": "next"}]},
            {"id": "chart", "kind": "chart", "title": "Comparison", "labels": ["A", "B"],
             "values": [1, -2.5], "unit": "hours"}]
        self.assertEqual(read_document(self.write()), self.document)
        self.assertNotIn("verified", read_document(self.path)["session_link"])

    def test_byte_limit_and_duplicate_json_keys_and_nonfinite_numbers(self):
        self.write()
        with self.assertRaisesRegex(ValueError, "byte limit"):
            read_document(self.path, ActivityPolicy(max_document_bytes=10))
        for content in ('{"id":"a","id":"b"}', '{"value":NaN}', '{"value":Infinity}'):
            self.path.write_text(content)
            with self.subTest(content=content), self.assertRaises(ValueError):
                read_document(self.path)

    def test_schema_identity_revision_card_and_size_rejections(self):
        mutations = [lambda d: d.update(id="../escape"), lambda d: d.update(schema_version=True),
                     lambda d: d.update(revision=True), lambda d: d.update(revision=0),
                     lambda d: d["cards"].append(copy.deepcopy(d["cards"][0])),
                     lambda d: d["cards"][0].update(kind="iframe"),
                     lambda d: d["cards"][0].update(caption={"bad": "value"}),
                     lambda d: d["cards"][0].update(geometry=[0, 0, 0, 2]),
                     lambda d: d["cards"][0].update(geometry=[0.5, 0, 300, 200]),
                     lambda d: d["cards"][0].update(geometry=[100001, 0, 300, 200]),
                     lambda d: d["cards"][0].update(geometry=[0, 0, 99, 80]),
                     lambda d: d["cards"][0].update(geometry=[False, 0, 300, 200]),
                     lambda d: d["cards"][0].update(geometry=[0, 0, 2]),
                     lambda d: d["cards"][0].update(choices=["A", "A"])]
        for mutate in mutations:
            document = copy.deepcopy(self.document)
            mutate(document)
            with self.subTest(document=document), self.assertRaises(ValueError):
                read_document(self.write(document))
        self.write()
        with self.assertRaises(ValueError):
            read_document(self.path, ActivityPolicy(max_text_chars=5))
        self.document["cards"].append({"id": "text", "kind": "text", "title": "T", "html": ""})
        with self.assertRaises(ValueError):
            read_document(self.write(), ActivityPolicy(max_cards=1))

    def test_chart_and_diagram_reject_bad_values_and_broken_edges(self):
        for value in (float("inf"), float("nan"), True, "1", 10 ** 500):
            self.document["cards"] = [{"id": "chart", "kind": "chart", "title": "T", "labels": ["A"], "values": [value]}]
            with self.subTest(value=str(value)[:30]), self.assertRaises(ValueError):
                read_document(self.write())
        self.document["cards"][0].update(labels=[], values=[1])
        with self.assertRaises(ValueError):
            read_document(self.write())
        self.document["cards"] = [{"id": "diagram", "kind": "diagram", "title": "T",
                                   "nodes": [], "edges": [{"from": [], "to": "missing"}]}]
        with self.assertRaises(ValueError):
            read_document(self.write())

    def test_image_rejects_traversal_remote_windows_paths_and_nonraster(self):
        self.write()
        self.image()
        self.assertEqual(resolve_image(self.path, "image.png"), (self.root / "image.png").resolve())
        for value in ("../image.png", "/tmp/image.png", "https://example.com/image.png", "C:\\image.png", "image.svg"):
            with self.subTest(path=value), self.assertRaises(ValueError):
                resolve_image(self.path, value)

    def test_windows_rooted_image_paths_are_rejected_before_filesystem_lookup(self):
        with patch("visor.activity_document.Path", PureWindowsPath):
            for value in ("/tmp/image.png", "/image.png"):
                with self.subTest(path=value), self.assertRaisesRegex(ValueError, "stay relative"):
                    _relative_image(value, ActivityPolicy())
            self.assertEqual(_relative_image("assets/image.png", ActivityPolicy()),
                             PureWindowsPath("assets/image.png"))

    @unittest.skipIf(os.name == "nt", "Native symlink creation may require extra privilege")
    def test_image_symlink_escape_rejected_and_internal_link_accepted(self):
        self.write()
        self.image()
        (self.root / "link.png").symlink_to("image.png")
        self.assertEqual(resolve_image(self.path, "link.png"), (self.root / "image.png").resolve())
        with tempfile.TemporaryDirectory() as outside:
            target = self.image(Path(outside) / "outside.png")
            (self.root / "escape.png").symlink_to(target)
            with self.assertRaises(ValueError):
                resolve_image(self.path, "escape.png")

    def test_image_header_pixels_bytes_suffix_and_missing_file(self):
        self.write()
        image = self.image()
        with self.assertRaisesRegex(ValueError, "pixel limit"):
            resolve_image(self.path, image.name, ActivityPolicy(max_image_pixels=20))
        with self.assertRaisesRegex(ValueError, "byte limit"):
            resolve_image(self.path, image.name, ActivityPolicy(max_image_bytes=10))
        (self.root / "wrong.jpg").write_bytes(image.read_bytes())
        (self.root / "bad.png").write_text("not an image")
        for name in ("wrong.jpg", "bad.png"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                resolve_image(self.path, name)
        with self.assertRaises(FileNotFoundError):
            resolve_image(self.path, "missing.png")

    def test_reply_is_atomic_private_revision_bound_and_local_only(self):
        state = self.root / "state"
        answer = "Income\nFirst compare costs and available time."
        event = save_reply(state, self.document, self.question, answer)
        self.assertEqual(event["answer"], answer)
        self.assertEqual(event["document_revision"], 3)
        self.assertEqual(event["board_id"], "board-1")
        self.assertEqual(event["card_id"], "decision")
        self.assertEqual(event["prompt_sha256"], hashlib.sha256(self.question["prompt"].encode()).hexdigest())
        self.assertEqual(event["origin"], "operator_local_preview")
        self.assertEqual(event["recipient_hint"], self.document["session_link"])
        path = state / "replies" / (event["id"] + ".json")
        self.assertEqual(json.loads(path.read_text()), event)
        self.assertEqual(list_replies(state, "board-1"), [event])
        self.assertEqual(list_replies(state, "other"), [])
        self.assertEqual(list((state / "replies").glob("*.tmp")), [])
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        next_event = save_reply(state, self.document, self.question, "Different freeform answer")
        self.assertNotEqual(next_event["id"], event["id"])
        self.assertEqual(len(list_replies(state)), 2)

    def test_reply_rejects_wrong_prompt_unknown_card_and_invalid_answer(self):
        state = self.root / "state"
        for card in (dict(self.question, prompt="Changed question"), dict(self.question, id="foreign")):
            with self.subTest(card=card), self.assertRaises(ValueError):
                save_reply(state, self.document, card, "Answer")
        for answer in ("", "   ", None, "A" * 11):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                save_reply(state, self.document, self.question, answer, ActivityPolicy(max_answer_chars=10))
        self.assertFalse(state.exists())

    def test_atomic_failure_leaves_no_partial_or_temporary_reply(self):
        state = self.root / "state"
        with patch("visor.activity_document.os.replace", side_effect=OSError("interrupted")), self.assertRaises(OSError):
            save_reply(state, self.document, self.question, "Answer")
        self.assertEqual(list((state / "replies").iterdir()), [])
        self.assertEqual(list_replies(state), [])

    @unittest.skipIf(os.name == "nt", "POSIX permissions and symlink checks")
    def test_reply_directory_and_file_symlinks_are_rejected(self):
        state = self.root / "state"
        state.mkdir()
        (state / "replies").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            save_reply(state, self.document, self.question, "Answer")
        with self.assertRaises(ValueError):
            list_replies(state)
        (state / "replies").unlink()
        event = save_reply(state, self.document, self.question, "Answer")
        (state / "replies" / "bad.json").symlink_to(state / "replies" / (event["id"] + ".json"))
        with self.assertRaises(ValueError):
            list_replies(state)

    def test_reply_readback_rejects_tampered_identity_revision_and_origin(self):
        state = self.root / "state"
        event = save_reply(state, self.document, self.question, "Answer")
        path = state / "replies" / (event["id"] + ".json")
        for changes in ({"id": "foreign"}, {"document_revision": True}, {"origin": "agent"}, {"prompt_sha256": "bad"}):
            path.write_text(json.dumps({**event, **changes}))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                list_replies(state)
        self.assertEqual(list_replies(self.root / "missing"), [])

    def test_policy_rejects_nonpositive_noninteger_limits(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ActivityPolicy(max_cards=value)


if __name__ == "__main__":
    unittest.main()
