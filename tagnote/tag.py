#!/usr/bin/env python3

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

from abc import ABCMeta, abstractmethod
from argparse import ArgumentParser, Namespace
from bisect import bisect_left
from collections import OrderedDict
from datetime import datetime
from itertools import zip_longest, chain
from json import load
from os import environ, scandir, stat_result
from pathlib import Path
from re import compile, error as re_error
from shutil import which, copy2, get_terminal_size
from subprocess import run as subprocess_run, CalledProcessError
from sys import stdout, stderr, argv, exit
from traceback import print_exc
from typing import (
    Sequence, Iterator, Iterable, Optional, Any, TextIO, Pattern, Type, Tuple
)


VERSION = "3.0.0"


class Config:
    PROPERTIES = dict(
        notes_directory=dict(
            default=Path("notes"),
            constructor=lambda p: Path(Path.home(), p),
            check=lambda v: v.is_dir(),
            check_string="must be an existing directory"
        ),
        editor=dict(
            default=[environ.get("EDITOR") or "vim"],
            constructor=lambda v: [v] if isinstance(v, str) else v,
            check=lambda v: isinstance(v, Sequence) and which(v[0]),
            check_string="must be a valid command"
        ),
        rsync=dict(
            default=["rsync"],
            constructor=lambda v: [v] if isinstance(v, str) else v,
            check=lambda v: isinstance(v, Sequence),
            check_string="must be a command"
        ),
        utc=dict(
            default=False,
            check=lambda v: isinstance(v, bool),
            check_string="must be either true or false"
        )
    )

    def __init__(self, file: Optional[TextIO]=None) -> None:
        self.notes_directory = None  # type: Optional[Path]
        self.editor = None  # type: Optional[Sequence[str]]
        self.rsync = None  # type: Optional[Sequence[str]]
        self.utc = None  # type: Optional[bool]

        config_file = {}
        if file:
            config_file = load(file)
        for name, spec in self.PROPERTIES.items():
            default = spec.get("default")
            constructor = spec.get("constructor")
            check = spec.get("check")
            check_string = spec.get("check_string")
            config_file_value = config_file.get(name)

            if config_file_value is None and default is None:
                raise TagError(
                    "Required property: '{}'".format(name),
                    TagError.EXIT_CONFIG_REQUIRED_PROPERTY
                )

            if constructor and default is not None:
                default = constructor(default)

            if constructor and config_file_value is not None:
                try:
                    config_file_value = constructor(config_file_value)
                except (
                        TypeError, ValueError, LookupError, AttributeError
                        ) as e:
                    raise TagError(
                        "Could not construct property '{}'"
                        " from '{}'.".format(name, config_file_value),
                        TagError.EXIT_CONFIG_CONSTRUCTOR_FAILED
                    ) from e

            if check is not None and config_file_value is not None \
                    and not check(config_file_value):
                if not check_string or not check_string.strip():
                    check_string = "has an invalid value"
                raise TagError(
                    "'{}' {}.".format(name, check_string),
                    TagError.EXIT_CONFIG_CHECK_FAILED
                )

            setattr(self, name, config_file_value or default)


class TagError(Exception):
    EXIT_USAGE = 2

    EXIT_CONFIG_REQUIRED_PROPERTY = 11

    EXIT_CONFIG_CONSTRUCTOR_FAILED = 12

    EXIT_CONFIG_CHECK_FAILED = 13

    EXIT_DIRECTORY_NOT_FOUND = 21

    EXIT_UNSUPPORTED_OPERATION = 22

    EXIT_NOTE_NOT_EXISTS = 23

    EXIT_NOTE_EXISTS = 24

    EXIT_LABEL_NOT_EXISTS = 25

    EXIT_BAD_NAME = 26

    EXIT_BAD_RANGE = 27

    EXIT_BAD_REGEX = 28

    EXIT_EDITOR_FAILED = 29

    EXIT_EXISTING_MAPPINGS = 30

    EXIT_IMPORT_FILE_NOT_EXISTS = 31

    EXIT_BAD_PERMISSIONS = 32

    EXIT_BAD_ORDER = 33

    def __init__(self, message: str, exit_status: int) -> None:
        super().__init__(message)
        self.exit_status = exit_status


