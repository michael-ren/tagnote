#!/usr/bin/env python3

# Copyright 2019 Michael Ren <michael.ren@mailbox.org>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
Main module for tagnote

Conceptually, the program operates on a number of Tags. Tags are either
Notes or Labels.

Notes are plain-text files in the notes directory, by default ~/notes,
with names that are the timestamps of their creation,
e.g. 2019-12-31_23-59-59.txt. Because of the naming convention, sorting
Notes puts them in chronological order. There is no provision for storing
time zones, so the time zone is either that of the local time or UTC when
utc=true in the configuration file.

Labels are plain-text files with names that are alphanumeric characters,
dashes, and underscores. The content of Labels is a sorted,
newline-separated list of Tags that represent the members of the Label.

Labels contain any sort of Tag, including other Labels, and Notes
cannot contain any Tags. Tags can be members of as many Labels as needed.

The program does not create Notes, delegating this to a text editor and a
plugin that saves Notes in the notes directory with their timestamp.
Currently, the only plugin that exists is for vim.

This file is structured into several sections:

- Base types and helper functions

  - Config for managing configuration options
  - TagError and error handling
  - Tag and its implementations Note and Label. TAG_TYPES registers each
    implementation for use with helper functions.
  - Various helper functions, including tag_of(), a factory function for
    creating Tags, all_tags(), which returns an iterator of all Tags in the
    notes directory, and AllTagsFrom(), which does the same as all_tags except
    from a series of starting Tags.
  - Formatting and parsing functions and classes
  - Date pattern classes to support searching Tags by date range

- Commands, which correspond to individual sub-commands on the command line
  and which generally return a sequence of result Tags. COMMANDS is a registry
  of all commands available to the user.

  - Add and Remove change the existence and membership of Tags in the notes
    directory and return Tags that have changed.
  - Members and Categories report immediate children and immediate parents,
    respectively.
  - Show returns a formatted report of Notes that can be piped to a pager,
    and Last edits the chronologically last Note or diffs the last two Notes
    of a selection of Notes, e.g. the last Note in a Tag or the last Note in
    the notes directory.
  - Import converts any file to a Note by copying it to the notes directory
    directory and renaming the copy to the timestamp of the file's
    modification time.
  - Pull and Push use rsync to copy the notes directory elsewhere either
    locally or remotely for backup and synchronization.
  - Unknown and Reconcile are for dealing with the results of
    synchronization, with Unknown listing all unknown files in the Notes
    directory and Reconcile running a diff on the backup files that rsync
    generates.

- Post-processors for manipulating result Tags, such as sorting, slicing,
  and filters for date range, Tag name, Tag type, and text content of Notes.

- The main runtime, which in sequence:

  - Parses a command and options
  - Reads the configuration file
  - Determines runtime configuration values
  - Runs the command
  - Runs post-processors on the command results
  - Formats the result

All errors that the program knows about are wrapped in TagError,
which contains an exit_status that indicates the particular type of error
that occurred. By default, only the message of TagError is printed as the
program exits with the indicated exit_status. Passing "-d" to the command
line disables this behavior and prints the full stack trace on error.

