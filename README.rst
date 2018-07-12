Tagnote: Minimalist Note Organization
=====================================

Replace files like "asdflajsdf" in vim with timestamped .txt files::

    :W
    "~/notes/2018-07-11_19-37-44.txt" [New] *L, *C written

Categorize those files using arbitrarily linked labels::

    $ tag add todo
    todo
    $ tag members
    todo
    $ tag add 2018-07-11_19-37-44.txt todo
    $ tag members todo
    2018-07-11_19-37-44.txt

Installation
------------
If installing from PyPI::

    pip3 install tagnote

If installing from source::

    python3 setup.py sdist
    pip3 install -U dist/tagnote-$VERSION.$FORMAT

To install the vim plugin, copy ``tagnote.vim`` to the ``~/.vim/plugin`` directory, creating the directory if needed.

Note-taking
-----------

::

    $ tag add meeting_minutes
    meeting_minutes
    $
    $ vim
    A meeting was held.
    :W meeting_minutes
    "~/notes/2018-07-11_19-55-08.txt" [New] *L, *C written
    :q
    $
    $ vim
    A second meeting was held.
    :W meeting_minutes
    "~/notes/2018-07-11_19-55-29.txt" [New] *L, *C written
    :q
    $
    $ tag members meeting_minutes
    2018-07-11_19-55-08.txt
    2018-07-11_19-55-29.txt
    $ tag show meeting_minutes
    2018-07-11_19-55-29.txt
    ---
    A second meeting was held.

    ***
    2018-07-11_19-55-08.txt
    ---
    A meeting was held.

    ***
    $ tag -o a show meeting_minutes
    2018-07-11_19-55-08.txt
    ---
    A meeting was held.

    ***
    2018-07-11_19-55-29.txt
    ---
    A second meeting was held.

    ***

Todo List
---------

::

    $ tag add todo
    todo
    $
    $ vim
    - buy groceries
    :W todo
    "~/notes/2018-07-11_20-06-35.txt" [New] *L, *C written
    :q
    $
    $ tag last todo
    - buy groceries
    - walk around
    :wq
    $
    $ tag last todo
    - buy groceries
    - walk around
    - garden
    :W todo
    "~/notes/2018-07-11_20-11-04.txt" [New] *L, *C written
    :q
    $
    $ tag last todo
    - buy groceries
    - walk around
    - garden
    :q
    $ tag show todo
    2018-07-11_20-11-04.txt
    ---
    - buy groceries
    - walk around
    - garden

    ***
    2018-07-11_20-06-35.txt
    ---
    - buy groceries
    - walk around

    ***

Bookmarks
---------

::

    $ tag add bookmarks
    bookmarks
    $
    $ vim
    https://www.python.org/
    :W bookmarks
    "~/notes/2018-07-11_20-15-25.txt" [New] *L, *C written
    :q
    $
    $ tag -s python show bookmarks
    2018-07-11_20-15-25.txt
    ---
    https://www.python.org/

    ***

UTC
---

By default, notes use local time for timestamps. To use UTC, update ``~/.tag.config.json``::

    {
    ...
    "utc": true
    }

Also update ``~/.vim/plugin/tagnote.vim``::

    ...
    let UTC = 1
    ...

