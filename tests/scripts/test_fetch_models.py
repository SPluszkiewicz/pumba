"""Unit tests for `scripts/fetch_models.py`.

These deliberately never touch the network: `_download` (or the
`hf_hub_download` it delegates to) is monkeypatched out in every test that
would otherwise trigger it. The behaviors under test
are the ones that actually matter for a model-integrity script: checksum
verification, the "already present" fast path, `--force`, and cleanup of
corrupt downloads.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import fetch_models


@pytest.fixture(autouse=True)
def _models_dir(tmp_path, monkeypatch):
    """Redirect MODELS_DIR to an isolated tmp dir for every test."""
    monkeypatch.setattr(fetch_models, "MODELS_DIR", tmp_path)
    return tmp_path


def _spec(**overrides) -> fetch_models.ModelSpec:
    defaults = dict(
        name="unit-test",
        repo_id="example/unit-test-GGUF",
        filename="model.gguf",
        sha256=None,
    )
    defaults.update(overrides)
    return fetch_models.ModelSpec(**defaults)


def test_spec_url_is_the_hub_resolve_url():
    assert _spec().url == "https://huggingface.co/example/unit-test-GGUF/resolve/main/model.gguf"
    assert fetch_models.CHAT_MODEL.url.endswith("/resolve/main/Qwen2.5-Coder-14B-Instruct-Q5_K_M.gguf")


def test_download_delegates_to_hf_hub_download_with_local_dir(tmp_path, monkeypatch):
    """The transfer itself is huggingface_hub's job; we only check the call."""
    calls = []

    def _fake_hf_hub_download(**kwargs):
        calls.append(kwargs)
        target = tmp_path / kwargs["filename"]
        target.write_bytes(b"payload")
        return str(target)

    monkeypatch.setattr(fetch_models, "hf_hub_download", _fake_hf_hub_download)

    fetch_models._download(_spec(), tmp_path / "model.gguf", force=True)

    assert calls == [
        {
            "repo_id": "example/unit-test-GGUF",
            "filename": "model.gguf",
            "local_dir": str(tmp_path),
            "force_download": True,
        }
    ]
    assert (tmp_path / "model.gguf").read_bytes() == b"payload"


def test_download_moves_file_if_hub_wrote_elsewhere(tmp_path, monkeypatch):
    elsewhere = tmp_path / "somewhere-else.bin"

    def _fake_hf_hub_download(**_kwargs):
        elsewhere.write_bytes(b"moved")
        return str(elsewhere)

    monkeypatch.setattr(fetch_models, "hf_hub_download", _fake_hf_hub_download)
    fetch_models._download(_spec(), tmp_path / "model.gguf")

    assert (tmp_path / "model.gguf").read_bytes() == b"moved"
    assert not elsewhere.exists()


def test_sha256_of_matches_hashlib(tmp_path):
    path = tmp_path / "some.bin"
    path.write_bytes(b"hello world" * 1000)
    expected = hashlib.sha256(b"hello world" * 1000).hexdigest()
    assert fetch_models._sha256_of(path) == expected


def test_pinned_checksums_are_real_sha256_hex():
    """Guards against accidentally reverting a pin to `None` or garbage.

    Doesn't hit the network - just checks shape (64 lowercase hex chars) -
    but that's enough to catch e.g. a truncated paste or a stray `sha1`.
    """
    for spec in (
        fetch_models.CHAT_MODEL,
        fetch_models.EMBED_MODEL,
        fetch_models.TEST_MODEL,
        fetch_models.RERANK_MODEL,
    ):
        assert spec.sha256 is not None, f"{spec.name} has no pinned checksum"
        assert len(spec.sha256) == 64
        assert all(c in "0123456789abcdef" for c in spec.sha256)


