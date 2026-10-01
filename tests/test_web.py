import gzip
import io
import json
import tarfile
import zipfile

from textools import web


def _run(request):
    return json.loads(web.run(json.dumps(request)))


def test_defaults():
    defaults = json.loads(web.defaults())
    assert defaults["empty_comments"] and defaults["keep_verbatim"]


def test_run():
    response = _run({"sources": [{"name": "a.tex", "text": "a % x\n% y\nb\n"},
                                 {"name": "b.tex", "text": "\\verb|%|\n"}],
                     "options": {"unknown": True}})
    first, second = response["files"]
    assert first["text"] == "a\nb\n" and first["count"] == 2
    assert first["comments"] == 2
    assert first["lines"] == [[0, 1, "comment"], [None, 0, "comment line"],
                              [1, 1, ""]]
    assert second["kept"] == [{"kind": "inline", "lines": [0, 0],
                               "name": "\\verb"}]
    assert second["messages"] == [["info", "Kept the % in 1 inline verbatim."]]


def test_run_excluded_file():
    text = "% x\n\\verb|%| a % y\n"
    response = _run({"sources": [{"name": "a.tex", "text": text,
                                  "exclude": True},
                                 {"name": "b.tex", "text": text}],
                     "options": {}})
    excluded, cleaned = response["files"]
    assert excluded["text"] == text
    assert excluded["comments"] == 0 and excluded["count"] == 2
    assert excluded["kept"] == [] and excluded["messages"] == []
    assert excluded["lines"] == [[0, 3, ""], [1, 14, ""]]
    assert cleaned["comments"] == 2


def test_run_with_steps_off():
    response = _run({"sources": [{"name": "a.tex", "text": "a % x\n"}],
                     "options": {"empty_comments": False,
                                 "space_before": False}})
    assert response["files"][0]["text"] == "a % x\n"


def test_zip_files():
    data = web.write_archive(json.dumps({"files": [
        {"name": "a.tex", "text": "ä\n", "encoding": "utf-8", "bom": True},
        {"name": "b.tex", "text": "ä\n", "encoding": "latin-1"}]}))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.read("a.tex") == b"\xef\xbb\xbf" + "ä\n".encode("utf-8")
        assert archive.read("b.tex") == b"\xe4\n"


def _project():
    """A zip file like one from Overleaf, with a .tex file in a folder, an
    image, and the copies that macOS adds."""
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("main.tex", "\\input{sections/intro} % x\n")
        archive.writestr("sections/", b"")
        archive.writestr(zipfile.ZipInfo("sections/intro.tex",
                                         (2020, 1, 2, 3, 4, 6)),
                         b"Caf\xe9 % x\r\n")
        archive.writestr(zipfile.ZipInfo("figures/plot.png"), b"\x89PNG%")
        archive.writestr("__MACOSX/._main.tex", b"\x00\x05")
    return data.getvalue()


def test_read_zip():
    response = json.loads(web.read_archive(_project()))
    assert response == {"kind": "zip", "compression": "", "others": 2, "files": [
        {"name": "main.tex", "text": "\\input{sections/intro} % x\n",
         "encoding": "utf-8", "bom": False},
        {"name": "sections/intro.tex", "text": "Caf\xe9 % x\r\n",
         "encoding": "latin-1", "bom": False}]}
    assert "error" in json.loads(web.read_archive(b"not a zip file"))


def test_zip_files_keeps_the_other_files():
    project = _project()
    files = json.loads(web.read_archive(project))["files"]
    response = _run({"sources": files, "options": {}})
    for _file, _result in zip(files, response["files"]):
        _file["text"] = _result["text"]
    files.append({"name": "new.tex", "text": "new\n"})
    data = web.write_archive(json.dumps({"files": files}), project)
    with zipfile.ZipFile(io.BytesIO(project)) as before, \
            zipfile.ZipFile(io.BytesIO(data)) as after:
        assert after.namelist() == before.namelist() + ["new.tex"]
        assert after.read("main.tex") == b"\\input{sections/intro}\n"
        assert after.read("sections/intro.tex") == b"Caf\xe9\r\n"
        assert after.getinfo("sections/intro.tex").date_time == \
            (2020, 1, 2, 3, 4, 6)
        assert after.getinfo("sections/").is_dir()
        for name in ("figures/plot.png", "__MACOSX/._main.tex"):
            assert after.read(name) == before.read(name)
        assert after.read("new.tex") == b"new\n"


