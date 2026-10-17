from store.cli import main


def test_cli_books_and_rows(conn, tmp_path, capsys):
    db = tmp_path / "lib.db"
    conn.execute(f"VACUUM INTO '{db}'")
    assert main([str(db), "books", "--author", "Lea Moss"]) == 0
    assert "Paper Harbour" in capsys.readouterr().out
    assert main([str(db), "rows", "members"]) == 0
    assert "kim@example.com" in capsys.readouterr().out