def test_fetch_skips_download_when_file_present_and_checksum_matches(tmp_path, monkeypatch):
    content = b"already downloaded"
    (tmp_path / "model.gguf").write_bytes(content)
    spec = _spec(sha256=hashlib.sha256(content).hexdigest())

    called = False

    def _fail_download(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(fetch_models, "_download", _fail_download)

    fetch_models.fetch(spec, force=False)

    assert called is False


def test_fetch_raises_on_checksum_mismatch_for_existing_file(tmp_path, monkeypatch):
    (tmp_path / "model.gguf").write_bytes(b"corrupted-or-wrong-file")
    spec = _spec(sha256="0" * 64)
    monkeypatch.setattr(fetch_models, "_download", lambda *a, **k: pytest.fail("should not re-download"))

    with pytest.raises(fetch_models.ChecksumMismatchError):
        fetch_models.fetch(spec, force=False)

    # The mismatching file is left in place for the operator to inspect -
    # only fresh downloads that fail verification get deleted automatically.
    assert (tmp_path / "model.gguf").exists()


def test_fetch_forces_redownload_when_force_is_set(tmp_path, monkeypatch):
    (tmp_path / "model.gguf").write_bytes(b"stale")
    new_content = b"fresh content"
    spec = _spec(sha256=hashlib.sha256(new_content).hexdigest())

    seen_force = []

    def _fake_download(_spec, destination, *, force=False):
        seen_force.append(force)
        destination.write_bytes(new_content)

    monkeypatch.setattr(fetch_models, "_download", _fake_download)

    fetch_models.fetch(spec, force=True)

    assert (tmp_path / "model.gguf").read_bytes() == new_content
    assert seen_force == [True]  # --force must reach hf_hub_download(force_download=True)


def test_fetch_deletes_file_on_post_download_checksum_mismatch(tmp_path, monkeypatch):
    spec = _spec(sha256="f" * 64)

    def _fake_download(_spec, destination, *, force=False):
        destination.write_bytes(b"not what we expected")

    monkeypatch.setattr(fetch_models, "_download", _fake_download)

    with pytest.raises(fetch_models.ChecksumMismatchError):
        fetch_models.fetch(spec, force=False)

    assert not (tmp_path / "model.gguf").exists()


def test_fetch_without_pinned_checksum_warns_but_succeeds(tmp_path, monkeypatch, capsys):
    spec = _spec(sha256=None)

    def _fake_download(_spec, destination, *, force=False):
        destination.write_bytes(b"anything")

    monkeypatch.setattr(fetch_models, "_download", _fake_download)

    fetch_models.fetch(spec, force=False)

    assert (tmp_path / "model.gguf").exists()
    assert "no pinned SHA256" in capsys.readouterr().err


def test_main_test_flag_selects_tiny_chat_model_and_real_embedder(monkeypatch):
    fetched = []
    monkeypatch.setattr(fetch_models, "fetch", lambda spec, force: fetched.append(spec))
    monkeypatch.setattr("sys.argv", ["fetch_models.py", "--test"])

    assert fetch_models.main() == 0
    assert fetched == [fetch_models.TEST_MODEL, fetch_models.EMBED_MODEL]


def test_nested_shard_uses_the_models_root_as_local_dir(tmp_path, monkeypatch):
    calls = []

    def _fake_hf_hub_download(**kwargs):
        calls.append(kwargs)
        target = Path(kwargs["local_dir"]) / kwargs["filename"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"shard")
        return str(target)

    monkeypatch.setattr(fetch_models, "hf_hub_download", _fake_hf_hub_download)
    filename = "UD-Q5_K_M/model-00001-of-00003.gguf"
    fetch_models._download(_spec(filename=filename), tmp_path / filename, force=True)

    assert calls[0]["local_dir"] == str(tmp_path)
    assert (tmp_path / filename).read_bytes() == b"shard"


def test_mistral_small_4_fetch_walks_the_three_shards(monkeypatch):
    spec = fetch_models.EXTRA_MODELS["mistral-small-4"]
    seen = []
    monkeypatch.setattr(fetch_models, "_fetch_part", lambda part, force: seen.append(part.filename))

    fetch_models.fetch(spec, force=False)

    assert seen == [spec.filename, *[name for name, _sha in spec.extra_files]]
    for sha in (spec.sha256, *(item[1] for item in spec.extra_files)):
        assert sha is not None and len(sha) == 64


def test_main_extra_fetches_only_that_gguf(monkeypatch):
    fetched = []
    monkeypatch.setattr(fetch_models, "fetch", lambda spec, force: fetched.append(spec))
    monkeypatch.setattr("sys.argv", ["fetch_models.py", "--extra", "coder-30b-q5"])

    assert fetch_models.main() == 0
    assert fetched == [fetch_models.EXTRA_MODELS["coder-30b-q5"]]
    assert fetch_models.EXTRA_MODELS["coder-32b-q5"].sha256


def test_main_default_selects_full_chat_model_and_embedder(monkeypatch):
    fetched = []
    monkeypatch.setattr(fetch_models, "fetch", lambda spec, force: fetched.append(spec))
    monkeypatch.setattr("sys.argv", ["fetch_models.py"])

    assert fetch_models.main() == 0
    assert fetched == [fetch_models.CHAT_MODEL, fetch_models.EMBED_MODEL, fetch_models.RERANK_MODEL]


def test_main_returns_nonzero_on_checksum_mismatch(monkeypatch):
    def _raise(_spec, force):
        raise fetch_models.ChecksumMismatchError("boom")

    monkeypatch.setattr(fetch_models, "fetch", _raise)
    monkeypatch.setattr("sys.argv", ["fetch_models.py"])

    assert fetch_models.main() == 1


def test_main_hf_train_base_flag(monkeypatch, tmp_path):
    called = []

    def _fake_fetch(*, force):
        called.append(force)

    monkeypatch.setattr(fetch_models, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(fetch_models, "fetch_hf_train_base", _fake_fetch)
    monkeypatch.setattr("sys.argv", ["fetch_models.py", "--hf-train-base"])

    assert fetch_models.main() == 0
    assert called == [False]
