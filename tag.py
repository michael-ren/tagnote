#!/usr/bin/env python3

from abc import ABCMeta, abstractmethod
from argparse import ArgumentParser, Namespace
from datetime import datetime
from enum import Enum
from json import load
from os import environ, scandir, stat_result
from pathlib import Path
from re import compile, error as re_error
from shutil import which, copy2
from sqlite3 import connect, Cursor, Row, IntegrityError, OperationalError
from subprocess import run as subprocess_run, CalledProcessError
from sys import stdout, stderr, argv, exit
from traceback import print_exc
from typing import (
    Sequence, Iterator, Optional, Callable, Mapping, Any, Tuple, TextIO, Set,
    Pattern
)


#TODO: implement editor support in vim:
#          - get file name, write file name, add file name to tags mentioned if any
#          - split this into :W to write with timestamp, :T to add file to any tags, :R to remove any tags, :L to list any tags
#          - journal, bookmarks, note-taking, research, todo list
#          - solve problem of naming, get it in first, decide how to label it later
#          - additions to a note should be linked as children of a note
#          - can either write whole note or quote parts of it or nothing at all, like email
#          - members start from present by default--we always start from the present and work backwards into the past through successive layers of interpretation


class Config:
    EXIT_REQUIRED_PROPERTY = 11

    EXIT_CONSTRUCTOR_FAILED = 12

    EXIT_CHECK_FAILED = 13

    PROPERTIES = dict(
        db_file=dict(
            default=Path("tags.sqlite"),
            constructor=Path,
            check=None
        ),
        notes_directory=dict(
            default=Path("."),
            constructor=Path,
            check=lambda v: v.is_dir(),
            check_string="must be an existing directory"
        ),
        editor=dict(
            default=[environ.get("EDITOR") or "vim"],
            constructor=lambda v: [v] if isinstance(v, str) else v,
            check=lambda v: isinstance(v, Sequence) and which(v[0]),
            check_string="must be a valid command"
        )
    )

    def __init__(self, file: Optional[TextIO]=None) -> None:
        self.db_file = None
        self.notes_directory = None
        self.editor = None

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
                    self.EXIT_REQUIRED_PROPERTY
                )

            if constructor and config_file_value is not None:
                try:
                    config_file_value = constructor(config_file_value)
                except (
                        TypeError, ValueError, LookupError, AttributeError
                        ) as e:
                    raise TagError(
                        "Could not construct property '{}'"
                        " from '{}'.".format(name, config_file_value),
                        self.EXIT_CONSTRUCTOR_FAILED
                    ) from e

            if check is not None and config_file_value is not None \
                    and not check(config_file_value):
                if not check_string or not check_string.strip():
                    check_string = "has an invalid value"
                raise TagError(
                    "'{}' {}.".format(name, check_string),
                    self.EXIT_CHECK_FAILED
                )

            setattr(self, name, config_file_value or default)


class TagType(Enum):
    NOTE = 1

    LABEL = 2


class Tag:
    TYPES = {
        TagType.NOTE: compile("^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}.txt$"),
        TagType.LABEL: compile("^[\w-]+$")
    }

    @classmethod
    def of(cls, value: str) -> TagType:
        for type_, pattern in cls.TYPES.items():
            if pattern.match(value):
                return type_
        raise ValueError("No tag type found for '{}'".format(value))


class TagError(Exception):
    def __init__(self, message: str, exit_status: int) -> None:
        super().__init__(message)
        self.exit_status = exit_status


def insert_new_key(mapping: dict, key: Any, value: Any) -> None:
    if key in mapping:
        raise RuntimeError(
            "Key already in mapping {}: {}".format(mapping, key)
        )
    else:
        mapping[key] = value


def generate_query(
        query: str,
        dynamic_args: Optional[Mapping[str, Any]]=None,
        static_args: Optional[Mapping[str, Any]]=None
        ) -> Callable[[Cursor], Iterator[Row]]:
    dynamic_args = dynamic_args or {}

    params = {}
    params.update(static_args or {})
    args = {}

    for key, value in dynamic_args.items():
        if not isinstance(value, str) and isinstance(value, Sequence):
            insert_new_key(
                params,
                key,
                ",".join(
                    ":" + key + str(i) for i in range(len(value))
                )
            )
            for i, item in enumerate(value):
                insert_new_key(args, key + str(i), item)
        else:
            insert_new_key(params, key, ":" + key)
            insert_new_key(args, key, value)
    formatted = query.format(**params)

    def execute(cursor: Cursor) -> Iterator[Row]:
        cursor.execute(formatted, args)
        return (row for row in cursor)

    return execute


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