class Tag:
    def __init__(self, name: str, directory: Path) -> None:
        if not self.match(name):
            raise ValueError(
                "'{}' is not a valid {}".format(name, self.tag_type())
            )
        self.name = name
        self.directory = directory

    def __hash__(self):
        return hash((self.name, self.directory))

    def __eq__(self, other):
        return self.name == other.name and self.directory == other.directory

    def __ne__(self, other):
        return self.name != other.name or self.directory != other.directory

    def __str__(self):
        return str(Path(self.directory, self.name))

    def __repr__(self):
        return "{}('{}')".format(type(self).__name__, self.__str__())

    def __lt__(self, other):
        return self.name < other.name

    def __le__(self, other):
        return self.name <= other.name

    def __gt__(self, other):
        return self.name > other.name

    def __ge__(self, other):
        return self.name >= other.name

    @classmethod
    def match(cls, name: str) -> bool:
        return bool(cls.pattern().match(name))

    def path(self) -> Path:
        return Path(self.directory, self.name)

    def exists(self) -> bool:
        return self.path().is_file()

    def check_exists(self) -> bool:
        if not self.exists():
            raise self.not_exists_error()
        return True

    def categories(self) -> Iterator["Tag"]:
        matches = (
            tag for tag in all_tags(self.directory)
            if self in tag.members()
        )
        return matches

    @classmethod
    @abstractmethod
    def tag_type(cls) -> str:
        pass

    @classmethod
    @abstractmethod
    def pattern(cls) -> Pattern:
        pass

    @abstractmethod
    def create(self) -> bool:
        pass

    @abstractmethod
    def not_exists_error(self) -> TagError:
        pass

    @abstractmethod
    def add_member(self, tag: "Tag") -> bool:
        pass

    @abstractmethod
    def remove_member(self, tag: "Tag") -> bool:
        pass

    @abstractmethod
    def members(self) -> Iterator["Tag"]:
        pass

    @abstractmethod
    def search_text(self, pattern: Pattern) -> bool:
        pass


class Note(Tag):
    TAG_TYPE = "note"

    PATTERN = compile("^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}.txt$")

    @classmethod
    def tag_type(cls) -> str:
        return cls.TAG_TYPE

    @classmethod
    def pattern(cls) -> Pattern:
        return cls.PATTERN

    def create(self) -> bool:
        self.check_exists()
        return False

    def not_exists_error(self) -> TagError:
        return TagError(
            "Note '{}' does not exist".format(self.path()),
            TagError.EXIT_NOTE_NOT_EXISTS
        )

    def add_member(self, tag: "Tag") -> bool:
        raise TagError(
            "Cannot add members to a note",
            TagError.EXIT_UNSUPPORTED_OPERATION
        )

    def remove_member(self, tag: "Tag") -> bool:
        raise TagError(
            "Cannot remove members from a note",
            TagError.EXIT_UNSUPPORTED_OPERATION
        )

    def members(self) -> Iterator["Tag"]:
        self.check_exists()
        return iter([])

    def search_text(self, pattern: Pattern) -> bool:
        self.check_exists()
        with self.path().open() as f:
            for line in f:
                if pattern.search(line):
                    return True
        return False


class Label(Tag):
    TAG_TYPE = "label"

    PATTERN = compile("^[\w-]+$")

    @classmethod
    def tag_type(cls) -> str:
        return cls.TAG_TYPE

    @classmethod
    def pattern(cls) -> Pattern:
        return cls.PATTERN

    def create(self) -> bool:
        try:
            self.path().touch(exist_ok=False)
        except FileExistsError:
            return False
        return True

    def not_exists_error(self) -> TagError:
        return TagError(
            "Label '{}' does not exist".format(self.path()),
            TagError.EXIT_LABEL_NOT_EXISTS
        )

    def write_members(self, members: Iterable["Tag"]) -> None:
        with self.path().open("w") as f:
            f.writelines(member.name + "\n" for member in members)

    def add_member(self, tag: "Tag") -> bool:
        members = list(set(self.members()))
        members.sort()
        add_index = bisect_left(members, tag)
        if add_index >= len(members) or members[add_index] != tag:
            changed = True
            members.insert(add_index, tag)
        else:
            changed = False
        self.write_members(members)
        return changed

    def remove_member(self, tag: "Tag") -> bool:
        members = list(set(self.members()))
        members.sort()
        try:
            members.remove(tag)
            changed = True
        except ValueError:
            changed = False
        self.write_members(members)
        return changed

    def members(self) -> Iterator["Tag"]:
        self.check_exists()
        with self.path().open() as f:
            members = f.readlines()
        return (
            tag_of(member.strip(), self.directory)
            for member in members
            if tag_of(member.strip(), self.directory).check_exists()
        )

    def search_text(self, pattern: Pattern) -> bool:
        return False


