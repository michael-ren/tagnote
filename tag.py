#!/usr/bin/env python3

from abc import ABCMeta, abstractmethod
from argparse import ArgumentParser, Namespace
from collections import OrderedDict
from enum import Enum
from json import load
from os import environ
from pathlib import Path
from re import compile
from shutil import which
from sqlite3 import connect, Cursor, Row, IntegrityError, OperationalError
from subprocess import run as subprocess_run, CalledProcessError
from sys import stdout, stderr, argv, exit
from traceback import print_exc
from typing import (
    Sequence, Iterator, Optional, Callable, Mapping, Any, Tuple, TextIO
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
    def __init__(self, message: str, exit_status: int = 1) -> None:
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


class Command(metaclass=ABCMeta):
    @classmethod
    @abstractmethod
    def arguments(cls, parser: ArgumentParser) -> ArgumentParser:
        pass

    @classmethod
    @abstractmethod
    def run(
            cls, cursor: Cursor, arguments: Namespace, config: Config
            ) -> Iterator[str]:
        pass

    @classmethod
    @abstractmethod
    def format(cls, tags: Iterator[str], config: Config) -> None:
        pass


class Init(Command):
    EXIT_DB_EXISTS = 21

    EXIT_TAG_TYPES_EXIST = 22

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
    def arguments(cls, parser: ArgumentParser) -> ArgumentParser:
        parser.description = "Initialize the tag database."
        return parser

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
        yield from ()

    @classmethod
    def format(cls, tags: Iterator[str], config: Config) -> None:
        pass


class Add(Command):
    EXIT_BAD_NAME = 21

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
    def arguments(cls, parser: ArgumentParser) -> ArgumentParser:
        parser.description = "Add members to a category."
        parser.add_argument("category", help="The category to add to")
        parser.add_argument(
            "members", nargs="*", help="The members to add to the category"
        )
        return parser

    @classmethod
    def add_tag(cls, cursor: Cursor, name: str) -> Tuple[int, bool]:
        try:
            tag_type = Tag.of(name).value
        except ValueError as e:
            raise TagError(
                "Bad tag name {}".format(name), cls.EXIT_BAD_NAME
            ) from e
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
                "Error adding tag {}: {} rows modified".format(
                    name, cursor.rowcount
                ),
                cls.EXIT_BAD_NAME
            )
        cursor.execute(
            cls.GET_ID,
            dict(name=name)
        )
        tag_id = next(row["id"] for row in cursor)
        return tag_id, changed

    @classmethod
    def run(
            cls, cursor: Cursor, arguments: Namespace, config: Config
            ) -> Iterator[str]:
        new_members = []
        category_id, category_changed = cls.add_tag(
            cursor, arguments.category
        )
        if category_changed:
            new_members.append(arguments.category)
        for member in arguments.members:
            member_id, member_changed = cls.add_tag(cursor, member)
            if member_changed:
                new_members.append(member)
            cursor.execute(
                cls.ADD_MAPPING,
                dict(category=category_id, member=member_id)
            )
        yield from new_members

    @classmethod
    def format(cls, tags: Iterator[str], config: Config) -> None:
        for tag in tags:
            print("Added new tag '{}'".format(tag), file=stderr)


class Members(Command):
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
    def arguments(cls, parser: ArgumentParser) -> ArgumentParser:
        parser.description = "List immediate members of a category."
        parser.add_argument(
            "category",
            help="The category to list, else all tags without a category",
            nargs="?"
        )
        return parser

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
        yield from (row["name"] for row in query(cursor))

    @classmethod
    def format(cls, tags: Iterator[str], config: Config) -> None:
        print(" ".join(tags), file=stdout)


class Categories(Command):
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
    def arguments(cls, parser: ArgumentParser) -> ArgumentParser:
        parser.description = "List immediate categories a tag belongs to."
        parser.add_argument("tag", help="The tag to list categories for")
        return parser

    @classmethod
    def run(
            cls, cursor: Cursor, arguments: Namespace, config: Config
            ) -> Iterator[str]:
        query = generate_query(
            cls.QUERY,
            dict(tag=arguments.tag)
        )
        yield from (row["name"] for row in query(cursor))

    @classmethod
    def format(cls, tags: Iterator[str], config: Config) -> None:
        print(" ".join(tags), file=stdout)


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
    EXIT_BAD_RANGE = 21

    EXIT_FILE_NOT_FOUND = 22

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
    def arguments(cls, parser: ArgumentParser) -> ArgumentParser:
        parser.description = "Combine all notes into a single document."
        parser.add_argument(
            "tags", nargs="*", help="The tags to combine, else all"
        )
        parser.add_argument(
            "-r",
            "--range",
            help="A continuous range of notes to show in Python slice notation"
        )
        parser.add_argument(
            "-b",
            "--beginning",
            action="store_true",
            help="List notes from beginning forward and not present backward"
        )
        return parser

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
    def run(
            cls, cursor: Cursor, arguments: Namespace, config: Config
            ) -> Iterator[str]:
        limit_offset = cls.limit_offset(arguments.range)
        order = "asc" if arguments.beginning else "desc"
        if arguments.tags:
            query = generate_query(
                cls.NOTES_OF_CATEGORIES,
                dict(
                    categories=arguments.tags,
                    note_type=TagType.NOTE.value,
                    **limit_offset
                ),
                dict(order=order)
            )
        else:
            query = generate_query(
                cls.ALL_NOTES,
                dict(
                    note_type=TagType.NOTE.value,
                    **limit_offset
                ),
                dict(order=order)
            )
        yield from (row["name"] for row in query(cursor))

    @classmethod
    def print(cls, member: str, config: Config) -> None:
        note_path = config.notes_directory / member
        try:
            with note_path.open() as f:
                print(cls.HEADER.format(member), end="")
                for line in f:
                    print(line, end="")
                print(cls.FOOTER, end="")
        except FileNotFoundError as e:
            raise TagError(
                "Could not open note at path: '{}'".format(note_path),
                cls.EXIT_FILE_NOT_FOUND
            ) from e

    @classmethod
    def format(cls, tags: Iterator[str], config: Config) -> None:
        for tag in tags:
            cls.print(tag, config)