class Command(metaclass=ABCMeta):
    EXIT_DB_EXISTS = 21

    EXIT_TAG_TYPES_EXIST = 22

    EXIT_NOTE_NOT_EXISTS = 23

    EXIT_NOTE_EXISTS = 24

    EXIT_TAG_NOT_EXISTS = 25

    EXIT_BAD_NAME = 26

    EXIT_BAD_RANGE = 27

    EXIT_BAD_REGEX = 28

    EXIT_EDITOR_FAILED = 29

    EXIT_EXISTING_MAPPINGS = 30

    EXIT_IMPORT_FILE_NOT_EXISTS = 31

    EXIT_BAD_PERMISSIONS = 32

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
    @abstractmethod
    def run(
            cls, cursor: Cursor, arguments: Namespace, config: Config
            ) -> Iterator[str]:
        pass

    @classmethod
    def format(
            cls, tags: Iterator[str], arguments: Namespace, config: Config
            ) -> None:
        for tag in tags:
            print(tag, file=stdout)


class Init(Command):
    NAME = "init"

    DESCRIPTION = "Initialize the tag database."

    CREATE_TABLES = (
        "create table tags ("
        "    id integer primary key not null,"
        "    name text unique not null,"
        "    type not null references tag_types(id)"
        ");"
        "create table mappings ("
        "    id integer primary key not null,"
        "    category not null references tags(id),"
        "    member not null references tags(id),"
        "    unique (category, member)"
        ");"
        "create table tag_types ("
        "    id integer primary key not null,"
        "    name text unique not null"
        ");"
    )

    ADD_TYPES = (
        "insert into tag_types (id, name)"
        "    values (:id, :name);"
    )

    @classmethod
    def name(cls) -> str:
        return cls.NAME

    @classmethod
    def description(cls) -> str:
        return cls.DESCRIPTION

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> None:
        pass

    @classmethod
    def run(
            cls, cursor: Cursor, arguments: Namespace, config: Config
            ) -> Iterator[str]:
        try:
            cursor.executescript(cls.CREATE_TABLES)
        except OperationalError as e:
            raise TagError(
                "Error creating tables;"
                " has the database already been initialized?",
                cls.EXIT_DB_EXISTS
            ) from e
        try:
            cursor.executemany(
                cls.ADD_TYPES,
                [
                    dict(id=item.value, name=item.name.lower())
                    for item in TagType
                ]
            )
        except IntegrityError as e:
            raise TagError(
                "Error adding tag types;"
                " has the database already been created?",
                cls.EXIT_TAG_TYPES_EXIST
            ) from e
        return iter([])


