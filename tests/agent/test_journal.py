from journal import journal_excerpt


def test_verification_excerpt_is_the_tail() -> None:
    stderr = "x" * 50 + "\nerror: unknown type name 'Foo'\n"
    excerpt = journal_excerpt("build_step", stderr, "ignored")
    assert excerpt.endswith("error: unknown type name 'Foo'")
    assert excerpt.startswith("x")
    assert len(excerpt) <= 200
    assert "ignored" not in excerpt


def test_read_excerpt_is_the_first_line() -> None:
    stderr = "no such file\nsecond line that must not win"
    assert journal_excerpt("read_file", stderr, "") == "no such file"


def test_empty_stderr_uses_stdout() -> None:
    assert journal_excerpt("search", "  \n", "no matches") == "no matches"