class Last(Command):
    EXIT_EDITOR_FAILED = 21

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
    def arguments(cls, parser: ArgumentParser) -> ArgumentParser:
        parser.description = "Open the last note in a text editor."
        parser.add_argument(
            "tags", nargs="*", help="The tags to search, else all"
        )
        return parser

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
        yield from (row["name"] for row in query(cursor))

    @classmethod
    def format(cls, tags: Iterator[str], config: Config) -> None:
        for tag in tags:
            command = [*config.editor, tag]
            try:
                subprocess_run(command, check=True)
            except (CalledProcessError, FileNotFoundError) as e:
                raise TagError(
                    "Editor command {} failed.".format(command),
                    cls.EXIT_EDITOR_FAILED
                ) from e


class Remove(Command):
    EXIT_EXISTING_MAPPINGS = 21

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
        "    )"
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
    def arguments(cls, parser: ArgumentParser) -> ArgumentParser:
        parser.description = "Remove a tag from categories or from everything."
        parser.add_argument("tag", help="The tag to remove")
        parser.add_argument(
            "categories",
            nargs="*",
            help="The categories to remove from, else everything"
        )
        return parser

    @classmethod
    def run(
            cls, cursor: Cursor, arguments: Namespace, config: Config
            ) -> Iterator[str]:
        results = []
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
                    results.append(arguments.tag)
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
                        " Try removing all of its categories and members first."
                    ).format(arguments.tag),
                    cls.EXIT_EXISTING_MAPPINGS
                ) from e
        yield from results

    @classmethod
    def format(cls, tags: Iterator[str], config: Config) -> None:
        for tag in tags:
            print("Removed tag '{}'".format(tag), file=stderr)


class Validate(Command):
    EXIT_MISSING_NOTE = 21

    ALL_NOTES = (
        "select name"
        "    from tags"
        "    where type = :note_type;"
    )

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> ArgumentParser:
        parser.description = "Check that all notes exist; print missing notes."
        parser.add_argument(
            "--max", "-m",
            help="The maximum missing notes to print", default=10, type=int
        )
        return parser

    @classmethod
    def run(
            cls, cursor: Cursor, arguments: Namespace, config: Config
            ) -> Iterator[str]:
        cursor.execute(cls.ALL_NOTES, dict(note_type=TagType.NOTE.value))
        missing = 0
        for row in cursor:
            if missing >= arguments.max > 0:
                break
            note_path = Path(config.notes_directory, row["name"])
            if not note_path.is_file():
                missing += 1
                if arguments.max != 0:
                    print(row["name"], file=stderr)
        if missing > 0:
            raise TagError("Missing notes", cls.EXIT_MISSING_NOTE)
        yield from ()

    @classmethod
    def format(cls, tags: Iterator[str], config: Config) -> None:
        pass


COMMANDS = OrderedDict(
    [
        ('init', Init),
        ('add', Add),
        ('members', Members),
        ('categories', Categories),
        ('show', Show),
        ('last', Last),
        ('remove', Remove),
        ('validate', Validate)
    ]
)


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
        print(error, file=stderr)
    exit(error.exit_status)


def run(args: Sequence[str]) -> None:
    parser = ArgumentParser()
    parser.add_argument(
        "--config", "-c",
        help="The configuration file to use",
        default=Path("tag.config.json"),
        type=Path
    )
    parser.add_argument(
        "--debug", "-d",
        help="Print more verbose error messages",
        action="store_true"
    )
    subparsers = parser.add_subparsers(help="Commands")

    for name, command in COMMANDS.items():
        command_parser = subparsers.add_parser(name)
        command_parser = command.arguments(command_parser)
        command_parser.set_defaults(run=command.run, format=command.format)

    args = parser.parse_args(args)

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
            args.format(results, config)
        except TagError as e:
            handle_tag_error(e, args.debug)


def main():
    run(argv[1:])


if __name__ == "__main__":
    main()