class Add(Command):
    NAME = "add"

    DESCRIPTION = "Add categories to a tag."

    ADD_TAG = (
        "insert or ignore into tags (name, type)"
        "    values (:name, :type);"
    )

    ADD_MAPPING = (
        "insert or ignore into mappings (category, member)"
        "    values (:category, :member);"
    )

    GET_ID = (
        "select id"
        "    from tags"
        "    where name = :name"
        "    limit 1;"
    )

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
    def get_tag_id(cls, cursor: Cursor, name: str) -> Optional[int]:
        cursor.execute(
            cls.GET_ID,
            dict(name=name)
        )
        tag_id = None
        for row in cursor:
            if tag_id is not None:
                raise RuntimeError(
                    "Duplicate entries for tag: '{}'".format(name)
                )
            tag_id = row["id"]
        return tag_id

    @classmethod
    def add_tag(
            cls, cursor: Cursor, name: str, config: Config
            ) -> Tuple[int, bool]:
        try:
            tag_type = Tag.of(name).value
        except ValueError as e:
            raise TagError(
                "Bad tag name {}".format(name), cls.EXIT_BAD_NAME
            ) from e
        if tag_type == TagType.NOTE:
            tag_path = Path(config.notes_directory, name)
            if tag_path.is_file():
                raise TagError(
                    "Tag '{}' does not exist.".format(tag_path),
                    cls.EXIT_NOTE_NOT_EXISTS
                )
        cursor.execute(
            cls.ADD_TAG,
            dict(name=name, type=tag_type)
        )
        if cursor.rowcount == 1:
            changed = True
        elif cursor.rowcount == 0:
            changed = False
        else:
            raise RuntimeError(
                "Error adding tag {}: {} rows modified.".format(
                    name, cursor.rowcount
                )
            )
        tag_id = cls.get_tag_id(cursor, name)
        if tag_id is None:
            raise RuntimeError(
                "Failed to add tag and return id: '{}'".format(name)
            )
        return tag_id, changed

    @classmethod
    def run(
            cls, cursor: Cursor, arguments: Namespace, config: Config
            ) -> Iterator[str]:
        added_tags = []
        tag_id, tag_changed = cls.add_tag(
            cursor, arguments.tag, config
        )
        if tag_changed:
            added_tags.append(arguments.tag)
        for category in arguments.categories:
            category_id = cls.get_tag_id(cursor, category)
            if category_id is None:
                raise TagError(
                    "Could not find category: '{}'".format(category),
                    cls.EXIT_TAG_NOT_EXISTS
                )
            cursor.execute(
                cls.ADD_MAPPING,
                dict(category=category_id, member=tag_id)
            )
        return iter(added_tags)


class Members(Command):
    NAME = "members"

    DESCRIPTION = "List immediate members of a category."

    QUERY = (
        "select t.name"
        "    from tags t"
        "    left join mappings m"
        "        on t.id = m.member"
        "    left join tags c"
        "        on m.category = c.id"
        "    where {}"
        "    order by t.name;"
    )

    NO_PARENT = QUERY.format(
        "m.member is null"
    )

    WITH_PARENT = QUERY.format(
        "c.name = {category}"
    )

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
    def run(
            cls, cursor: Cursor, arguments: Namespace, config: Config
            ) -> Iterator[str]:
        if arguments.category:
            query = generate_query(
                cls.WITH_PARENT,
                dict(category=arguments.category)
            )
        else:
            query = generate_query(cls.NO_PARENT)
        return (row["name"] for row in query(cursor))


class Categories(Command):
    NAME = "categories"

    DESCRIPTION = "List immediate categories a tag belongs to."

    QUERY = (
        "select c.name"
        "    from tags t"
        "    inner join mappings m"
        "        on"
        "            t.id = m.member"
        "            and t.name = {tag}"
        "    inner join tags c"
        "        on m.category = c.id"
        "    order by c.name;"
    )

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
    def run(
            cls, cursor: Cursor, arguments: Namespace, config: Config
            ) -> Iterator[str]:
        query = generate_query(
            cls.QUERY,
            dict(tag=arguments.tag)
        )
        return (row["name"] for row in query(cursor))


RECURSIVE_MEMBERS = (
    "with recursive"
    "    members (id, name, type) as ("
    "        select t.id, t.name, t.type"
    "            from tags t"
    "            inner join mappings m"
    "                on"
    "                    t.id = m.category"
    "                    and t.name in ({categories})"
    "        union all"
    "        select t.id, t.name, t.type"
    "            from members a"
    "            inner join mappings m"
    "                on a.id = m.category"
    "            inner join tags t"
    "                on m.member = t.id"
    "    )"
)


def slice_to_limit_offset(slice_: str) -> Tuple[int, int]:
    if not slice_:
        raise ValueError("Empty slice")
    components = slice_.split(":")
    if len(components) > 2 or len(components) < 1:
        raise ValueError("Bad slice: '{}'".format(slice_))
    if components[0]:
        start = int(components[0])
        if start < 0:
            raise ValueError("Negative slice indices are not supported.")
    else:
        start = 0
    if len(components) == 1:
        end = start + 1
    elif components[1]:
        end = int(components[1])
        if end < 0:
            raise ValueError("Negative slice indices are not supported.")
    else:
        end = -1

    offset = start
    limit = end - start

    return limit, offset


