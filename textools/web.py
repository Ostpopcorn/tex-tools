"""Bridge between the web app in the `web` folder and textools. The web app
runs this module in the browser with Pyodide and exchanges JSON strings with
it, and zip files as bytes."""
import io
import json
import re
import time
import zipfile
from dataclasses import asdict, fields

from .comments import Options, remove_comments

_OPTIONS = {_field.name for _field in fields(Options)}

# The files that the web app opens from zip files and cleans, like TEX_FILE
# in web/app.js
_TEX_FILE = re.compile(r"\.(tex|ltx|sty|cls|dtx|ins|bbx|cbx|lbx|tikz|pgf)$",
                       re.IGNORECASE)
_BOM = b"\xef\xbb\xbf"


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


def _bytes(data):
    """Bytes from Python, or from a Uint8Array of the web app."""
    return data.to_bytes() if hasattr(data, "to_bytes") else bytes(data)


def is_tex(name):
    """Whether a file in a zip file is a LaTeX file. The copies that macOS
    adds to zip files, e.g., __MACOSX/._main.tex, are not."""
    base = name.rsplit("/", 1)[-1]
    return (bool(_TEX_FILE.search(name)) and not base.startswith("._")
            and not name.startswith("__MACOSX/"))


def _decode(data):
    """Read a file as UTF-8, or else as Latin-1, like the web app."""
    bom = data.startswith(_BOM)
    try:
        return data[len(_BOM) if bom else 0:].decode("utf-8"), "utf-8", bom
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1", False


def _encode(source):
    data = source["text"].encode(source.get("encoding", "utf-8"))
    return _BOM + data if source.get("bom") else data


def read_zip(data):
    """Return the LaTeX files of a zip file and the number of other files,
    which are kept as they are, as JSON."""
    files = []
    others = 0
    try:
        with zipfile.ZipFile(io.BytesIO(_bytes(data))) as archive:
            for _info in archive.infolist():
                if _info.is_dir():
                    continue
                if not is_tex(_info.filename):
                    others += 1
                    continue
                text, encoding, bom = _decode(archive.read(_info))
                files.append({"name": _info.filename, "text": text,
                              "encoding": encoding, "bom": bom})
    except zipfile.BadZipFile:
        return json.dumps({"error": "It is not a zip file."})
    except (RuntimeError, NotImplementedError) as err:
        # e.g., an encrypted file or an unsupported compression
        return json.dumps({"error": str(err)})
    return json.dumps({"files": files, "others": others})


def _copy_info(info):
    """The name, date, and permissions of a file in a zip file, for a copy
    of it in another zip file."""
    copy = zipfile.ZipInfo(info.filename, info.date_time)
    copy.create_system = info.create_system
    copy.external_attr = info.external_attr
    copy.compress_type = (zipfile.ZIP_STORED if info.is_dir()
                          or info.compress_type == zipfile.ZIP_STORED
                          else zipfile.ZIP_DEFLATED)
    return copy


def zip_files(request_json, *archives):
    """Return a zip file (bytes) with the files of a request, each with its
    name (a path), text, and encoding ("utf-8" or "latin-1"). The files of
    the zip files in `archives` are copied in their order, with the files of
    the request in place of the files with the same name. The other files of
    the request are added at the end."""
    files = {_file["name"]: _file for _file in json.loads(request_json)["files"]}
    written = set()
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as out:
        for _archive in archives:
            with zipfile.ZipFile(io.BytesIO(_bytes(_archive))) as archive:
                for _info in archive.infolist():
                    if _info.filename in written:
                        continue
                    written.add(_info.filename)
                    source = files.get(_info.filename)
                    out.writestr(_copy_info(_info), _encode(source) if source
                                 else archive.read(_info))
        for _name, _source in files.items():
            if _name not in written:
                info = zipfile.ZipInfo(_name, time.localtime()[:6])
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                out.writestr(info, _encode(_source))
    return data.getvalue()
