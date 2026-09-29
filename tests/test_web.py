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
