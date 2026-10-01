import io
import json
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
    data = web.zip_files(json.dumps({"files": [
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
    response = json.loads(web.read_zip(_project()))
    assert response == {"others": 2, "files": [
        {"name": "main.tex", "text": "\\input{sections/intro} % x\n",
         "encoding": "utf-8", "bom": False},
        {"name": "sections/intro.tex", "text": "Caf\xe9 % x\r\n",
         "encoding": "latin-1", "bom": False}]}
    assert "error" in json.loads(web.read_zip(b"not a zip file"))


def test_zip_files_keeps_the_other_files():
    project = _project()
    files = json.loads(web.read_zip(project))["files"]
    response = _run({"sources": files, "options": {}})
    for _file, _result in zip(files, response["files"]):
        _file["text"] = _result["text"]
    files.append({"name": "new.tex", "text": "new\n"})
    data = web.zip_files(json.dumps({"files": files}), project)
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
