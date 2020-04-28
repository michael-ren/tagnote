Tagnote: Minimalist Note Organization
=====================================

Installation
------------
If installing from PyPI::

    $ pip3 install tagnote

If installing from source::

    $ python3 setup.py sdist
    $ pip3 install -U dist/tagnote-$VERSION.$FORMAT

To create and edit notes, Tagnote needs a text editor with an appropriate plugin. Currently, Tagnote only supports ``vim``, so you will need to make sure that ``vim`` is installed.

To install the ``vim`` plugin, see the repository_ for instructions.

For synchronizing notes, Tagnote requires the ``rsync`` command. Install ``rsync`` if you need to synchronize notes.

.. _repository: https://github.com/michael-ren/tagnote-vim

Introduction
------------

The first problem with note-taking software is you might not realize it's a note until after it's been written.

Normally, notes like this would exist as a collection of text files with gibberish names in several unrelated directories. Tagnote allows you to do this::

    :W
    "~/notes/2018-07-11_19-37-44.txt" [New] *L, *C written

There's no need to think of a name right now, and Tagnote preserves the most important metadata, the creation date, for you. There's no need to open up a separate note application, copy and paste the contents of the note, and then put the note in the right place. Tagnote is for when you want to get your idea out first and organize it later.

Working with Labels
-------------------

If, after the fact, you wish to add a label, you can do this::

    :A groceries

The Tagnote command can also do this if you know the name of the note::

    $ tag add 2018-07-11_19-37-44.txt groceries

You can then open the note afterwards by label::

    $ tag last groceries

This opens the note in a text editor. If you make changes to the note, you can either save them normally or create a copy of the note::

    :W
    "~/notes/2018-07-11_19-45-04.txt" [New] *L, *C written

This command remembers the current note's labels and applies them to the copy. When you run ``tag last groceries`` again, Tagnote displays the most recent note, ``2018-07-11_19-45-04.txt``.

You can also edit the previous note::

    $ tag -r 1: last groceries

This passes the ``-r`` flag, which filters out the latest note using the slice ``1:``.

Instead of switching between these two notes manually, you can see their changes by running a diff on them::

    $ tag last -d groceries

This opens up a diff editor with the changes highlighted.

Summary Commands
----------------

You can see all members of the ``groceries`` label::

    $ tag members groceries

This lists all members of ``groceries`` in sorted order. Sorting puts the notes in chronological order because of the way Tagnote names notes.

This is handy for reporting::

    $ tag show groceries | less

This prints out the content of the notes as well as a formatted header and footer around each note. This is great for getting a summary of the material in each label. By default, ``show`` puts notes in reverse chronological order.

You can also get a forward chronological account::

    $ tag -o a show groceries | less

The ``-o`` flag takes the ``a`` argument which is short for "ascending."

You can confirm that a note is in fact a member of a label::

    $ tag categories 2018-07-11_19-45-04.txt

This prints the labels the note is a part of, in this case ``groceries``.

You can also list all top-level tags::

    $ tag members

You'll notice that this only includes ``groceries`` and not the two notes. This is because the ``members`` command only lists immediate children, and since ``groceries`` is now the parent of the two notes, those two notes are not members of the top level.

The ``show`` command works differently::

    $ tag show | less

This lists all notes in reverse chronological order, even if they are children of another label. ``show`` can also show notes from several labels::

    :A foods

    $ tag show groceries foods

Even if a note is a member of several labels, ``show`` shows each note only once.

Important Filters
-----------------

You can also ask Tagnote to search for text inside notes directly::

    $ tag -s lentils -s kale show | less

This only shows notes that have both the text ``lentils`` and the text ``kale`` in them.

Similarly, you can search by date range::

    $ tag -t 2018-07-11_19-40: -t 2005-*-29:2007 show

This shows notes created at 7:40 PM on July 11th, 2018 or later or notes created in 2005, 2006, or 2007 on the 29th, 30th, or 31st of each month, which means the command only includes the ``2018-07-11_19-45-04.txt`` note.

Dealing with Remotes
--------------------

You can back up your notes to another location::

    $ tag push michael@my-server:notes