TAG_TYPES = (Note, Label)


def tag_of(value: str, directory: Path) -> Tag:
    for type_ in TAG_TYPES:
        try:
            return type_(value, directory)
        except ValueError:
            continue
    raise TagError(
        "No tag type for '{}'".format(value),
        TagError.EXIT_BAD_NAME
    )


def tag_types(tag_type: Optional[Type[Tag]]=None) -> Tuple[Type[Tag]]:
    if tag_type is not None:
        if tag_type not in TAG_TYPES:
            raise TypeError("Not a valid tag type: '{}'".format(tag_type))
        types = (tag_type,)
    else:
        types = TAG_TYPES
    return types


def valid_tag_instance(
        instance: Tag, tag_type: Optional[Type[Tag]]=None
        ) -> bool:
    types = tag_types(tag_type)
    for type_ in types:
        if isinstance(instance, type_):
            return True
    return False


def valid_tag_name(
        name: str, tag_type: Optional[Type[Tag]]=None
        ) -> bool:
    types = tag_types(tag_type)
    for type_ in types:
        if type_.match(name):
            return True
    return False


def all_tags(
        directory: Path, tag_type: Optional[Type[Tag]]=None
        ) -> Iterator[Tag]:
    try:
        directories = scandir(str(directory))
    except FileNotFoundError as e:
        raise TagError(
            "Directory not found: '{}'".format(directory),
            TagError.EXIT_DIRECTORY_NOT_FOUND
        ) from e
    all_files = (
        entry.name for entry in directories if entry.is_file()
    )
    tags = (
        tag_of(file, directory) for file in all_files
        if valid_tag_name(file, tag_type)
    )
    return tags


class AllTagsFrom(Iterator):
    def __init__(
            self, category: Tag, tag_type: Optional[Type[Tag]]=None
            ) -> None:
        self.category = category
        self.tag_type = tag_type
        self.visited = set()
        self.remaining = OrderedDict()
        self.remaining.setdefault(category)

    def __next__(self):
        while self.remaining:
            # BFS
            current_tag, __ = self.remaining.popitem(last=False)
            self.visited.add(current_tag)
            for member in current_tag.members():
                if member not in self.visited:
                    self.remaining.setdefault(member)
            if valid_tag_instance(current_tag, self.tag_type):
                return current_tag
        raise StopIteration


def all_unique_notes(roots: Iterable[Tag]) -> Iterator[Note]:
    notes = set()
    for root in roots:
        for note in AllTagsFrom(root, Note):
            notes.add(note)
    return iter(notes)


def left_pad(text: str, length: int, padding: str) -> str:
    if len(padding) != 1:
        raise ValueError(
            "Only single-character padding supported: '{}'".format(padding)
        )
    if len(text) > length:
        raise ValueError(
            "Text more than {} characters long: '{}'".format(length, text)
        )
    number_of_pads = length - len(text)
    return (number_of_pads * padding) + text


def format_timestamp(timestamp: datetime) -> str:
    name = (
        "{year}-{month}-{day}_{hour}-{minute}-{second}".format(
            year=left_pad(str(timestamp.year), 4, "0"),
            month=left_pad(str(timestamp.month), 2, "0"),
            day=left_pad(str(timestamp.day), 2, "0"),
            hour=left_pad(str(timestamp.hour), 2, "0"),
            minute=left_pad(str(timestamp.minute), 2, "0"),
            second=left_pad(str(timestamp.second), 2, "0")
        )
    )
    return name