class Show(Command):
    NAME = "show"

    DESCRIPTION = "Combine all notes into a single document."

    HEADER = "{}\n---\n"

    FOOTER = "\n***\n"

    ALL_NOTES = (
        "select name"
        "    from tags"
        "    where type = {note_type}"
        "    order by name {order}"
        "    limit {limit} offset {offset};"
    )

    NOTES_OF_CATEGORIES = (
        "{}"
        "select distinct name"
        "    from members"
        "    where type = {{note_type}}"
        "    order by name {{order}}"
        "    limit {{limit}} offset {{offset}};"
    ).format(
        RECURSIVE_MEMBERS
    )

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
        parser.add_argument(
            "-r", "--range",
            help="A continuous range of notes to show in Python slice notation"
        )
        parser.add_argument(
            "-b", "--beginning",
            action="store_true",
            help="List notes from beginning forward and not present backward"
        )
        parser.add_argument(
            "-s", "--search",
            help="A regex in the notes to filter on"
        )

    @classmethod
    def limit_offset(cls, range_: str) -> Mapping[str, int]:
        if range_ is not None:
            try:
                limit, offset = slice_to_limit_offset(range_)
            except ValueError as e:
                raise TagError(
                    "Bad range: {}".format(range_), cls.EXIT_BAD_RANGE
                ) from e
        else:
            limit = -1
            offset = 0
        return dict(limit=limit, offset=offset)

    @classmethod
    def query_categories(
            cls,
            categories: Sequence[str],
            note_type: TagType,
            order: str,
            limit: int,
            offset: int
            ) -> Callable[[Cursor], Iterator[Row]]:
        return generate_query(
            cls.NOTES_OF_CATEGORIES,
            dict(
                categories=categories,
                note_type=note_type.value,
                limit=limit,
                offset=offset
            ),
            dict(order=order)
        )

    @classmethod
    def query_all(
            cls,
            note_type: TagType,
            order: str,
            limit: int,
            offset: int
            ) -> Callable[[Cursor], Iterator[Row]]:
        return generate_query(
            cls.ALL_NOTES,
            dict(
                note_type=note_type.value,
                limit=limit,
                offset=offset
            ),
            dict(order=order)
        )

    @classmethod
    def compile_regex(cls, pattern: Optional[str]) -> Optional[Pattern]:
        if not pattern:
            return None
        try:
            regex = compile(pattern)
        except re_error as e:
            raise TagError(
                "Bad regex: '{}'".format(pattern), cls.EXIT_BAD_REGEX
            ) from e
        return regex

    @classmethod
    def filter_path(
            cls, path: Path, regex: Optional[Pattern]
            ) -> Optional[Path]:
        if not path.is_file():
            raise TagError(
                "Could not open note at path: '{}'".format(path),
                cls.EXIT_NOTE_NOT_EXISTS
            )
        if not regex:
            return path
        with path.open() as f:
            for line in f:
                if regex.search(line):
                    return path
        return None

    @classmethod
    def run(
            cls, cursor: Cursor, arguments: Namespace, config: Config
            ) -> Iterator[str]:
        limit_offset = cls.limit_offset(arguments.range)
        regex = cls.compile_regex(arguments.search)
        order = "asc" if arguments.beginning else "desc"
        if arguments.tags:
            query = cls.query_categories(
                arguments.tags, TagType.NOTE, order, **limit_offset
            )
        else:
            query = cls.query_all(
                TagType.NOTE, order, **limit_offset
            )
        return (
            row["name"] for row in query(cursor)
            if cls.filter_path(
                Path(config.notes_directory, row["name"]), regex
            )
        )

    @classmethod
    def print(cls, member: str, config: Config) -> None:
        note_path = Path(config.notes_directory, member)
        with note_path.open() as f:
            print(cls.HEADER.format(member), end="")
            for line in f:
                print(line, end="")
            print(cls.FOOTER, end="")

    @classmethod
    def format(
            cls, tags: Iterator[str], arguments: Namespace, config: Config
            ) -> None:
        for tag in tags:
            cls.print(tag, config)


