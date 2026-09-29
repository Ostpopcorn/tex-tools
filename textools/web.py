"""Bridge between the web app in the `web` folder and textools. The web app
runs this module in the browser with Pyodide and exchanges JSON strings with
it."""
import io
import json
import zipfile
from dataclasses import asdict, fields

from .comments import Options, remove_comments

_OPTIONS = {_field.name for _field in fields(Options)}


def defaults():
    """Return the default options of the web app."""
    return json.dumps(asdict(Options()))


def _options(request):
    return Options(**{_key: bool(_value)
                      for _key, _value in request.get("options", {}).items()
                      if _key in _OPTIONS})


def run(request_json):
    """Remove the comments of the files of a request of the web app, see
    `web/app.js`."""
    request = json.loads(request_json)
    options = _options(request)
    files = []
    for _source in request["sources"]:
        result = remove_comments(_source["text"], options)
        files.append({
            "text": result.text,
            "count": result.count,
            "comments": result.comments,
            # for each line of the input: its line in the output (or None),
            # the number of characters kept, and why it was changed
            "lines": [[_line.out, _line.kept, _line.reason]
                      for _line in result.lines],
            "kept": [{"kind": _kept.kind, "lines": list(_kept.lines),
                      "name": _kept.name} for _kept in result.kept],
            "messages": result.messages,
        })
    return json.dumps({"files": files})


def zip_files(request_json):
    """Return a zip file (bytes) with the files of a request, each with its
    name, text, and encoding ("utf-8" or "latin-1")."""
    request = json.loads(request_json)
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        for _file in request["files"]:
            encoded = _file["text"].encode(_file.get("encoding", "utf-8"))
            if _file.get("bom"):
                encoded = b"\xef\xbb\xbf" + encoded
            archive.writestr(_file["name"], encoded)
    return data.getvalue()