class Formatter(metaclass=ABCMeta):
    PADDING = 20

    @classmethod
    @abstractmethod
    def format(cls, items: Iterable[str]) -> None:
        pass


class MultipleColumn(Formatter):
    @classmethod
    def format(cls, items: Iterable[str]) -> None:
        all_items = tuple(items)
        if not all_items:
            return
        column_width = max(len(item) for item in all_items) + cls.PADDING
        term_width = get_terminal_size().columns
        columns_per_line = term_width // column_width or 1
        column_height = len(all_items) // columns_per_line + 1
        tags_in_columns = [
            all_items[i: i + column_height]
            for i in range(0, len(all_items), column_height)
        ]

        if term_width >= column_width:
            placeholder = "{{:<{}}}".format(column_width)
        else:
            placeholder = "{}"

        for row in zip_longest(*tags_in_columns, fillvalue=""):
            format_string = "".join(
                [placeholder] * len(row)
            )
            print(format_string.format(*row).rstrip(), file=stdout)


class SingleColumn(Formatter):
    @classmethod
    def format(cls, items: Iterable[str]) -> None:
        for item in items:
            print(item, file=stdout)


class Command(metaclass=ABCMeta):
    @classmethod
    @abstractmethod
    def name(cls) -> str:
        pass

    @classmethod
    @abstractmethod
    def description(cls) -> str:
        pass

    @classmethod
    @abstractmethod
    def arguments(cls, parser: ArgumentParser) -> None:
        pass

    @classmethod
    def default_sort_order(cls) -> Optional[bool]:
        return True

    @classmethod
    @abstractmethod
    def run(cls, arguments: Namespace, config: Config) -> Iterator[Tag]:
        pass

    @classmethod
    def format(
            cls,
            tags: Iterable[Tag],
            arguments: Namespace,
            config: Config,
            formatter: Type[Formatter]
            ) -> None:
        formatter.format(t.name for t in tags)


class Add(Command):
    NAME = "add"

    DESCRIPTION = "Create a tag and optionally add categories to it."

    @classmethod
    def name(cls) -> str:
        return cls.NAME

    @classmethod
    def description(cls) -> str:
        return cls.DESCRIPTION

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> None:
        parser.add_argument("tag", help="The tag to add")
        parser.add_argument(
            "categories", nargs="*", help="The categories to add to the tag"
        )

    @classmethod
    def run(cls, arguments: Namespace, config: Config) -> Iterator[Tag]:
        tag = tag_of(arguments.tag, config.notes_directory)
        to_add = OrderedDict()  # type: OrderedDict[Tag, Any]
        for category_name in OrderedDict.fromkeys(arguments.categories).keys():
            category = tag_of(category_name, config.notes_directory)
            if not isinstance(category, Label):
                raise TagError(
                    "Categories must be labels: '{}'".format(category_name),
                    TagError.EXIT_UNSUPPORTED_OPERATION
                )
            to_add.setdefault(category)
        new_tags = []
        for new_tag in chain([tag], to_add.keys()):
            changed = new_tag.create()
            if changed:
                new_tags.append(new_tag)
        for category in to_add.keys():
            category.add_member(tag)
        return iter(new_tags)


class Members(Command):
    NAME = "members"

    DESCRIPTION = "List immediate members of a category."

    @classmethod
    def name(cls) -> str:
        return cls.NAME

    @classmethod
    def description(cls) -> str:
        return cls.DESCRIPTION

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> None:
        parser.add_argument(
            "category",
            help="The category to list, else all tags without a category",
            nargs="?"
        )

    @classmethod
    def run(cls, arguments: Namespace, config: Config) -> Iterator[Tag]:
        if arguments.category:
            category = tag_of(arguments.category, config.notes_directory)
            return category.members()
        else:
            remaining = set(all_tags(config.notes_directory))
            in_labels = set()
            for label in all_tags(config.notes_directory, Label):
                in_labels.update(label.members())
            remaining -= in_labels
            return iter(remaining)


class Categories(Command):
    NAME = "categories"

    DESCRIPTION = "List immediate categories a tag belongs to."

    @classmethod
    def name(cls) -> str:
        return cls.NAME

    @classmethod
    def description(cls) -> str:
        return cls.DESCRIPTION

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> None:
        parser.add_argument("tag", help="The tag to list categories for")

    @classmethod
    def run(cls, arguments: Namespace, config: Config) -> Iterator[Tag]:
        tag = tag_of(arguments.tag, config.notes_directory)
        return tag.categories()


