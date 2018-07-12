"""
Copyright 2018 Michael Ren

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

from unittest import TestCase, main
from unittest.mock import patch
from io import StringIO
from tempfile import TemporaryDirectory
from datetime import datetime
from pathlib import Path
from re import compile as re_compile
from typing import Sequence, TextIO
from os import terminal_size

from tagnote.tag import (
    Note, Label, tag_of, TagError, Config, all_tags, AllTagsFrom,
    all_unique_notes, left_pad, format_timestamp, MultipleColumn, SingleColumn,
    tag_types, valid_tag_instance, valid_tag_name
)


class TestConfig(TestCase):
    def test_defaults(self):
        config = Config()

        self.assertIsInstance(config.notes_directory, Path)
        self.assertEqual(config.notes_directory.name, "notes")

        self.assertIsInstance(config.editor, Sequence)
        for argument in config.editor:
            self.assertIsInstance(argument, str)

        self.assertIsInstance(config.rsync, Sequence)
        for argument in config.rsync:
            self.assertIsInstance(argument, str)
        self.assertEqual(config.rsync[0], "rsync")

        self.assertEqual(config.utc, False)

    def test_required_property(self):
        p1 = dict(notes_directory=dict())
        with patch.object(Config, "PROPERTIES", new=p1):
            with self.assertRaises(TagError):
                Config()

    def test_constructor(self):
        p1 = dict(notes_directory=dict(constructor=int, default="-1"))
        with patch.object(Config, "PROPERTIES", new=p1):
            c1 = Config()
            self.assertEqual(c1.notes_directory, -1)

            override = StringIO('{"notes_directory": "2"}')  # type: TextIO
            c2 = Config(override)
            self.assertEqual(c2.notes_directory, 2)

        p2 = dict(notes_directory=dict(constructor=int))
        with patch.object(Config, "PROPERTIES", new=p2):
            bad = StringIO('{"notes_directory": "foo"}')  # type: TextIO
            with self.assertRaises(TagError):
                Config(bad)
            good = StringIO('{"notes_directory": "3"}')  # type: TextIO
            c3 = Config(good)
            self.assertEqual(c3.notes_directory, 3)

    def test_check_value(self):
        p1 = dict(notes_directory=dict(check=bool, check_string="bar bar bar"))
        with patch.object(Config, "PROPERTIES", new=p1):
            f1 = StringIO('{"notes_directory": ""}')  # type: TextIO
            with self.assertRaises(TagError) as e1:
                Config(f1)
            self.assertTrue(str(e1.exception).endswith("bar bar bar."))

            f2 = StringIO('{"notes_directory": "hi"}')  # type: TextIO
            c1 = Config(f2)
            self.assertEqual(c1.notes_directory, "hi")


class TestTag(TestCase):
    def test_tag_names(self):
        with self.assertRaises(ValueError):
            Note("2018-05-05_01-01-01", Path())
        self.assertEqual(
            Note("2018-10-10_10-10-10.txt", Path()).name,
            "2018-10-10_10-10-10.txt"
        )
        with self.assertRaises(ValueError):
            Label("todo.txt", Path())
        self.assertEqual(
            Label("todo", Path()).name,
            "todo"
        )

    def test_tag_operators(self):
        self.assertEqual(
            len({Label("todo", Path())}), 1
        )
        self.assertEqual(
            Label("todo", Path()), Label("todo", Path())
        )
        self.assertNotEqual(
            Label("todo", Path("/")), Label("todo", Path("/tmp"))
        )
        self.assertNotEqual(
            Label("todo", Path()), Label("tod", Path())
        )
        self.assertLess(Label("a", Path()), Label("b", Path()))
        self.assertLessEqual(Label("a", Path()), Label("a", Path()))
        self.assertGreater(Label("b", Path()), Label("a", Path()))
        self.assertGreaterEqual(Label("b", Path()), Label("b", Path()))

    def test_create_and_search_text(self):
        with TemporaryDirectory() as tmp_dir:
            tmp_dir = Path(tmp_dir)

            note = Note("2018-10-10_10-10-10.txt", tmp_dir)
            with self.assertRaises(TagError):
                note.create()
            with self.assertRaises(TagError):
                p0 = re_compile("baz")
                note.search_text(p0)

            with note.path().open("w") as f:
                f.writelines(
                    ["The quick brown fox jumped\n", "over the lazy dog\n"]
                )
            create = note.create()
            self.assertFalse(create)

            p1 = re_compile("own")
            m1 = note.search_text(p1)
            self.assertTrue(m1)

            p2 = re_compile("bar")
            m2 = note.search_text(p2)
            self.assertFalse(m2)

            p3 = re_compile("foo")
            self.assertFalse(Label("todo", tmp_dir).search_text(p3))

    def test_member_category(self):
        with TemporaryDirectory() as tmp_dir:
            tmp_dir = Path(tmp_dir)

            note = Note("2018-10-10_10-10-10.txt", tmp_dir)
            with self.assertRaises(TagError):
                note.add_member(Label("todo", tmp_dir))
            with self.assertRaises(TagError):
                note.remove_member(Label("todo", tmp_dir))
            with self.assertRaises(TagError):
                note.members()
            note.path().touch()
            self.assertEqual(len(list(note.members())), 0)

            root1, root2 = Label("todo", tmp_dir), Label("1", tmp_dir)
            child1, child2, child3 = (
                Label("todo1", tmp_dir),
                Label("todo2", tmp_dir),
                Label("todo3", tmp_dir)
            )
            r1c, r2c = root1.create(), root2.create()
            c1c, c2c, c3c = child1.create(), child2.create(), child3.create()
            self.assertEqual((r1c, r2c), (True, True))
            self.assertEqual((c1c, c2c, c3c), (True, True, True))

            r1c3a = root1.add_member(child3)
            r1c1a = root1.add_member(child1)
            r1c2a = root1.add_member(child2)
            r2c1a = root2.add_member(child1)
            r2c2a = root2.add_member(child2)
            self.assertEqual(
                (r1c1a, r1c2a, r1c3a, r2c1a, r2c2a),
                (True, True, True, True, True)
            )
            self.assertEqual(set(root1.members()), {child1, child2, child3})
            self.assertEqual(set(root2.members()), {child1, child2})
            self.assertEqual(set(child1.categories()), {root1, root2})
            self.assertEqual(set(child2.categories()), {root1, root2})
            self.assertEqual(set(child3.categories()), {root1})

            r1c1a2 = root1.add_member(child1)
            r1c2a2 = root1.add_member(child2)
            r1c3a2 = root1.add_member(child3)
            self.assertEqual((r1c1a2, r1c2a2, r1c3a2), (False, False, False))

            r1c2d = root1.remove_member(child2)
            self.assertEqual(r1c2d, True)
            self.assertEqual(set(root1.members()), {child1, child3})
            self.assertEqual(set(child2.categories()), {root2})

            r1c2d2 = root1.remove_member(child2)
            self.assertEqual(r1c2d2, False)

            fake_child1 = Label("foo", tmp_dir)
            root1.add_member(fake_child1)
            with self.assertRaises(TagError):
                list(root1.members())

    # noinspection PyTypeChecker
    def test_static_tag_helpers(self):
        self.assertEqual(
            type(tag_of("todo", Path())), Label
        )
        self.assertEqual(
            type(tag_of("2018-10-10_10-10-10.txt", Path())), Note
        )
        with self.assertRaises(TagError):
            tag_of("todo.txt", Path())

        with self.assertRaises(TypeError):
            tag_types(1)
        with self.assertRaises(TypeError):
            valid_tag_name("foo", Path)
        with self.assertRaises(TypeError):
            valid_tag_instance(Label("foo", Path()), Path)
        self.assertTrue(valid_tag_name("foo", Label))
        self.assertTrue(valid_tag_instance(Label("foo", Path())), Label)
        self.assertTrue(valid_tag_name("2018-10-10_10-10-10.txt", Note))
        self.assertTrue(
            valid_tag_instance(Note("2018-10-10_10-10-10.txt", Path()), Note)
        )
        self.assertTrue(valid_tag_name("bar"))
        self.assertTrue(valid_tag_instance(Label("bar", Path())))

    def test_all_tags(self):
        with self.assertRaises(TagError):
            with TemporaryDirectory() as tmp_dir:
                tmp_dir = Path(tmp_dir)
            all_tags(tmp_dir)
        with TemporaryDirectory() as tmp_dir:
            tmp_dir = Path(tmp_dir)
            note1 = Note("2018-10-10_10-10-10.txt", tmp_dir)
            note2 = Note("2018-10-10_10-10-11.txt", tmp_dir)
            note3 = Note("2018-10-10_10-10-12.txt", tmp_dir)
            label1 = Label("todo1", tmp_dir)
            label2 = Label("todo2", tmp_dir)
            label3 = Label("todo3", tmp_dir)
            extra1 = Path(tmp_dir, "todo1.2018-10-10_10-10-10.bak")
            extra2 = Path(
                tmp_dir, "2018-10-10_10-10-10.txt.2018-10-10_10-10-11.bak"
            )
            extra3 = Path(tmp_dir, "todo3.2018-10-10_10-10-12.bak")
            note1.path().touch(), note2.path().touch(), note3.path().touch()
            label1.path().touch(), label2.path().touch(), label3.path().touch()
            extra1.touch(), extra2.touch(), extra3.touch()

            all_ = list(all_tags(tmp_dir))
            all_.sort()
            self.assertEqual(
                [note1, note2, note3, label1, label2, label3],
                all_
            )

            notes = list(all_tags(tmp_dir, Note))
            notes.sort()
            self.assertEqual([note1, note2, note3], notes)

            labels = list(all_tags(tmp_dir, Label))
            labels.sort()
            self.assertEqual([label1, label2, label3], labels)

    def test_all_tags_from(self):
        """
        Notes nested under labels should still be returned when only returning
        notes, and throw in a multi-node loop for good measure.
        """
        with TemporaryDirectory() as tmp_dir:
            tmp_dir = Path(tmp_dir)

            note1 = Note("2018-10-10_09-09-09.txt", tmp_dir)
            note1.path().touch()
            self.assertEqual(list(AllTagsFrom(note1)), [note1])

            label1 = Label("foo", tmp_dir)
            label1.create()
            self.assertEqual(list(AllTagsFrom(label1)), [label1])

            node_1_1 = Label("all", tmp_dir)
            node_2_1 = Label("work", tmp_dir)
            node_2_2 = Label("play", tmp_dir)
            node_2_3 = Note("2018-10-10_10-10-10.txt", tmp_dir)
            node_3_1 = Note("2018-10-10_10-10-11.txt", tmp_dir)
            node_3_2 = Label("work2", tmp_dir)
            node_3_3 = Label("work3", tmp_dir)
            node_4_1 = Note("2018-10-10_10-10-12.txt", tmp_dir)
            node_loop_1 = Label("loop1", tmp_dir)
            node_loop_2 = Label("loop2", tmp_dir)
            node_loop_3 = Label("loop3", tmp_dir)
            node_1_1.create()
            node_2_1.create(), node_2_2.create(), node_2_3.path().touch()
            node_3_1.path().touch(), node_3_2.create(), node_3_3.create()
            node_4_1.path().touch()
            node_loop_1.create(), node_loop_2.create(), node_loop_3.create()
            node_1_1.add_member(node_2_1)
            node_1_1.add_member(node_2_2)
            node_1_1.add_member(node_2_3)
            node_2_1.add_member(node_3_1)
            node_2_1.add_member(node_3_2)
            node_2_1.add_member(node_3_3)
            node_3_2.add_member(node_4_1)
            node_3_3.add_member(node_loop_1)
            node_loop_1.add_member(node_loop_2)
            node_loop_2.add_member(node_loop_3)
            node_loop_3.add_member(node_1_1)

            all_ = list(AllTagsFrom(node_1_1))
            all_.sort()
            self.assertEqual(
                all_,
                [
                    node_2_3, node_3_1, node_4_1,
                    node_1_1,
                    node_loop_1, node_loop_2, node_loop_3,
                    node_2_2,
                    node_2_1,
                    node_3_2, node_3_3,
                ]
            )

            notes = list(AllTagsFrom(node_1_1, Note))
            notes.sort()
            self.assertEqual(notes, [node_2_3, node_3_1, node_4_1])

            labels = list(AllTagsFrom(node_1_1, Label))
            labels.sort()
            self.assertEqual(
                labels,
                [
                    node_1_1,
                    node_loop_1, node_loop_2, node_loop_3,
                    node_2_2,
                    node_2_1,
                    node_3_2, node_3_3,
                ]
            )

    def test_all_unique_notes(self):
        with TemporaryDirectory() as tmp_dir:
            tmp_dir = Path(tmp_dir)
            label1 = Label("todo", tmp_dir)
            label2 = Label("work", tmp_dir)
            note1 = Note("2018-10-10_10-10-10.txt", tmp_dir)
            note2 = Note("2018-10-10_10-10-11.txt", tmp_dir)
            note3 = Note("2018-10-10_10-10-12.txt", tmp_dir)
            label1.create()
            label2.create()
            note1.path().touch()
            note2.path().touch()
            note3.path().touch()
            label1.add_member(note1)
            label1.add_member(note2)
            label1.add_member(note3)
            label2.add_member(note1)
            label2.add_member(note2)
            notes = list(
                all_unique_notes([label1, label2, note1, note2, note3])
            )
            notes.sort()
            self.assertEqual(
                notes,
                [note1, note2, note3]
            )


class TestFormat(TestCase):
    def test_left_pad(self):
        with self.assertRaises(ValueError):
            left_pad("", 0, "")
        with self.assertRaises(ValueError):
            left_pad("", 0, "hi")
        with self.assertRaises(ValueError):
            left_pad("something", 2, "h")
        self.assertEqual(
            left_pad("hi", 10, " "),
            "        hi"
        )

    def test_format_timestamp(self):
        t1 = datetime(
            year=2018,
            month=10,
            day=9,
            hour=8,
            minute=7,
            second=6
        )
        self.assertEqual(format_timestamp(t1), "2018-10-09_08-07-06")

    def test_multicolumn_common(self):
        def get_terminal_size():
            return terminal_size([10, 10])
        stdout = StringIO()
        with patch("tagnote.tag.get_terminal_size", new=get_terminal_size):
            with patch("tagnote.tag.stdout", new=stdout):
                with patch("tagnote.tag.MultipleColumn.PADDING", new=1):
                    MultipleColumn.format(["1", "10", "110", "111", "112"])
                    self.assertEqual(
                        stdout.getvalue(),
                        "1   111\n"
                        "10  112\n"
                        "110\n"
                    )

    def test_multicolumn_overflow(self):
        def get_terminal_size():
            return terminal_size([1, 1])
        stdout = StringIO()
        with patch("tagnote.tag.get_terminal_size", new=get_terminal_size):
            with patch("tagnote.tag.stdout", new=stdout):
                with patch("tagnote.tag.MultipleColumn.PADDING", new=1):
                    MultipleColumn.format(["hello", "1"])
                    self.assertEqual(
                        stdout.getvalue(),
                        "hello\n1\n"
                    )

    def test_single_column(self):
        stdout = StringIO()
        with patch("tagnote.tag.stdout", new=stdout):
            SingleColumn.format(["single", "column", "format"])
            self.assertEqual(
                stdout.getvalue(),
                "single\ncolumn\nformat\n"
            )


if __name__ == "__main__":
    main()
