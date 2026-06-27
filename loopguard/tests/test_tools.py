from loopguard.tools import LIST_DIR_SCHEMA, READ_FILE_SCHEMA, TOOLS, list_dir, read_file


def test_read_existing_file(tmp_path):
    (tmp_path / "hello.txt").write_text("hi there")
    assert read_file("hello.txt", root=tmp_path) == "hi there"


def test_missing_file_returns_error(tmp_path):
    out = read_file("package.json", root=tmp_path)
    assert out.startswith("Error:") and "not found" in out


def test_path_escape_is_blocked(tmp_path):
    out = read_file("../../etc/passwd", root=tmp_path)
    assert out.startswith("Error:") and "escapes" in out


def test_list_dir_lists_real_entries(tmp_path):
    (tmp_path / "pyproject.toml").write_text("x")
    (tmp_path / "sub").mkdir()
    out = list_dir(".", root=tmp_path)
    assert "pyproject.toml" in out
    assert "sub/" in out  # directories are marked with a trailing slash


def test_list_dir_escape_is_blocked(tmp_path):
    out = list_dir("../..", root=tmp_path)
    assert out.startswith("Error:") and "escapes" in out


def test_schema_and_registry_shapes():
    assert READ_FILE_SCHEMA["function"]["name"] == "read_file"
    assert LIST_DIR_SCHEMA["function"]["name"] == "list_dir"
    assert "read_file" in TOOLS and callable(TOOLS["read_file"])
    assert "list_dir" in TOOLS and callable(TOOLS["list_dir"])