class Show(Command):
    NAME = "show"

    DESCRIPTION = "Combine all notes into a single document."

    HEADER = "{}\n---\n"

    FOOTER = "\n***\n"

    @classmethod
    def name(cls) -> str:
        return cls.NAME

    @classmethod
    def description(cls) -> str:
        return cls.DESCRIPTION

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> None:
        parser.add_argument(
            "tags", nargs="*", help="The tags to combine, else all"
        )

    @classmethod
    def default_sort_order(cls) -> Optional[bool]:
        return False

    @classmethod
    def run(cls, arguments: Namespace, config: Config) -> Iterator[Tag]:
        if arguments.tags:
            return all_unique_notes(
                tag_of(name, config.notes_directory)
                for name in set(arguments.tags)
            )
        else:
            return all_tags(config.notes_directory, Note)

    @classmethod
    def print(cls, member: Tag) -> None:
        with member.path().open() as f:
            print(cls.HEADER.format(member.name), end="")
            for line in f:
                print(line, end="")
            print(cls.FOOTER, end="")

    @classmethod
    def format(
            cls,
            tags: Iterable[Tag],
            arguments: Namespace,
            config: Config,
            formatter: Type[Formatter]
            ) -> None:
        for tag in tags:
            cls.print(tag)


class Last(Command):
    NAME = "last"

    DESCRIPTION = "Open the last note in a text editor."

    @classmethod
    def name(cls) -> str:
        return cls.NAME

    @classmethod
    def description(cls) -> str:
        return cls.DESCRIPTION

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> None:
        parser.add_argument(
            "tags", nargs="*", help="The tags to search, else all"
        )

    @classmethod
    def run(cls, arguments: Namespace, config: Config) -> Iterator[Tag]:
        if arguments.tags:
            tags = all_unique_notes(
                tag_of(name, config.notes_directory)
                for name in set(arguments.tags)
            )
        else:
            tags = all_tags(config.notes_directory, Note)
        last = None
        for tag in tags:
            if last is None or tag > last:
                last = tag
        if last:
            return iter([last])
        else:
            return iter([])

    @classmethod
    def format(
            cls,
            tags: Iterable[Tag],
            arguments: Namespace,
            config: Config,
            formatter: Type[Formatter]
            ) -> None:
        for tag in tags:
            command = [*config.editor, str(tag.path())]
            try:
                subprocess_run(command, check=True)
            except (CalledProcessError, FileNotFoundError) as e:
                raise TagError(
                    "Editor command {} failed.".format(command),
                    TagError.EXIT_EDITOR_FAILED
                ) from e


class Remove(Command):
    NAME = "remove"

    DESCRIPTION = "Remove a tag from categories or from everything."

    @classmethod
    def name(cls) -> str:
        return cls.NAME

    @classmethod
    def description(cls) -> str:
        return cls.DESCRIPTION

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> None:
        parser.add_argument("tag", help="The tag to remove")
        parser.add_argument(
            "categories",
            nargs="*",
            help="The categories to remove from, else from the database"
        )

    @classmethod
    def run(cls, arguments: Namespace, config: Config) -> Iterator[Tag]:
        removed_tags = []
        tag = tag_of(arguments.tag, config.notes_directory)
        if arguments.categories:
            to_remove = []
            for category_name in set(arguments.categories):
                category = tag_of(category_name, config.notes_directory)
                if not isinstance(category, Label):
                    raise TagError(
                        "Categories must be labels: '{}'".format(
                            category_name
                        ),
                        TagError.EXIT_UNSUPPORTED_OPERATION
                    )
                category.check_exists()
                to_remove.append(category)
            for category in to_remove:
                category.remove_member(tag)
        else:
            if tag.exists():
                if any(tag.members()) or any(tag.categories()):
                    raise TagError(
                        (
                            "Failed removing tag '{}'."
                            " Try removing all of its categories and members"
                            " first."
                        ).format(arguments.tag),
                        TagError.EXIT_EXISTING_MAPPINGS
                    )
                tag.path().unlink()
                removed_tags.append(tag)
        return iter(removed_tags)