class Last(Command):
    NAME = "last"

    DESCRIPTION = "Open the last note in a text editor."

    ALL_NOTES = (
        "select name"
        "    from tags"
        "    where type = {note_type}"
        "    order by name desc"
        "    limit 1;"
    )

    NOTES_OF_CATEGORIES = (
        "{}"
        "select name"
        "    from members"
        "    where type = {{note_type}}"
        "    order by name desc"
        "    limit 1;"
    ).format(
        RECURSIVE_MEMBERS,
    )

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
    def run(
            cls, cursor: Cursor, arguments: Namespace, config: Config
            ) -> Iterator[str]:
        if arguments.tags:
            query = generate_query(
                cls.NOTES_OF_CATEGORIES,
                dict(categories=arguments.tags, note_type=TagType.NOTE.value)
            )
        else:
            query = generate_query(
                cls.ALL_NOTES,
                dict(note_type=TagType.NOTE.value)
            )
        return (row["name"] for row in query(cursor))

    @classmethod
    def format(
            cls, tags: Iterator[str], arguments: Namespace, config: Config
            ) -> None:
        for tag in tags:
            tag_path = Path(config.notes_directory, tag)
            if not tag_path.is_file():
                raise TagError(
                    "Note '{}' does not exist.".format(tag_path),
                    cls.EXIT_NOTE_NOT_EXISTS
                )
            command = [*config.editor, str(tag_path)]
            try:
                subprocess_run(command, check=True)
            except (CalledProcessError, FileNotFoundError) as e:
                raise TagError(
                    "Editor command {} failed.".format(command),
                    cls.EXIT_EDITOR_FAILED
                ) from e


class Remove(Command):
    NAME = "remove"

    DESCRIPTION = "Remove a tag from categories or from everything."

    REMOVE_EVERYTHING = (
        "delete from tags"
        "    where name = {tag};"
    )

    REMOVE_CATEGORIES = (
        "with"
        "    member_id (id) as ("
        "        select id"
        "            from tags"
        "            where name = {tag}"
        "    ),"
        "    category_id (id) as ("
        "        select id"
        "            from tags"
        "            where name in ({categories})"
        "    )"
        "delete from mappings"
        "    where"
        "        member in member_id"
        "        and category in category_id;"
    )

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
    def run(
            cls, cursor: Cursor, arguments: Namespace, config: Config
            ) -> Iterator[str]:
        removed_tags = []
        if arguments.categories:
            query = generate_query(
                cls.REMOVE_CATEGORIES,
                dict(
                    tag=arguments.tag,
                    categories=arguments.categories
                )
            )
            query(cursor)
        else:
            query = generate_query(
                cls.REMOVE_EVERYTHING,
                dict(
                    tag=arguments.tag
                )
            )
            try:
                query(cursor)
                if cursor.rowcount == 1:
                    removed_tags.append(arguments.tag)
                elif cursor.rowcount > 1:
                    raise RuntimeError(
                        "Removed more than one row deleting '{}'".format(
                            arguments.tag
                        )
                    )
            except IntegrityError as e:
                raise TagError(
                    (
                        "Failed removing tag '{}'."
                        " Try removing all of its categories and members"
                        " first."
                    ).format(arguments.tag),
                    cls.EXIT_EXISTING_MAPPINGS
                ) from e
        return iter(removed_tags)