"""


from abc import ABCMeta, abstractmethod
from argparse import ArgumentParser, Namespace
from bisect import bisect_left
from collections import OrderedDict, deque
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
    Sequence, Iterator, Iterable, Optional, Any, TextIO, Pattern, Type, Tuple,
    NamedTuple, Callable, Union, Mapping, MutableMapping, MutableSequence
)
from enum import Enum
from filecmp import cmp
from textwrap import indent as textwrap_indent, fill as textwrap_fill


VERSION = "4.2.0"


class Config:
    """
    The configuration for tagnote

    The PROPERTIES field defines all valid configuration options. This class
    either populates the values by default or reads values from a JSON file.
    The JSON file contains a flat object with the option name as the key and
    the option value as the value. For instance, {"foo": 1} sets the
    option named "foo" to 1.

    The following options are available:

    - notes_directory -- The string path relative to the home directory for the
        directory containing the notes, by default ["notes"], i.e. ~/notes
    - editor -- An array of strings describing the name of the editor and any
        arguments to pass to it, by default ["vim"]
    - diff -- An array of strings describing the name of the diff editor and
        any arguments to pass to it, by default ["vimdiff"]
    - rsync -- An array of strings describing the name of the rsync command and
        any arguments to pass to it, by default ["rsync"]
    - utc -- Whether to interpret timestamps as UTC or not, by default false

    If not set in the configuration file, the following environment variables
    set options:

    - TAGNOTE_EDITOR, VISUAL, and EDITOR -- In order of precedence, these set
        the editor command. Passing arguments is not supported.
    - TAGNOTE_DIFF -- This sets the diff editor command. Passing arguments is
        not supported.
    - TAGNOTE_RSYNC -- This sets the rsync command. Passing arguments is not
        supported.

    Each option definition in PROPERTIES is a dict with the following keys:

    - default -- The default value of the option. The value specified here gets
        passed through the constructor before being used, no differently than
        if it had been specified in the config file.
    - constructor -- A function that processes the raw option value before the
        option gets used.
    - check -- A boolean-returning function to run on the constructed value. If
        the result is False, this class raises a TagError with an
        EXIT_CONFIG_REQUIRED_PROPERTY status.
    - check_string -- A string description of the reason the check failed
    """
    # NOTE: If you add a new option to PROPERTIES, also add a stub with a type
    #       definition in the constructor to help with IDE auto-completion and
    #       type checking.
    PROPERTIES = dict(
        notes_directory=dict(
            default=Path("notes"),
            constructor=lambda p: Path(Path.home(), p),
            check=lambda v: v.is_dir(),
            check_string="must be an existing directory"
        ),
        editor=dict(
            default=[
                environ.get("TAGNOTE_EDITOR") or environ.get("VISUAL")
                or environ.get("EDITOR") or "vim"
            ],
            constructor=lambda v: [v] if isinstance(v, str) else v,
            check=lambda v: isinstance(v, Sequence),
            check_string="must be a command"
        ),
        diff=dict(
            default=[environ.get("TAGNOTE_DIFF") or "vimdiff"],
            constructor=lambda v: [v] if isinstance(v, str) else v,
            check=lambda v: isinstance(v, Sequence),
            check_string="must be a command"
        ),
        rsync=dict(
            default=[environ.get("TAGNOTE_RSYNC") or "rsync"],
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

    def __init__(self, file: Optional[TextIO] = None) -> None:
        """
        Create a Config instance, optionally parsing a config file, and make
        options available as fields.

        :param file: The file-like object for the configuration file, if any
        :raises TagError: For an error populating a configuration option
        """
        self.notes_directory = None  # type: Optional[Path]
        self.editor = None  # type: Optional[Sequence[str]]
        self.diff = None  # type: Optional[Sequence[str]]
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

    def __eq__(self, other):
        """
        Two Configs are equal if all their options have the same value
        """
        if isinstance(other, Config):
            return all(
                getattr(self, name) == getattr(other, name)
                for name in self.PROPERTIES.keys()
            )
        return False


class TagError(Exception):
    """
    An error running Tagnote. TagError wraps all expected exceptions, adding an
    exit status that explains why the error happened.
    """
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

    EXIT_BAD_TAG_TYPE = 34

    EXIT_BAD_TIMESTAMP = 35

    EXIT_BAD_DATE_PATTERN = 36

    EXIT_BAD_DATE_RANGE = 37

    def __init__(self, message: str, exit_status: int) -> None:
        """
        Create a TagError with a message and an exit status

        :param message: The message explaining the error
        :param exit_status: The exit status, e.g. TagError.EXIT_USAGE
        """
        super().__init__(message)
        self.exit_status = exit_status


class Tag(metaclass=ABCMeta):
    """
    The base unit in Tagnote, providing a common interface for both Notes and
    Labels.
    """
    def __init__(self, name: str, directory: Path) -> None:
        """
        Create a Tag. Different naming schemes distinguish Tags from each
        other, and the constructor checks that the caller follows the scheme
        for a particular type of Tag.

        :param name: The name identifying the Tag
        :param directory: The directory containing the Tag
        :raises TagError: If ``name`` is not valid for a Tag of this type
        """
        if not self.match(name):
            raise TagError(
                "'{}' is not a valid {}".format(name, self.tag_type()),
                TagError.EXIT_BAD_NAME
            )
        self.name = name
        self.directory = directory

    def __str__(self):
        return str(Path(self.directory, self.name))

    def __repr__(self):
        return "{}('{}')".format(type(self).__name__, self.__str__())

    def __hash__(self):
        return hash((self.name, self.directory))

    def __eq__(self, other):
        return isinstance(other, Tag) \
            and self.name == other.name \
            and self.directory == other.directory

    def _compare(self, other, operation: Callable[[Any, Any], bool]) -> bool:
        if not isinstance(other, Tag):
            raise TypeError(
                "Cannot compare '{}' with '{}'".format(self, other)
            )
        return operation(self.name, other.name)

    def __lt__(self, other):
        def operation(x, y) -> bool:
            return x < y

        return self._compare(other, operation)

    def __le__(self, other):
        def operation(x, y) -> bool:
            return x <= y

        return self._compare(other, operation)

    def __gt__(self, other):
        def operation(x, y) -> bool:
            return x > y

        return self._compare(other, operation)

    def __ge__(self, other):
        def operation(x, y) -> bool:
            return x >= y

        return self._compare(other, operation)

    @classmethod
    def match(cls, name: str) -> bool:
        """
        Check whether this is a valid name for a particular type of tag

        :param name: The name to check
        :return: Whether the name is valid
        """
        return bool(cls.pattern().match(name))

    def path(self) -> Path:
        """
        Get the path to the tag

        :return: The path
        """
        return Path(self.directory, self.name)

    def exists(self) -> bool:
        """
        Report on the existence of this tag

        :return: True if the tag exists, otherwise False
        """
        return self.path().is_file()

    def check_exists(self) -> bool:
        """
        Raise an exception if the tag doesn't exist

        :raises TagError: If the tag doesn't exist
        :return: True if the tag exists
        """
        if not self.exists():
            raise self.not_exists_error()
        return True

    def categories(self) -> Iterator["Tag"]:
        """
        Get the Tags this Tag is a member of

        :return: An Iterator of parent Tags
        """
        self.check_exists()
        matches = (
            tag for tag in all_tags(self.directory)
            if self in tag.members()
        )
        return matches

    @classmethod
    @abstractmethod
    def tag_type(cls) -> str:
        """
        Get the canonical name for this type of Tag

        :return: The canonical name
        """
        pass

    @classmethod
    @abstractmethod
    def pattern(cls) -> Pattern:
        """
        Get the regex pattern that matches the names of Tags of this type

        :return: The pattern
        """
        pass

    @abstractmethod
    def create(self) -> bool:
        """
        Create the tag

        :return: True if the Tag doesn't already exist, False otherwise
        """
        pass

    @abstractmethod
    def not_exists_error(self) -> TagError:
        """
        Generate an exception for if the Tag doesn't exist

        :return: The exception
        """
        pass

    @abstractmethod
    def add_member(self, tag: "Tag") -> bool:
        """
        Add a child to this Tag

        :param tag: The child to add
        :return: True if the child needed to be added, False otherwise
        """
        pass

    @abstractmethod
    def remove_member(self, tag: "Tag") -> bool:
        """
        Remove a child from this Tag

        :param tag: The child to remove
        :return: True if the child needed to be removed, False otherwise
        """
        pass

    @abstractmethod
    def members(self) -> Iterator["Tag"]:
        """
        Get the members of this Tag

        :return: An iterator of the members
        """
        pass

    @abstractmethod
    def search_text(self, pattern: Pattern) -> bool:
        """
        Check if the text of the Tag contains a pattern

        :param pattern: The pattern
        :return: True if the text matches, False otherwise
        """
        pass


class Note(Tag):
    """
    A Tag representing text content contained in a file
    """
    TAG_TYPE = "note"

    PATTERN = compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}.txt$")

    @classmethod
    def tag_type(cls) -> str:
        return cls.TAG_TYPE

    @classmethod
    def pattern(cls) -> Pattern:
        return cls.PATTERN

    def create(self) -> bool:
        """
        Notes cannot be created directly; they already exist.

        :raises TagError: If the Note doesn't already exist
        :return: False, since no new note has been created
        """
        self.check_exists()
        return False

    def not_exists_error(self) -> TagError:
        return TagError(
            "Note '{}' does not exist".format(self.path()),
            TagError.EXIT_NOTE_NOT_EXISTS
        )

    def add_member(self, tag: "Tag") -> bool:
        """
        Notes cannot have children.

        :raises TagError: Always
        """
        raise TagError(
            "Cannot add members to a note",
            TagError.EXIT_UNSUPPORTED_OPERATION
        )

    def remove_member(self, tag: "Tag") -> bool:
        """
        Notes cannot have children to remove.

        :raises TagError: Always
        """
        raise TagError(
            "Cannot remove members from a note",
            TagError.EXIT_UNSUPPORTED_OPERATION
        )

    def members(self) -> Iterator["Tag"]:
        """
        Notes cannot have children to return.

        :raises TagError: If the note doesn't exist
        :return: An empty iterator
        """
        self.check_exists()
        return iter([])

    def search_text(self, pattern: Pattern) -> bool:
        self.check_exists()
        with self.path().open() as f:
            for line in f:
                if pattern.search(line):
                    return True
        return False

    @classmethod
    def from_timestamp(cls, timestamp: datetime, directory: Path) -> "Note":
        """
        Create a representation of a Note from a timestamp instead of a string
        name.

        :param timestamp: The timestamp
        :param directory: The directory containing the file
        :return: The Note
        """
        name = "{}.txt".format(format_timestamp(timestamp))
        return cls(name, directory)

    def to_timestamp(self) -> datetime:
        """
        Return the timestamp of the Note

        :return: The timestamp
        """
        return parse_timestamp(Path(self.name).stem)


class Label(Tag):
    """
    A Tag containing other Tags in order
    """
    TAG_TYPE = "label"

    PATTERN = compile(r"^[\w-]+$")

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
        """
        Labels do not have text to search, so no pattern would match.

        :return: False
        """
        return False


TAG_TYPES = (Note, Label)


def tag_of(value: str, directory: Path) -> Tag:
    """
    Create a Tag, choosing the right type based on the format of the name

    :param value: The name of the Tag
    :param directory: The directory of the Tag
    :return: The new Tag
    """
    for type_ in TAG_TYPES:
        try:
            return type_(value, directory)
        except TagError as e:
            if e.exit_status == TagError.EXIT_BAD_NAME:
                continue
            raise
    raise TagError(
        "No tag type for '{}'".format(value),
        TagError.EXIT_BAD_NAME
    )


def tag_types(tag_type: Optional[Type[Tag]] = None) -> Tuple[Type[Tag]]:
    """
    Return some or all types of Tag, checking that all returned values are in
    fact Tag types

    :param tag_type: The type of Tag to return
    :return: The types of Tag that match
    """
    if tag_type is not None:
        if tag_type not in TAG_TYPES:
            raise TagError(
                "Not a valid tag type: '{}'".format(tag_type),
                TagError.EXIT_BAD_TAG_TYPE
            )
        types = (tag_type,)
    else:
        types = TAG_TYPES
    return types


def valid_tag_instance(
        instance: Tag, tag_type: Optional[Type[Tag]] = None
        ) -> bool:
    """
    Check that a Tag is a valid Tag instance, optionally of a certain type

    :param instance: The Tag to check
    :param tag_type: The type to check for
    :return: If the Tag is valid Tag, optionally of the right type
    """
    types = tag_types(tag_type)
    for type_ in types:
        if isinstance(instance, type_):
            return True
    return False


def valid_tag_name(
        name: str, tag_type: Optional[Type[Tag]] = None
        ) -> bool:
    """
    Check that a string is a valid name for a Tag, optionally of the right type

    :param name: The string to check
    :param tag_type: The Tag type to check for
    :return: Whether the string is a valid name
    """
    types = tag_types(tag_type)
    for type_ in types:
        if type_.match(name):
            return True
    return False


def all_non_tags(directory: Path) -> Iterator[Path]:
    """
    Return all files in a directory that are not Tags

    :param directory: The directory
    :return: An iterator of files that are not Tags
    """
    try:
        directory_scan = scandir(str(directory))
    except FileNotFoundError as e:
        raise TagError(
            "Directory not found: '{}'".format(directory),
            TagError.EXIT_DIRECTORY_NOT_FOUND
        ) from e
    return (
        Path(entry.path) for entry in directory_scan
        if entry.is_file() and not valid_tag_name(entry.name)
    )


def all_tags(
        directory: Path, tag_type: Optional[Type[Tag]] = None
        ) -> Iterator[Tag]:
    """
    Return all Tags in a directory

    :param directory: The directory to check
    :param tag_type: The type of Tag to return
    :return: The Tags in the directory
    """
    try:
        directory_scan = scandir(str(directory))
    except FileNotFoundError as e:
        raise TagError(
            "Directory not found: '{}'".format(directory),
            TagError.EXIT_DIRECTORY_NOT_FOUND
        ) from e
    return (
        tag_of(entry.name, directory) for entry in directory_scan
        if entry.is_file() and valid_tag_name(entry.name, tag_type)
    )


class AllTagsFrom(Iterator):
    """
    An iterator to return all Tags from a series of starting Tags, recursively
    retrieving children of the starting Tags
    """
    def __init__(
            self,
            categories: Iterable[Tag],
            tag_type: Optional[Type[Tag]] = None
            ) -> None:
        """
        Create an iterator for all Tags from a series of starting Tags

        :param categories: The starting points
        :param tag_type: The type of Tag to return
        """
        self.categories = categories
        self.tag_type = tag_type
        self.visited = set()
        self.remaining = deque(categories)

    def __next__(self) -> Tag:
        while self.remaining:
            # BFS queue: add to the right, pop from the left
            current_tag = self.remaining.popleft()
            self.visited.add(current_tag)
            for member in current_tag.members():
                if member not in self.visited:
                    self.remaining.append(member)
            if valid_tag_instance(current_tag, self.tag_type):
                return current_tag
        raise StopIteration


def left_pad(text: str, length: int, padding: str) -> str:
    """
    Pad text from the left to a certain length

    :param text: The text to pad
    :param length: The length to pad to
    :param padding: The padding to use
    :raises ValueError: If the padding is invalid or the text is too long
    :return: The padded text
    """
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
    """
    Turn a datetime into a formatted, Tag-compatible timestamp string

    :param timestamp: The timestamp to use
    :return: The formatted string
    """
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


def split_timestamp(timestamp: str) -> Sequence[str]:
    """
    Split a string as if it were a Tag-compatible timestamp

    :param timestamp: The string to split as a timestamp
    :return: The pieces of the timestamp
    """
    delimiters = ["-", "-", "_", "-", "-"]
    split = deque([timestamp])  # type: deque

    def raise_error() -> TagError:
        raise TagError(
            "Bad timestamp: {}".format(timestamp),
            TagError.EXIT_BAD_TIMESTAMP
        )

    for delimiter in delimiters:
        rest = split.pop()
        rest_split = rest.split(delimiter, 1)
        if not rest_split[0]:
            raise_error()
        for outlier in set(delimiters).difference({delimiter}):
            if outlier in rest_split[0]:
                raise_error()
        if len(rest_split) == 2:
            split.extend([rest_split[0], rest_split[1]])
        elif len(rest_split) == 1:
            split.append(rest_split[0])
            break
        else:
            raise_error()
    for delimiter in set(delimiters):
        if delimiter in split[-1]:
            raise_error()
    return list(split)


def parse_timestamp(timestamp: str) -> datetime:
    """
    Parse a Tag-compatible formatted string as a datetime

    :param timestamp: The formatted string
    :return: The datetime
    """
    split = split_timestamp(timestamp)
    try:
        return datetime(*[int(i) for i in split])
    except (ValueError, TypeError) as e:
        raise TagError(
            "Bad timestamp: {}".format(timestamp),
            TagError.EXIT_BAD_TIMESTAMP
        ) from e


def parse_backup_file(name: str) -> Tuple[str, str, str]:
    """
    Parse a backup file, e.g. for rsync transfers

    :param name: The file name to parse
    :return: The pieces of the backup file name
    """
    def exception() -> Exception:
        return TagError(
            "Bad backup file name: {}".format(name), TagError.EXIT_BAD_NAME
        )

    try:
        tag_name, timestamp, extension = name.rsplit(".", 2)
    except ValueError as e:
        raise exception() from e
    if extension != "bak" or not valid_tag_name(tag_name):
        raise exception()
    try:
        parse_timestamp(timestamp)
    except TagError as e:
        raise exception() from e
    return tag_name, timestamp, extension


class Formatter(metaclass=ABCMeta):
    """
    A class for printing a series of items to console
    """
    PADDING = 20

    @classmethod
    @abstractmethod
    def format(cls, items: Iterable[str]) -> None:
        """
        Print the items to console

        :param items: The items to print
        """
        pass


class MultipleColumn(Formatter):
    """
    A formatter that outputs in multiple columns, reading top-down and then
    left-to-right
    """
    @classmethod
    def format(cls, items: Iterable[str]) -> None:
        """
        Format the items in multiple columns, top-down and then left-to-right

        :param items: The items to print
        """
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
    """
    A formatter that prints items one-per-line
    """
    @classmethod
    def format(cls, items: Iterable[str]) -> None:
        """
        Format the items one line at a time

        :param items: The items to print
        """
        for item in items:
            print(item, file=stdout)


class DatePattern:
    """
    A pattern of dates, represented as a fixed-length series of date
    components, any of which may be None to indicate that any value matches.

    This DatePattern compares to other DatePatterns and also datetime objects
    using the standard comparison operators.
    """
    WILDCARD = "*"

    Pattern = NamedTuple(
        "Pattern",
        [
            ("year", Optional[int]),
            ("month", Optional[int]),
            ("day", Optional[int]),
            ("hour", Optional[int]),
            ("minute", Optional[int]),
            ("second", Optional[int])
        ]
    )

    def __init__(self, *elements: Optional[int]) -> None:
        """
        Create a DatePattern from a series of date elements

        :param elements: The date elements
        """
        if len(elements) > 6:
            raise TagError(
                "Too many elements in date pattern: {}".format(elements),
                TagError.EXIT_BAD_DATE_PATTERN
            )
        padding = 6 - len(elements)
        padded_elements = list(elements) + [None] * padding
        self.pattern = self.Pattern(*padded_elements)

    @classmethod
    def parse_element(cls, element: str) -> Optional[int]:
        if element == cls.WILDCARD:
            return None
        try:
            return int(element)
        except ValueError as e:
            raise TagError(
                "Bad date pattern element: {}".format(element),
                TagError.EXIT_BAD_DATE_PATTERN
            ) from e

    @classmethod
    def from_string(cls, pattern: str) -> "DatePattern":
        """
        Create a DatePattern from a string. This string is like a
        Tag-compatible timestamp, except any component can be * to indicate any
        value matches.

        :param pattern: The string pattern
        :return: The new DatePattern
        """
        return cls(
            *[cls.parse_element(i) for i in split_timestamp(pattern)]
        )

    def __hash__(self):
        return hash(self.pattern)

    def __eq__(self, other):
        return isinstance(other, DatePattern) and self.pattern == other.pattern

    def _compare(self, other, operation: Callable[[Any, Any], bool]) -> bool:
        if isinstance(other, DatePattern):
            def accessor(f):
                return getattr(other.pattern, f)
        elif isinstance(other, datetime):
            def accessor(f):
                return getattr(other, f)
        else:
            raise TypeError("Cannot compare {} with {}".format(self, other))

        for field in self.pattern._fields:
            self_item = getattr(self.pattern, field)
            other_item = accessor(field)
            if self_item is None \
                    or other_item is None \
                    or self_item == other_item:
                continue
            return operation(self_item, other_item)
        return True

    def __lt__(self, other):
        def operation(x, y) -> bool:
            return x < y
        return self._compare(other, operation)

    def __le__(self, other):
        def operation(x, y) -> bool:
            return x <= y
        return self._compare(other, operation)

    def __gt__(self, other):
        def operation(x, y) -> bool:
            return x > y
        return self._compare(other, operation)

    def __ge__(self, other):
        def operation(x, y) -> bool:
            return x >= y
        return self._compare(other, operation)


class DateRange:
    """
    Two DatePatterns indicating the start and end of a range of time.
    """
    def __init__(self, start: DatePattern, end: DatePattern) -> None:
        """
        Create a DateRange

        :param start: The starting pattern
        :param end: The ending pattern
        """
        self.start = start
        self.end = end

    @classmethod
    def from_string(cls, range_: str) -> "DateRange":
        """
        Create a DateRange from a string pattern, which is two DatePatterns
        separated by a colon.

        :param range_: The string pattern
        :return: The DateRange
        """
        elements = range_.split(":")
        if len(elements) == 1:
            elements.append(elements[0])
        if len(elements) != 2:
            raise TagError(
                "Bad date range: {}".format(range_),
                TagError.EXIT_BAD_DATE_RANGE
            )
        start, end = (
            DatePattern.from_string(element) for element in elements
        )
        return cls(start, end)

    def match(self, other: Union[DatePattern, datetime]) -> bool:
        """
        Check that a DatePattern or datetime is within the range of the
        DateRange. The check is inclusive on both the start and the end.

        :param other: The DatePattern or datetime
        :return: Whether the DatePattern or datetime are within range
        """
        return self.start <= other <= self.end


def check_external_command(command: Sequence[str], purpose: str) -> None:
    """
    Check that a command is available on the system

    :param command: The command to check
    :param purpose: A string description of what the command is used for
    """
    if len(command) < 1 or which(command[0]) is None:
        raise TagError(
            "Could not find command for {}: {}".format(purpose, command),
            TagError.EXIT_UNSUPPORTED_OPERATION
        )


class Command(metaclass=ABCMeta):
    """
    A particular command to run on the Tags
    """
    @classmethod
    @abstractmethod
    def name(cls) -> str:
        """
        Get the name of the command

        :return: The name
        """
        pass

    @classmethod
    @abstractmethod
    def description(cls) -> str:
        """
        Get the description of the command

        :return: The description
        """
        pass

    @classmethod
    @abstractmethod
    def arguments(cls, parser: ArgumentParser) -> None:
        """
        Add any arguments this particular command has

        :param parser: The ArgumentParser to add arguments to
        """
        pass

    @classmethod
    def default_sort_order(cls) -> Optional[bool]:
        """
        The default order results are sorted in for this command

        :return: None if no order, True if ascending, False if descending
        """
        return True

    @classmethod
    @abstractmethod
    def run(cls, arguments: Namespace, config: Config) -> Iterator[Tag]:
        """
        Run the command, doing any work to generate result Tags.

        :param arguments: The command-line arguments
        :param config: The configuration to use
        :return: An Iterator of result Tags
        """
        pass

    @classmethod
    def format(
            cls,
            tags: Iterable[Tag],
            arguments: Namespace,
            config: Config,
            formatter: Type[Formatter]
            ) -> None:
        """
        Process the result Tags and show the result to the user.

        :param tags: The result Tags
        :param arguments: The command-line arguments
        :param config: The configuration to use
        :param formatter: The formatter to use
        """
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
        parser.add_argument(
            "-p", "--prototype", help="Another tag to copy categories from"
        )
        parser.add_argument("tag", help="The tag to add")
        parser.add_argument(
            "categories", nargs="*", help="The categories to add to the tag"
        )

    @classmethod
    def run(cls, arguments: Namespace, config: Config) -> Iterator[Tag]:
        tag = tag_of(arguments.tag, config.notes_directory)
        to_add = OrderedDict()  # type: OrderedDict[Tag, Any]
        if arguments.prototype:
            prototype = tag_of(arguments.prototype, config.notes_directory)
            for category in prototype.categories():
                to_add.setdefault(category)
        for category_name in OrderedDict.fromkeys(arguments.categories).keys():
            category = tag_of(category_name, config.notes_directory)
            if category == tag:
                raise TagError(
                    "Cannot make tag a category of itself: '{}'".format(tag),
                    TagError.EXIT_UNSUPPORTED_OPERATION
                )
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
            "-i", "--indent",
            nargs="?",
            type=int,
            const=10,
            default=0,
            help="Indent output by a certain number of characters"
        )
        parser.add_argument(
            "-w", "--width",
            nargs="?",
            type=int,
            const=70,
            default=0,
            help="The max line length"
        )
        parser.add_argument(
            "tags", nargs="*", help="The tags to combine, else all"
        )

    @classmethod
    def default_sort_order(cls) -> Optional[bool]:
        return False

    @classmethod
    def run(cls, arguments: Namespace, config: Config) -> Iterator[Tag]:
        if arguments.tags:
            return AllTagsFrom(
                (
                    tag_of(name, config.notes_directory)
                    for name in set(arguments.tags)
                ),
                Note
            )
        else:
            return all_tags(config.notes_directory, Note)

    @classmethod
    def print(
            cls,
            member: Tag,
            indent_str: str = "",
            width: int = 0
            ) -> None:
        """
        Print the contents of a file with a header and a footer

        :param member: The Tag to print contents for
        :param indent_str: The text of the indentation to add to each line
        :param width: The max line length
        """
        with member.path().open() as f:
            print(
                textwrap_indent(
                    cls.HEADER.format(member.name), indent_str
                ),
                end=""
            )
            for line in f:
                if width:
                    line = textwrap_fill(
                        line,
                        width=width,
                        break_long_words=False,
                        break_on_hyphens=False,
                        replace_whitespace=False,
                        drop_whitespace=False
                    )
                line = textwrap_indent(line, indent_str)
                print(line, end="")
            print(
                textwrap_indent(
                    cls.FOOTER, indent_str
                ),
                end=""
            )

    @classmethod
    def format(
            cls,
            tags: Iterable[Tag],
            arguments: Namespace,
            config: Config,
            formatter: Type[Formatter]
            ) -> None:
        indent_str = " " * arguments.indent
        for tag in tags:
            cls.print(tag, indent_str=indent_str, width=arguments.width)


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
            "-d", "--diff", action="store_true",
            help="Run the diff editor on the last two files instead"
        )
        parser.add_argument(
            "tags", nargs="*", help="The tags to search, else all"
        )

    @classmethod
    def run(cls, arguments: Namespace, config: Config) -> Iterator[Tag]:
        if arguments.diff:
            check_external_command(config.diff, "diff editor")
        else:
            check_external_command(config.editor, "editor")
        if arguments.tags:
            tags = AllTagsFrom(
                (
                    tag_of(name, config.notes_directory)
                    for name in set(arguments.tags)
                ),
                Note
            )
        else:
            tags = all_tags(config.notes_directory, Note)
        return tags

    @classmethod
    def format(
            cls,
            tags: Iterable[Tag],
            arguments: Namespace,
            config: Config,
            formatter: Type[Formatter]
            ) -> None:
        last = None
        second_to_last = None
        for tag in tags:
            if last is None or tag > last:
                second_to_last = last
                last = tag

        if arguments.diff and last is not None and second_to_last is not None:
            command = [
                *config.diff, str(second_to_last.path()), str(last.path())
            ]
        elif not arguments.diff and last is not None:
            command = [*config.editor, str(last.path())]
        else:
            return

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
        """
        Get the stat file metadata of a particular file

        :param path: The path to get metadata about
        :return: The stat metadata
        """
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
    def run(cls, arguments: Namespace, config: Config) -> Iterator[Tag]:
        destinations = []
        for path in arguments.files:
            stat = cls.stat(path)
            if config.utc:
                timestamp = datetime.utcfromtimestamp(stat.st_mtime)
            else:
                timestamp = datetime.fromtimestamp(stat.st_mtime)
            note = Note.from_timestamp(timestamp, config.notes_directory)
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
        check_external_command(config.rsync, "rsync")
        return iter([])

    @classmethod
    def format(
            cls,
            tags: Iterable[Tag],
            arguments: Namespace,
            config: Config,
            formatter: Type[Formatter]
            ) -> None:
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
        check_external_command(config.rsync, "rsync")
        return iter([])

    @classmethod
    def format(
            cls,
            tags: Iterable[Tag],
            arguments: Namespace,
            config: Config,
            formatter: Type[Formatter]
            ) -> None:
        if config.utc:
            now = datetime.utcnow()
        else:
            now = datetime.now()
        subprocess_run(
            [
                *config.rsync,
                "-rtbv",
                "--suffix=.{}.bak".format(format_timestamp(now)),
                "{}/".format(config.notes_directory),
                "{}/".format(arguments.dest_directory)
            ]
        )


class Unknown(Command):
    NAME = "unknown"

    DESCRIPTION = "Show unknown files in the notes directory."

    @classmethod
    def name(cls) -> str:
        return cls.NAME

    @classmethod
    def description(cls) -> str:
        return cls.DESCRIPTION

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> None:
        parser.add_argument(
            "-r", "--relative",
            help="Print paths relative to notes directory",
            action="store_true"
        )

    @classmethod
    def run(cls, arguments: Namespace, config: Config) -> Iterator[Tag]:
        return iter([])

    @classmethod
    def format(
            cls,
            tags: Iterable[Tag],
            arguments: Namespace,
            config: Config,
            formatter: Type[Formatter]
            ) -> None:
        for path in all_non_tags(config.notes_directory):
            path = path.resolve()
            if arguments.relative:
                print(path.relative_to(config.notes_directory.resolve()))
            else:
                print(path)


class Reconcile(Command):
    NAME = "reconcile"

    DESCRIPTION = "Reconcile differences between backup files."

    @classmethod
    def name(cls) -> str:
        return cls.NAME

    @classmethod
    def description(cls) -> str:
        return cls.DESCRIPTION

    @classmethod
    def arguments(cls, parser: ArgumentParser) -> None:
        parser.add_argument(
            "tags", nargs="*", help="The tags to reconcile, else all"
        )

    @classmethod
    def run(cls, arguments: Namespace, config: Config) -> Iterator[Tag]:
        return iter([])

    @classmethod
    def backup_files_by_tag(
            cls, directory: Path, tags: Optional[Iterable[Tag]] = None
            ) -> Mapping[Tag, Sequence[Path]]:
        """
        Get all backup files, organized by the Tag the backup file belongs to.

        :param directory: The directory to search
        :param tags: The Tags to get backup files for
        :return: An ordered mapping of Tags to sorted sequences of backup file
                 paths
        """
        by_tag = {}  # type: MutableMapping[Tag, MutableSequence[Path]]
        if tags:
            good_tags = set(AllTagsFrom(tags))
        else:
            good_tags = set()
        for non_tag in all_non_tags(directory):
            try:
                tag_name, __, __ = parse_backup_file(
                    non_tag.name
                )
            except TagError as e:
                if e.exit_status == TagError.EXIT_BAD_NAME:
                    continue
                raise
            tag = tag_of(tag_name, directory)
            if not good_tags or tag in good_tags:
                if tag in by_tag:
                    index = bisect_left(by_tag[tag], non_tag)
                    by_tag[tag].insert(index, non_tag)
                else:
                    by_tag[tag] = [non_tag]
        by_tag = OrderedDict(sorted(by_tag.items(), key=lambda t: t[0]))
        return by_tag

    class Action(Enum):
        """
        An action to take during reconciliation
        """
        EDIT = 1
        NEXT = 2
        SKIP = 3
        QUIT = 4

    @classmethod
    def parse_action(cls, name: str) -> "Reconcile.Action":
        """
        Parse the action from a string

        :param name: The string action
        :return: The action to take
        """
        if name:
            for action in cls.Action:
                if action.name.lower().startswith(name):
                    return action
        raise TagError("Bad action: {}".format(name), TagError.EXIT_BAD_NAME)

    @classmethod
    def handle_note(
            cls, tag: Tag, path: Path, config: Config
            ) -> "Reconcile.Action":
        """
        Present a user interface for reconciling a backup file to a Tag,
        running the diff program and removing the backup file if all changes
        are reconciled.

        :param tag: The Tag to reconcile
        :param path: The backup file path
        :param config: The configuration to use
        :return: The next action to take
        """
        __, timestamp, __ = parse_backup_file(path.name)
        prompt = (
            "Tag: {} | Version: {} [(e)dit, (n)ext, (s)kip, (q)uit]? "
            "".format(tag.name, timestamp)
        )
        while True:
            try:
                action = cls.parse_action(input(prompt))
            except TagError as e:
                if e.exit_status == TagError.EXIT_BAD_NAME:
                    continue
                raise

            if action == cls.Action.EDIT:
                command = [
                    *config.diff, str(tag.path()), str(path)
                ]
                try:
                    subprocess_run(command, check=True)
                except (CalledProcessError, FileNotFoundError) as e:
                    raise TagError(
                        "Diff command {} failed.".format(command),
                        TagError.EXIT_EDITOR_FAILED
                    ) from e

                if cmp(str(tag.path()), str(path), shallow=False):
                    path.unlink()
                    return cls.Action.NEXT
            else:
                return action

    @classmethod
    def format(
            cls,
            tags: Iterable[Tag],
            arguments: Namespace,
            config: Config,
            formatter: Type[Formatter]
            ) -> None:
        while True:
            tags = [
                tag_of(tag, config.notes_directory) for tag in arguments.tags
            ]
            by_tag = cls.backup_files_by_tag(
                config.notes_directory, tags
            )
            if not by_tag:
                break

            for tag, paths in by_tag.items():
                for i in range(len(paths) - 1, -1, -1):
                    path = paths[i]
                    result = cls.handle_note(tag, path, config)
                    if result == cls.Action.NEXT:
                        continue
                    if result == cls.Action.SKIP:
                        break
                    if result == cls.Action.QUIT:
                        return


COMMANDS = (
    Add,
    Members,
    Categories,
    Show,
    Last,
    Remove,
    Import,
    Pull,
    Push,
    Unknown,
    Reconcile
)


def argument_parser() -> ArgumentParser:
    """
    Construct the command-line arguments of the program

    :return: The ArgumentParser for the program
    """
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
        "-t", "--time",
        action="append",
        help="One or more date ranges, inclusive, e.g. *-12 or 2018:2019"
    )
    parser.add_argument(
        "-n", "--name",
        action="append",
        help="One or more regexes all matching the tag name"
    )
    parser.add_argument(
        "-s", "--search",
        action="append",
        help="One or more regexes all matching the note text"
    )
    parser.add_argument(
        "-y", "--type",
        help="Return only [n]otes or [l]abels"
    )
    parser.add_argument(
        "-o", "--order",
        help="Sort notes [a]scending, [d]escending, or [n]one"
    )
    parser.add_argument(
        "-r", "--range",
        help="A range of results to show, as a slice"
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


def compile_regex(pattern: str) -> Pattern:
    """
    Wrap regex compilation in a TagError if it fails

    :param pattern: The string pattern to compile
    :return: The regex Pattern
    """
    try:
        regex = compile(pattern)
    except re_error as e:
        raise TagError(
            "Bad regex: '{}'".format(pattern), TagError.EXIT_BAD_REGEX
        ) from e
    return regex


def parse_type(type_: str) -> Type[Tag]:
    """
    Parse a Tag type passed as a string

    :param type_: The string type
    :return: The Tag type
    """
    if type_:
        for candidate in tag_types():
            if candidate.tag_type().startswith(type_):
                return candidate
    raise TagError("Bad type: '{}'", TagError.EXIT_BAD_TAG_TYPE)


def run_filters(results: Iterable[Tag], args: Namespace) -> Iterator[Tag]:
    """
    Run all applicable filters on the results of a Command

    :param results: The results to filter
    :param args: The command-line arguments
    :return: An Iterator with Tags filtered
    """
    filters = []

    if args.time:
        time_patterns = [DateRange.from_string(time) for time in args.time]

        def time(t: Tag) -> bool:
            if isinstance(t, Note):
                return any(
                    pattern.match(t.to_timestamp())
                    for pattern in time_patterns
                )
            return False

        filters.append(time)

    if args.name:
        name_patterns = [compile_regex(name) for name in args.name]

        def name(t: Tag) -> bool:
            return all(pattern.search(t.name) for pattern in name_patterns)

        filters.append(name)

    if args.search:
        search_patterns = [compile_regex(search) for search in args.search]

        def search(t: Tag) -> bool:
            return all(t.search_text(pattern) for pattern in search_patterns)

        filters.append(search)

    if args.type:
        type_class = parse_type(args.type)

        def type_(t: Tag) -> bool:
            return isinstance(t, type_class)

        filters.append(type_)

    def all_filters(t: Tag) -> bool:
        for filter_ in filters:
            if not filter_(t):
                return False
        return True

    results = (t for t in results if all_filters(t))
    return results


def parse_order(value: str) -> Optional[bool]:
    """
    Parse a sort order passed as a string

    :param value: The string order
    :return: None for no order, True for ascending, False for descending
    """
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


def parse_range(text: str) -> slice:
    """
    Parse a range passed as a string

    :param text: The string range
    :return: A slice
    """
    if not text.strip():
        raise TagError("Empty range", TagError.EXIT_BAD_RANGE)
    components = text.split(":")
    if len(components) > 3 or len(components) < 1:
        raise TagError("Bad range: '{}'".format(text), TagError.EXIT_BAD_RANGE)

    def try_int(value) -> int:
        try:
            return int(value)
        except (ValueError, TypeError) as e:
            raise TagError(
                "Bad range: '{}'".format(text), TagError.EXIT_BAD_RANGE
            ) from e

    if components[0]:
        start = try_int(components[0])
    else:
        start = 0
    if len(components) == 1:
        return slice(start, start + 1)
    if components[1]:
        end = try_int(components[1])
    else:
        end = -1
    if len(components) == 2:
        return slice(start, end)
    if components[2]:
        step = try_int(components[2])
    else:
        step = 1
    return slice(start, end, step)


def run_order_range(
        results: Iterable[Tag],
        args: Namespace,
        default_sort_order: Optional[bool] = None
        ) -> Iterator[Tag]:
    """
    Order and slice the results of a Command

    :param results: The Tags to order and slice
    :param args: The command-line arguments
    :param default_sort_order: The sort order
    :return: An Iterator of ordered and sliced Tags
    """
    order = default_sort_order
    if args.order:
        order = parse_order(args.order)
    if order is not None or args.range:
        results_list = list(results)
        if order is not None:
            results_list.sort(reverse=not order)
        if args.range:
            result_range = parse_range(args.range)
            results_list = results_list[result_range]
        results = iter(results_list)
    return results


def read_config_file(path: Path) -> Config:
    """
    Populate the configuration, optionally from a config file

    :param path: The path to the config file
    :return: The Config
    """
    try:
        with path.open() as file:
            config = Config(file)
    except FileNotFoundError:
        config = Config()
    return config


def run(args: Sequence[str]) -> None:
    """
    Run the program.

    :param args: The command-line arguments
    """
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
        results = run_order_range(results, args, command.default_sort_order())
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
    except KeyboardInterrupt:
        exit(1)
    except BrokenPipeError:
        exit(0)


def main():
    run(argv[1:])


if __name__ == "__main__":
    main()