class Import(Command):
    NAME = "import"

    DESCRIPTION = "Copy text files into the notes directory in proper format."

    @classmethod
    def name(cls) -> str:
        return cls.NAME

    @classmethod
    def description(cls) -> str:
        return cls.DESCRIPTION

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> None:
        parser.add_argument(
            "files",
            nargs="+",
            help="The text files to import",
            type=Path
        )

    @classmethod
    def stat(cls, path: Path) -> stat_result:
        try:
            stat = path.stat()
        except FileNotFoundError as e:
            raise TagError(
                "Could not find file: '{}'".format(path),
                TagError.EXIT_IMPORT_FILE_NOT_EXISTS
            ) from e
        except PermissionError as e:
            raise TagError(
                "Could not read file: '{}'".format(path),
                TagError.EXIT_BAD_PERMISSIONS
            ) from e
        return stat

    @classmethod
    def filename(cls, timestamp: datetime) -> str:
        return "{}.txt".format(format_timestamp(timestamp))

    @classmethod
    def run(cls, arguments: Namespace, config: Config) -> Iterator[Tag]:
        destinations = []
        for path in arguments.files:
            stat = cls.stat(path)
            if config.utc:
                timestamp = datetime.utcfromtimestamp(stat.st_mtime)
            else:
                timestamp = datetime.fromtimestamp(stat.st_mtime)
            note = tag_of(cls.filename(timestamp), config.notes_directory)
            if note.exists():
                raise TagError(
                    "Note already exists: '{}'".format(note),
                    TagError.EXIT_NOTE_EXISTS
                )
            try:
                copy2(str(path), str(note))
            except PermissionError as e:
                raise TagError(
                    "Could not write to file: '{}'".format(note),
                    TagError.EXIT_BAD_PERMISSIONS
                ) from e
            destinations.append(note)
        return iter(destinations)


def check_rsync(rsync_command: Sequence[str]) -> None:
    if which(rsync_command[0]) is None:
        raise TagError(
            "Could not find rsync command: {}".format(rsync_command),
            TagError.EXIT_UNSUPPORTED_OPERATION
        )


class Pull(Command):
    NAME = "pull"

    DESCRIPTION = "Download notes using rsync."

    @classmethod
    def name(cls) -> str:
        return cls.NAME

    @classmethod
    def description(cls) -> str:
        return cls.DESCRIPTION

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> None:
        parser.add_argument("source_directory", help="The source directory")

    @classmethod
    def run(cls, arguments: Namespace, config: Config) -> Iterator[Tag]:
        check_rsync(config.rsync)
        if config.utc:
            now = datetime.utcnow()
        else:
            now = datetime.now()
        subprocess_run(
            [
                *config.rsync,
                "-rtbv",
                "--suffix=.{}.bak".format(format_timestamp(now)),
                "{}/".format(arguments.source_directory),
                "{}/".format(config.notes_directory)
            ]
        )
        return iter([])


class Push(Command):
    NAME = "push"

    DESCRIPTION = "Upload notes using rsync."

    @classmethod
    def name(cls) -> str:
        return cls.NAME

    @classmethod
    def description(cls) -> str:
        return cls.DESCRIPTION

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> None:
        parser.add_argument("dest_directory", help="The destination directory")

    @classmethod
    def run(cls, arguments: Namespace, config: Config) -> Iterator[Tag]:
        check_rsync(config.rsync)
        if config.utc:
            now = datetime.utcnow()
        else:
            now = datetime.now()
        subprocess_run(
            [
                *config.rsync,
                "-rtbv",
                "--suffix={}.bak".format(format_timestamp(now)),
                "{}/".format(config.notes_directory),
                "{}/".format(arguments.dest_directory)
            ]
        )
        return iter([])


COMMANDS = (
    Add,
    Members,
    Categories,
    Show,
    Last,
    Remove,
    Import,
    Pull,
    Push
)