class Validate(Command):
    NAME = "validate"

    DESCRIPTION = "Check that all notes exist; print missing notes."

    ALL_NOTES = (
        "select name"
        "    from tags"
        "    where type = :note_type;"
    )

    @classmethod
    def name(cls) -> str:
        return cls.NAME

    @classmethod
    def description(cls) -> str:
        return cls.DESCRIPTION

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> None:
        parser.add_argument(
            "-m", "--max",
            help="The maximum missing notes to print",
            default=10,
            type=int
        )

    @classmethod
    def notes_in_filesystem(cls, config: Config) -> Set[Path]:
        notes = set()
        for path in scandir(config.notes_directory):
            if path.is_file():
                try:
                    if Tag.of(path.name) == TagType.NOTE:
                        notes.add(Path(path.path))
                except ValueError:
                    continue
        return notes

    @classmethod
    def max_notes_reached(cls, maximum: int, missing: int) -> bool:
        return maximum >= 0 and (maximum == 0 or missing >= maximum)

    @classmethod
    def format_missing_note(cls, note: Path, type_: str, maximum: int) -> None:
        if maximum != 0:
            print("{}: {}".format(type_, note), file=stderr)

    @classmethod
    def run(
            cls, cursor: Cursor, arguments: Namespace, config: Config
            ) -> Iterator[str]:
        fs_notes = cls.notes_in_filesystem(config)

        cursor.execute(cls.ALL_NOTES, dict(note_type=TagType.NOTE.value))

        missing = 0
        for row in cursor:
            note = Path(config.notes_directory, row["name"])
            if note in fs_notes:
                fs_notes.remove(note)
            else:
                missing += 1
                cls.format_missing_note(note, "db", arguments.max)
                if cls.max_notes_reached(arguments.max, missing):
                    break

        if not cls.max_notes_reached(arguments.max, missing):
            for note in fs_notes:
                missing += 1
                cls.format_missing_note(note, "fs", arguments.max)
                if cls.max_notes_reached(arguments.max, missing):
                    break

        if missing > 0:
            raise TagError("", cls.EXIT_NOTE_NOT_EXISTS)
        return iter([])


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
                cls.EXIT_IMPORT_FILE_NOT_EXISTS
            ) from e
        except PermissionError as e:
            raise TagError(
                "Could not read file: '{}'".format(path),
                cls.EXIT_BAD_PERMISSIONS
            ) from e
        return stat

    @classmethod
    def filename(cls, timestamp: datetime) -> Path:
        name = (
            "{year}-{month}-{day}_{hour}-{minute}-{second}.txt".format(
                year=left_pad(str(timestamp.year), 4, "0"),
                month=left_pad(str(timestamp.month), 2, "0"),
                day=left_pad(str(timestamp.day), 2, "0"),
                hour=left_pad(str(timestamp.hour), 2, "0"),
                minute=left_pad(str(timestamp.minute), 2, "0"),
                second=left_pad(str(timestamp.second), 2, "0")
            )
        )
        return Path(name)

    @classmethod
    def run(
            cls, cursor: Cursor, arguments: Namespace, config: Config
            ) -> Iterator[str]:
        destinations = []
        for path in arguments.files:
            stat = cls.stat(path)
            timestamp = datetime.fromtimestamp(stat.st_mtime)
            name = cls.filename(timestamp)
            destination = Path(config.notes_directory, name)
            if destination.exists():
                raise TagError(
                    "Note already exists: '{}'".format(destination),
                    cls.EXIT_NOTE_EXISTS
                )
            try:
                copy2(str(path), str(destination))
            except PermissionError as e:
                raise TagError(
                    "Could not write to file: '{}'".format(destination),
                    cls.EXIT_BAD_PERMISSIONS
                ) from e
            destinations.append(str(name))
        return iter(destinations)


COMMANDS = (
    Init,
    Add,
    Members,
    Categories,
    Show,
    Last,
    Remove,
    Validate,
    Import
)


def argument_parser() -> ArgumentParser:
    parser = ArgumentParser()
    parser.add_argument(
        "-c", "--config",
        help="The configuration file to use",
        default=Path("tag.config.json"),
        type=Path
    )
    parser.add_argument(
        "-d", "--debug",
        help="Print more verbose error messages",
        action="store_true"
    )
    action = parser.add_subparsers(metavar="command", dest="command")

    for command in COMMANDS:
        command_parser = action.add_parser(
            command.name(), help=command.description()
        )
        command.arguments(command_parser)
        command_parser.set_defaults(run=command.run, format=command.format)

    return parser


def read_config_file(path: Path) -> Config:
    try:
        with path.open() as file:
            config = Config(file)
    except FileNotFoundError:
        config = Config()
    return config


def handle_tag_error(error: TagError, debug: bool=False) -> None:
    if debug:
        print_exc()
    else:
        if str(error):
            print(error, file=stderr)
    exit(error.exit_status)


def run(args: Sequence[str]) -> None:
    parser = argument_parser()
    args = parser.parse_args(args)

    if not args.command:
        parser.print_help(stderr)
        exit(2)

    config = None
    try:
        config = read_config_file(Path(args.config))
    except TagError as e:
        handle_tag_error(e, args.debug)

    with connect(str(config.db_file)) as connection:
        connection.row_factory = Row
        cursor = connection.cursor()
        cursor.execute("pragma foreign_keys = 1;")
        try:
            results = args.run(cursor, args, config)
            args.format(results, args, config)
        except TagError as e:
            handle_tag_error(e, args.debug)


def main():
    run(argv[1:])


if __name__ == "__main__":
    main()
