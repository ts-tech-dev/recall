"""HTTP API, one router per area. Each module defines `router`; app.py includes them all.

    status.py   status, settings, re-index
    files.py    file tree, folders, previews, raw files and cached images
    search.py   search, related notes, graph
    edit.py     reading/saving sources, new notes, moving, task checkboxes, uploads, versions
    ask.py      most relevant passages for a question (server-sent events)
"""

from . import ask, edit, files, search, status

routers = [status.router, files.router, search.router, edit.router, ask.router]
