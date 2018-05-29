#!/usr/bin/env python3

from abc import ABCMeta, abstractmethod
from enum import Enum
from sqlite3 import connect, Cursor, Row
from argparse import ArgumentParser, Namespace
from collections import OrderedDict
from re import compile
from sys import stdout, argv
from typing import Sequence, Iterator, Optional, Callable, Mapping, Any
from pathlib import Path


class Properties:
    DB_FILE = Path("./tags.sqlite")

    NOTES_DIRECTORY = Path(".")


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


def insert_new_key(mapping: dict, key: Any, value: Any) -> None:
    if key in mapping:
        raise RuntimeError(
            "Key already in mapping {}: {}".format(mapping, key)
        )
    else:
        mapping[key] = value


def generate_query(
        query: str,
        dynamic_args: Optional[Mapping[str, Any]]=None
        ) -> Callable[[Cursor], Iterator[Row]]:
    dynamic_args = dynamic_args or {}

    params = {}
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
    def run(cls, cursor: Cursor, arguments: Namespace) -> None:
        pass


class Init(Command):
    CREATE_TABLES = (
        "create table if not exists tags ("
        "    id integer primary key not null,"
        "    name text unique not null,"
        "    type not null references tag_types(id)"
        ");"
        "create table if not exists mappings ("
        "    id integer primary key not null,"
        "    category not null references tags(id),"
        "    member not null references tags(id),"
        "    unique (category, member)"
        ");"
        "create table if not exists tag_types ("
        "    id integer primary key not null,"
        "    name text unique not null"
        ");"
    )

    ADD_TYPES = (
        "insert or ignore into tag_types (id, name) values ("
        "    ?,?"
        ");"
    )

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> ArgumentParser:
        parser.description = "Initialize the tag database"
        return parser

    @classmethod
    def run(cls, cursor: Cursor, arguments: Namespace) -> None:
        cursor.executescript(cls.CREATE_TABLES)
        cursor.executemany(
            cls.ADD_TYPES,
            [(item.value, item.name.lower()) for item in TagType]
        )


class Add(Command):
    ADD_TAG = (
        "insert or ignore into tags (name, type) values (?, ?);"
    )

    ADD_MAPPING = (
        "insert or ignore into mappings (category, member) values (?, ?);"
    )

    GET_ID = (
        "select id from tags where name = ? limit 1;"
    )

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> ArgumentParser:
        parser.description = "Add members to a category"
        parser.add_argument("category", help="The category to add to")
        parser.add_argument(
            "members", nargs="*", help="The members to add to the category"
        )
        return parser

    @classmethod
    def run(cls, cursor: Cursor, arguments: Namespace) -> None:
        category_type = Tag.of(arguments.category).value
        cursor.execute(cls.ADD_TAG, (arguments.category, category_type))
        cursor.execute(cls.GET_ID, (arguments.category,))
        category_id = next(row["id"] for row in cursor)
        for member in arguments.members:
            member_type = Tag.of(member).value
            cursor.execute(cls.ADD_TAG, (member, member_type))
            cursor.execute(cls.GET_ID, (member,))
            member_id = next(row["id"] for row in cursor)
            cursor.execute(cls.ADD_MAPPING, (category_id, member_id))


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
        "c.name = ?"
    )

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> ArgumentParser:
        parser.description = "List immediate members of a category"
        parser.add_argument(
            "category",
            help="The category to list, else all tags without a category",
            nargs="?"
        )
        return parser

    @classmethod
    def run(cls, cursor: Cursor, arguments: Namespace) -> None:
        if arguments.category:
            cursor.execute(cls.WITH_PARENT, (arguments.category,))
        else:
            cursor.execute(cls.NO_PARENT)
        members = (row["name"] for row in cursor)
        print(" ".join(members), file=stdout)