This copies the directory containing your notes, by default ``~/notes``, to the remote location. The remote location can be anything ``rsync`` accepts as a destination. Unlike in ``rsync``, the name you pass in the command is always the name of the immediate directory containing the notes. In this example, even if there is no trailing slash, the destination directory is never ``notes/notes``.

To change the directory Tagnote stores your notes, see the `Configuration`_ section below. This doesn't move notes that already exist; use ``tag push`` to copy them over before changing the directory.

When synchronizing between several copies of the notes, sometimes you need to copy a remote source into your notes directory::

    $ tag pull michael@my-server:notes

This works the same way as ``tag push`` except in the opposite direction.

When there are conflicts, ``tag push`` and ``tag pull`` create backup files like ``2018-07-11_19-45-04.txt.2018-07-11_21-10-24.bak``. Tagnote indicates the conflicted file and adds the timestamp when you ran ``tag push`` or ``tag pull`` as well as the ``.bak`` extension. The new version exists as ``2018-07-11_19-45-04.txt``, and the old version is the backup file named above.

If you want to accept all changes after a ``push`` or a ``pull``, simply delete all ``.bak`` files::

    $ find ~/notes -name '*.bak' -delete

If there are changes you would like to keep, Tagnote provides a wizard to incorporate changes from ``.bak`` files::

    $ tag reconcile

This prints the conflicted file and the timestamp of the backup file and then prompts for an action. You can run the diff editor to reconcile changes by choosing the ``edit`` action. If you close the diff editor and the conflicted file and its backup are the same, ``reconcile`` deletes the backup file. To go to the next backup file, choose the ``next`` action. To go to the next conflicted file, choose ``skip``. ``quit`` exits the ``reconcile`` command at any point.

In addition to ``.bak`` files, the editor might generate other files, such as ``.swp`` files, that the editor must resolve manually. To list all files that Tagnote doesn't recognize, run::

    $ tag unknown

This prints out all such files.

Note Lifecycle
--------------

You can import a pre-existing file as a note::

    $ tag import asdlfkjfs.txt

This uses the modification time for the name of the note.

You can also remove tags and associations between tags::

    $ tag remove 2018-07-11_19-45-04.txt groceries
    $ tag remove 2018-07-11_19-45-04.txt

You must remove all associations for a tag before removing the tag itself.

Configuration
-------------

Tagnote uses a configuration file for various options. By default, the configuration file exists at ``~/.tag.config.json``. You can change this by passing a different value to the ``-c`` flag on the command line::

    tag -c ~/other-tag.config.json ...

The configuration file is a JSON object that maps string configuration options to configuration values.

By default, the notes directory is at ``~/notes``. To change the notes directory, use the ``notes_directory`` option::

    {
    ...
    "notes_directory": "Documents/notes",
    ...
    }

Note that the value of ``notes_directory`` is relative to the home directory.

By default, the editor is ``vim`` with no arguments. To change the editor, use the ``editor`` option::

    {
    ...
    "editor": ["vim", "-n"],
    ...
    }

You can also set the editor using environment variables. In order, Tagnote prioritizes ``TAGNOTE_EDITOR``, then ``VISUAL``, and then ``EDITOR``. You cannot pass editor command arguments using environment variables, and the value in the configuration file takes priority over the environment variables.

Similarly, the default diff editor is ``vimdiff`` with no arguments. To change the editor, use the ``diff`` option::

    {
    ...
    "diff": ["meld"],
    ...
    }

You can also set the diff editor using the ``TAGNOTE_DIFF`` environment variable. You cannot pass diff editor command arguments using environment variables, and the value in the configuration file takes priority over the environment variable.

By default, the rsync program is ``rsync``. To change the rsync command, use the ``rsync`` option::

    {
    ...
    "rsync": ["/usr/local/bin/rsync"],
    ...
    }

The ``TAGNOTE_RSYNC`` environment variable also sets the rsync command and is lower priority than the value in the configuration file.

By default, notes use local time for timestamps. To use UTC, update the config file::

    {
    ...
    "utc": true,
    ...
    }