def _arxiv():
    """A .tar.gz like the source of a paper on arXiv."""
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        for name, content, mode in [
                ("./", None, 0o755),
                ("./main.tex", b"\\input{sec/intro} % x\n", 0o644),
                ("./sec", None, 0o755),
                ("./sec/intro.tex", b"Intro. % y\n", 0o600),
                ("./fig.pdf", b"%PDF-1.5 %\xe2\xe3", 0o644)]:
            info = tarfile.TarInfo(name)
            info.mtime, info.mode, info.uname = 1700000000, mode, "arxiv"
            if content is None:
                info.type = tarfile.DIRTYPE
            else:
                info.size = len(content)
            archive.addfile(info, None if content is None
                            else io.BytesIO(content))
        link = tarfile.TarInfo("./figure.pdf")
        link.type, link.linkname = tarfile.SYMTYPE, "fig.pdf"
        archive.addfile(link)
    return data.getvalue()


def test_read_tar_gz():
    response = json.loads(web.read_archive(_arxiv()))
    assert response["kind"] == "tar" and response["compression"] == "gz"
    assert [f["name"] for f in response["files"]] == ["./main.tex",
                                                      "./sec/intro.tex"]
    assert response["others"] == 1


def test_write_tar_gz_keeps_the_other_files():
    source = _arxiv()
    files = json.loads(web.read_archive(source))["files"]
    response = _run({"sources": files, "options": {}})
    for _file, _result in zip(files, response["files"]):
        _file["text"] = _result["text"]
    data = web.write_archive(json.dumps({"files": files,
                                         "format": "tar.gz"}), source)
    assert data[:2] == b"\x1f\x8b"
    with tarfile.open(fileobj=io.BytesIO(source)) as before, \
            tarfile.open(fileobj=io.BytesIO(data)) as after:
        assert after.getnames() == before.getnames()
        assert after.extractfile("./main.tex").read() == \
            b"\\input{sec/intro}\n"
        assert after.extractfile("./sec/intro.tex").read() == b"Intro.\n"
        assert after.extractfile("./fig.pdf").read() == \
            before.extractfile("./fig.pdf").read()
        for name in before.getnames():
            old, new = before.getmember(name), after.getmember(name)
            assert (new.mode, new.mtime, new.uname, new.type, new.linkname) \
                == (old.mode, old.mtime, old.uname, old.type, old.linkname)


def test_write_zip_from_tar_and_zip():
    data = web.write_archive(json.dumps({"files": []}), _arxiv(), _project())
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        # the link of the tar file cannot be in a zip file
        assert names[:4] == ["./", "./main.tex", "./sec/", "./sec/intro.tex"]
        assert "./figure.pdf" not in names and "main.tex" in names
        assert archive.getinfo("./sec/").is_dir()
        assert archive.getinfo("./sec/intro.tex").external_attr >> 16 == \
            0o100600


def test_read_gzipped_file():
    # a single file on arXiv is a gzipped .tex file, with or without a name
    data = gzip.compress(b"a % b\n")
    response = json.loads(web.read_archive(data))
    assert response["kind"] == "file" and response["others"] == 0
    assert response["files"] == [{"name": None, "text": "a % b\n",
                                  "encoding": "utf-8", "bom": False}]
    named = io.BytesIO()
    with gzip.GzipFile("2401.00001v1.tex", "wb", fileobj=named) as _file:
        _file.write(b"a % b\n")
    response = json.loads(web.read_archive(named.getvalue()))
    assert response["files"][0]["name"] == "2401.00001v1.tex"


def test_read_archive_errors():
    assert "PDF" in json.loads(web.read_archive(
        gzip.compress(b"%PDF-1.5\n")))["error"]
    assert "error" in json.loads(web.read_archive(b"\x1f\x8b broken"))
    assert "not a .zip" in json.loads(web.read_archive(b"plain text"))["error"]