class Categories(Command):
    QUERY = (
        "select c.name"
        "    from tags t"
        "    inner join mappings m"
        "        on"
        "            t.id = m.member"
        "            and t.name = ?"
        "    inner join tags c"
        "        on m.category = c.id"
        "    order by c.name;"
    )

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> ArgumentParser:
        parser.description = "List immediate categories a tag belongs to"
        parser.add_argument("tag", help="The tag to list categories for")
        return parser

    @classmethod
    def run(cls, cursor: Cursor, arguments: Namespace) -> None:
        tag = arguments.tag
        cursor.execute(cls.QUERY, (tag,))
        members = (row["name"] for row in cursor)
        print(" ".join(members), file=stdout)


RECURSIVE_MEMBERS = (
    "with recursive"
    "    members(id, name, type) as ("
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


class Show(Command):
    HEADER = "{}\n---\n"

    FOOTER = "\n***\n"

    ALL_NOTES = (
        "select name"
        "    from tags"
        "    where type = {}"
        "    order by name;"
    ).format(
        TagType.NOTE.value
    )

    NOTES_OF_CATEGORIES = (
        "{}"
        "select distinct name"
        "    from members"
        "    where type = {}"
        "    order by name;"
    ).format(
        RECURSIVE_MEMBERS,
        TagType.NOTE.value
    )

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> ArgumentParser:
        parser.description = "Combine all notes into a single document"
        parser.add_argument(
            "tags", nargs="*", help="The tags to combine, else all"
        )
        return parser

    @classmethod
    def run(cls, cursor: Cursor, arguments: Namespace) -> None:
        if arguments.tags:
            query = generate_query(
                cls.NOTES_OF_CATEGORIES,
                dict(categories=arguments.tags)
            )
        else:
            query = generate_query(cls.ALL_NOTES)
        for row in query(cursor):
            cls.print(row["name"])

    @classmethod
    def print(cls, member: str) -> None:
        with open(Properties.NOTES_DIRECTORY / member, "r") as f:
            print(cls.HEADER.format(member), end="")
            for line in f:
                print(line, end="")
            print(cls.FOOTER, end="")


class Last(Command):
    ALL_NOTES = (
        "select name"
        "    from tags"
        "    where type = {}"
        "    order by name desc"
        "    limit 1;"
    ).format(
        TagType.NOTE.value
    )

    NOTES_OF_CATEGORIES = (
        "{}"
        "select name"
        "    from members"
        "    where type = {}"
        "    order by name desc"
        "    limit 1;"
    ).format(
        RECURSIVE_MEMBERS,
        TagType.NOTE.value
    )

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> ArgumentParser:
        parser.description = "Open the last note in a text editor"
        parser.add_argument(
            "tags", nargs="*", help="The tags to search, else all"
        )
        return parser

    @classmethod
    def run(cls, cursor: Cursor, arguments: Namespace) -> None:
        if arguments.tags:
            query = generate_query(
                cls.NOTES_OF_CATEGORIES,
                dict(categories=arguments.tags)
            )
        else:
            query = generate_query(cls.ALL_NOTES)
        for row in query(cursor):
            # run EDITOR
            print(row["name"])


COMMANDS = OrderedDict(
    [
        ('init', Init),
        ('add', Add),
        ('members', Members),
        ('categories', Categories),
        ('show', Show),
        ('last', Last),
        #('remove', Remove)
    ]
)


def run(args: Sequence[str]) -> None:
    parser = ArgumentParser()
    subparsers = parser.add_subparsers(help="Commands")

    for name, command in COMMANDS.items():
        command_parser = subparsers.add_parser(name)
        command_parser = command.arguments(command_parser)
        command_parser.set_defaults(run=command.run)

    args = parser.parse_args(args)

    with connect(str(Properties.DB_FILE)) as connection:
        connection.row_factory = Row
        cursor = connection.cursor()
        cursor.execute("pragma foreign_keys = 1;")
        args.run(cursor, args)


def main():
    run(argv)


if __name__ == "__main__":
    main()
