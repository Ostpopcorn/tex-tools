"""Bridge between the web app in the `web` folder and textools. The web app
runs this module in the browser with Pyodide and exchanges JSON strings with
it, and archives (.zip, .tar, .tar.gz) as bytes."""
import copy
import gzip
import io
import json
import re
import tarfile
import time
import zipfile
from dataclasses import asdict, fields

from .comments import Options, remove_comments

_OPTIONS = {_field.name for _field in fields(Options)}
# For the files that are excluded, which stay as they are
_UNCHANGED = Options(**{_name: False for _name in _OPTIONS})

# The files that the web app opens from archives and cleans, like TEX_FILE
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
    `web/app.js`. The files with "exclude" stay as they are."""
    request = json.loads(request_json)
    options = _options(request)
    files = []
    for _source in request["sources"]:
        result = remove_comments(_source["text"], _UNCHANGED
                                 if _source.get("exclude") else options)
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
    """Whether a file in an archive is a LaTeX file. The copies that macOS
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


def _gzip_name(data):
    """The name of the file in the header of gzip data, if it has one."""
    flags = data[3]
    if not flags & 8:
        return None
    start = 10 + (2 + int.from_bytes(data[10:12], "little") if flags & 4 else 0)
    return data[start:data.index(b"\0", start)].decode("latin-1")


def _unpack(data):
    """Return the kind of an archive ("zip" or "tar"), or "file" for a single
    gzipped file, its compression ("gz" or ""), and its entries as (name,
    info, content), with the ZipInfo or TarInfo of each entry, and the bytes
    of each file (None for folders and links). The source of arXiv is a
    .tar.gz, or a gzipped .tex file for a single file."""
    data = _bytes(data)
    compression = ""
    name = None
    if data[:2] == b"\x1f\x8b":
        name = _gzip_name(data)
        data, compression = gzip.decompress(data), "gz"
    if data[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return "zip", compression, [
                (_info.filename, _info,
                 None if _info.is_dir() else archive.read(_info))
                for _info in archive.infolist()]
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            return "tar", compression, [
                (_member.name, _member, archive.extractfile(_member).read()
                 if _member.isfile() else None)
                for _member in archive.getmembers()]
    except tarfile.ReadError:
        if compression:
            return "file", compression, [(name, None, data)]
        raise ValueError("It is not a .zip, .tar, or .tar.gz file.")


def read_archive(data):
    """Return the LaTeX files of an archive (.zip, .tar, or .tar.gz), as
    JSON, with its kind and compression, see `_unpack`, and the number of
    other files, which are kept as they are. A gzipped file is one file,
    with the name in its header (or None)."""
    try:
        kind, compression, entries = _unpack(data)
    except zipfile.BadZipFile:
        return json.dumps({"error": "It is not a zip file."})
    except (ValueError, OSError, EOFError, tarfile.TarError, RuntimeError,
            NotImplementedError) as err:
        # e.g., a broken gzip file, an encrypted zip file, or an unsupported
        # compression
        return json.dumps({"error": str(err)})
    files = []
    others = 0
    for _name, _info, _content in entries:
        if _content is None:
            continue
        if kind != "file" and not is_tex(_name):
            others += 1
            continue
        if _content.startswith(b"%PDF"):
            return json.dumps({"error": "It is a PDF, not LaTeX."})
        text, encoding, bom = _decode(_content)
        files.append({"name": _name, "text": text, "encoding": encoding,
                      "bom": bom})
    return json.dumps({"kind": kind, "compression": compression,
                       "files": files, "others": others})


def _zip_info(name, info):
    """The ZipInfo of a copy of an entry of a zip or tar file, with its
    name, date, and permissions, or of a new file (info is None)."""
    if isinstance(info, zipfile.ZipInfo):
        copy = zipfile.ZipInfo(info.filename, info.date_time)
        copy.create_system = info.create_system
        copy.external_attr = info.external_attr
        stored = info.is_dir() or info.compress_type == zipfile.ZIP_STORED
    elif isinstance(info, tarfile.TarInfo):
        folder = info.isdir()
        date = max(time.localtime(info.mtime)[:6], (1980, 1, 1, 0, 0, 0))
        copy = zipfile.ZipInfo(name.rstrip("/") + ("/" if folder else ""), date)
        copy.external_attr = ((info.mode | (0o040000 if folder else 0o100000))
                              << 16) | (0x10 if folder else 0)
        stored = folder
    else:
        copy = zipfile.ZipInfo(name, time.localtime()[:6])
        copy.external_attr = 0o644 << 16
        stored = False
    copy.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    return copy


def _tar_info(name, info, size):
    """The TarInfo of a copy of an entry of a tar file, with its name, date,
    owner, and permissions, or of a new file."""
    if isinstance(info, tarfile.TarInfo):
        copy_ = copy.copy(info)
    else:
        copy_ = tarfile.TarInfo(name)
        copy_.mtime = int(time.time())
        copy_.mode = 0o644
    if size is not None:
        copy_.size = size
    return copy_


def write_archive(request_json, *archives):
    """Return an archive (bytes) with the files of a request, each with its
    name (a path), text, encoding ("utf-8" or "latin-1"), and BOM. The
    entries of the archives (bytes) are copied in their order, with the files
    of the request in place of the files with the same name. The other files
    of the request are added at the end. The format of the request is "zip"
    (the default), "tar", or "tar.gz"."""
    request = json.loads(request_json)
    files = {_file["name"]: _file for _file in request["files"]}
    entries = []
    written = set()
    for _archive in archives:
        for _name, _info, _content in _unpack(_archive)[2]:
            if _name in written:
                continue
            written.add(_name)
            source = files.get(_name)
            if source is not None and _content is not None:
                _content = _encode(source)
            entries.append((_name, _info, _content))
    entries += [(_name, None, _encode(_source)) for _name, _source
                in files.items() if _name not in written]

    data = io.BytesIO()
    format_ = request.get("format", "zip")
    if format_ in ("tar", "tar.gz"):
        mode = "w:gz" if format_ == "tar.gz" else "w"
        with tarfile.open(fileobj=data, mode=mode) as out:
            for _name, _info, _content in entries:
                # only tar files have folders and links here
                if _content is None and not isinstance(_info, tarfile.TarInfo):
                    continue
                size = None if _content is None else len(_content)
                out.addfile(_tar_info(_name, _info, size),
                            None if _content is None else io.BytesIO(_content))
    else:
        with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as out:
            for _name, _info, _content in entries:
                # links of tar files cannot be in a zip file
                if _content is None and not (
                        isinstance(_info, zipfile.ZipInfo) or _info.isdir()):
                    continue
                out.writestr(_zip_info(_name, _info), _content or b"")
    return data.getvalue()