def argument_parser() -> ArgumentParser:
    parser = ArgumentParser()
    parser.add_argument(
        "-c", "--config",
        help="The configuration file to use",
        default=Path(Path.home(), ".tag.config.json"),
        type=Path
    )
    parser.add_argument(
        "-d", "--debug",
        help="Print more verbose error messages",
        action="store_true"
    )
    parser.add_argument(
        "-t", "--tag-name",
        help="A regex for tag names to filter on"
    )
    parser.add_argument(
        "-s", "--search",
        help="A regex in the notes to filter on"
    )
    parser.add_argument(
        "-o", "--order",
        help="Sort notes [a]scending, [d]escending, or [n]one"
    )
    parser.add_argument(
        "-r", "--range",
        help="A slice of notes to show"
    )
    parser.add_argument(
        "-sc", "--single-column",
        help="Print results in a single column",
        action="store_true"
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=VERSION
    )
    action = parser.add_subparsers(metavar="command")

    for command in COMMANDS:
        command_parser = action.add_parser(
            command.name(), help=command.description()
        )
        command.arguments(command_parser)
        command_parser.set_defaults(command=command)

    return parser


def read_config_file(path: Path) -> Config:
    try:
        with path.open() as file:
            config = Config(file)
    except FileNotFoundError:
        config = Config()
    return config


def compile_regex(pattern: str) -> Pattern:
    try:
        regex = compile(pattern)
    except re_error as e:
        raise TagError(
            "Bad regex: '{}'".format(pattern), TagError.EXIT_BAD_REGEX
        ) from e
    return regex


def parse_slice(text: str) -> slice:
    if not text.strip():
        raise ValueError("Empty slice")
    components = text.split(":")
    if len(components) > 3 or len(components) < 1:
        raise ValueError("Bad slice: '{}'".format(text))
    if components[0]:
        start = int(components[0])
    else:
        start = 0
    if len(components) == 1:
        return slice(start, start + 1)
    if components[1]:
        end = int(components[1])
    else:
        end = -1
    if len(components) == 2:
        return slice(start, end)
    if components[2]:
        step = int(components[2])
    else:
        step = 1
    return slice(start, end, step)


def run_filters(results: Iterable[Tag], args: Namespace) -> Iterator[Tag]:
    if args.search:
        search_pattern = compile_regex(args.search)

        def search(t: Tag) -> bool:
            return t.search_text(search_pattern)
    else:
        def search(__) -> bool:
            return True
    if args.tag_name:
        tag_name_pattern = compile_regex(args.tag_name)

        def tag_name(t: Tag) -> bool:
            return bool(tag_name_pattern.search(t.name))
    else:
        def tag_name(__) -> bool:
            return True

    results = (t for t in results if search(t) and tag_name(t))
    return results


def parse_order(value: str) -> Optional[bool]:
    if value:
        if "ascending".startswith(value):
            return True
        elif "descending".startswith(value):
            return False
        elif "none".startswith(value):
            return None
    raise TagError(
        "Bad order: '{}'".format(value),
        TagError.EXIT_BAD_ORDER
    )


def run_order_range(
        results: Iterable[Tag], args: Namespace, command: Command
        ) -> Iterator[Tag]:
    order = command.default_sort_order()
    if args.order:
        order = parse_order(args.order)
    if order is not None or args.range:
        results_list = list(results)
        if order is not None:
            results_list.sort(reverse=not order)
        if args.range:
            result_slice = parse_slice(args.range)
            results_list = results_list[result_slice]
        results = iter(results_list)
    return results


def run(args: Sequence[str]) -> None:
    parser = argument_parser()
    args = parser.parse_args(args)

    try:
        command = args.command  # type: Command
    except AttributeError:
        parser.print_help(stderr)
        exit(TagError.EXIT_USAGE)
        return

    try:
        config = read_config_file(Path(args.config))
        results = command.run(args, config)  # type: Iterator[Tag]
        results = run_filters(results, args)
        results = run_order_range(results, args, command)
        if args.single_column:
            formatter = SingleColumn
        else:
            formatter = MultipleColumn
        command.format(results, args, config, formatter)
    except TagError as e:
        if args.debug:
            print_exc()
        else:
            if str(e):
                print(e, file=stderr)
        exit(e.exit_status)


def main():
    run(argv[1:])


if __name__ == "__main__":
    main()
