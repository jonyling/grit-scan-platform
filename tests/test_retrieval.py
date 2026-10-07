import hashlib
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from grit.ontology.pack import load_ontology_pack
from grit.retrieval.anchors import build_anchors
from grit.retrieval.embed_run import candidate_text, main
from grit.retrieval.embedder import MAX_CHARS, Embedder, EmbeddingError
from grit.retrieval.similarity import dot, normalize, to_float32, top_matches
from grit.retrieval.store import VectorStore, text_hash

_ROOT = Path(__file__).resolve().parent.parent


class FakeEmbedder:
    """Hashes words into 64 dimensions, so texts that share words are closer. It makes no network call."""

    model = "fake-embed"
    dimensions = 64
    batch_size = 3

    def __init__(self, model="fake-embed", dimensions=64):
        self.model, self.dimensions, self.calls = model, dimensions, []

    def embed(self, texts, *, task):
        self.calls.append((task, list(texts)))
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for word in re.findall(r"[a-z0-9]+", text.casefold()):
                vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % self.dimensions] += 1.0
            vectors.append(to_float32(normalize(vector)))
        return vectors


def test_vectors_are_unit_length_and_dot_rejects_a_mismatch_or_a_zero_vector() -> None:
    unit = normalize([3.0, 4.0])
    assert abs(dot(unit, unit) - 1.0) < 1e-12 and unit == [0.6, 0.8]
    for bad in (lambda: normalize([0.0, 0.0]), lambda: dot([1.0], [1.0, 2.0])):
        try:
            bad()
        except ValueError:
            continue
        raise AssertionError("A bad vector must raise")


def test_float32_rounding_is_stable() -> None:
    once = to_float32(normalize([1.0, 2.0, 3.0]))
    assert to_float32(once) == once


def test_top_matches_orders_by_score_then_index() -> None:
    anchors = [[1.0, 0.0], [0.0, 1.0], [1.0, 0.0], [0.6, 0.8]]
    assert top_matches([1.0, 0.0], anchors, k=3) == [(0, 1.0), (2, 1.0), (3, 0.6)]


def test_the_store_embeds_each_text_once_across_batches_calls_and_instances() -> None:
    texts = ["alpha", "beta", "alpha", "gamma", "delta", "epsilon", "beta"]
    with TemporaryDirectory() as directory:
        path = Path(directory) / "index.sqlite"
        embedder, store = FakeEmbedder(), VectorStore(path)
        first = store.vectors(texts, task="RETRIEVAL_DOCUMENT", embedder=embedder)
        assert [len(call[1]) for call in embedder.calls] == [3, 2] and store.embedded == 5
        again = store.vectors(texts, task="RETRIEVAL_DOCUMENT", embedder=embedder)
        assert len(embedder.calls) == 2 and again == first and first[0] == first[2]
        store.close()
        reopened = VectorStore(path)
        assert reopened.vectors(texts, task="RETRIEVAL_DOCUMENT", embedder=embedder) == first and len(embedder.calls) == 2
        # A different task, model, or dimension is a different vector.
        reopened.vectors(["alpha"], task="RETRIEVAL_QUERY", embedder=embedder)
        reopened.vectors(["alpha"], task="RETRIEVAL_DOCUMENT", embedder=FakeEmbedder(model="other"))
        reopened.vectors(["alpha"], task="RETRIEVAL_DOCUMENT", embedder=FakeEmbedder(dimensions=32))
        reopened.close()


def test_whitespace_alone_does_not_make_a_new_vector() -> None:
    assert text_hash("rail  signalling\n") == text_hash("rail signalling")
    assert text_hash("Rail") != text_hash("rail")


def test_the_embedder_returns_unit_vectors_cuts_long_text_and_refuses_a_bad_shape() -> None:
    seen = {}

    def embed_content(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[3.0, 4.0, 0.0, 0.0]) for _ in kwargs["contents"]])

    embedder = Embedder(client=SimpleNamespace(models=SimpleNamespace(embed_content=embed_content)), dimensions=4)
    assert embedder.embed(["x" * (MAX_CHARS + 50)], task="RETRIEVAL_QUERY") == [[0.6000000238418579, 0.800000011920929, 0.0, 0.0]]
    assert len(seen["contents"][0]) == MAX_CHARS and seen["config"] == {"task_type": "RETRIEVAL_QUERY", "output_dimensionality": 4}
    for client, expected in (
        (SimpleNamespace(models=SimpleNamespace(embed_content=lambda **_: SimpleNamespace(embeddings=[]))), "Expected 1 vectors"),
        (SimpleNamespace(models=SimpleNamespace(embed_content=lambda **_: (_ for _ in ()).throw(RuntimeError('quota exceeded {"key": "secret-value"}')))), "quota exceeded"),
    ):
        try:
            Embedder(client=client, dimensions=4).embed(["x"], task="RETRIEVAL_QUERY")
        except EmbeddingError as error:
            assert expected in str(error) and "secret-value" not in str(error)
        else:
            raise AssertionError("A bad answer must raise")
    try:
        embedder.embed(["x"], task="CLASSIFICATION")
    except ValueError:
        pass
    else:
        raise AssertionError("An unknown task must raise")


def test_anchors_are_topics_in_a_fixed_order_and_never_names() -> None:
    pack = load_ontology_pack(_ROOT / "config", "urs")
    anchors = build_anchors(pack)
    assert anchors == build_anchors(pack)
    assert len({anchor.anchor_id for anchor in anchors}) == len(anchors)
    assert {anchor.kind for anchor in anchors} == {"TAG_CLUSTER", "EMERGING_THEME", "LOB_SCOPE", "STE_THEME", "MANAGEMENT_FOCUS", "MANAGEMENT_REQUEST"}
    names = {row["supplierName"].casefold() for row in pack.exposure["suppliers"]} | {row["competitor"].casefold() for row in pack.competitors["competitors"]}
    assert not any(anchor.text.casefold() in names for anchor in anchors)
    assert all(anchor.text and anchor.source for anchor in anchors)


def _run_folder(directory: Path, texts) -> Path:
    folder = directory / "run"
    folder.mkdir()
    candidates = [{"url": f"https://news.com/{number}", "title": title, "text": body} for number, (title, body) in enumerate(texts)]
    (folder / "candidates.json").write_text(json.dumps({"runDate": "2026-10-06", "candidates": candidates}), encoding="utf-8")
    return folder


def test_embed_run_writes_a_score_table_drops_nothing_and_reuses_the_cache() -> None:
    first_anchor = build_anchors(load_ontology_pack(_ROOT / "config", "urs"))[0]
    with TemporaryDirectory() as directory:
        folder = _run_folder(Path(directory), [(first_anchor.text, ""), ("Orlando airport trams", "Gate link trams in service"), ("Metro signalling contract", "")])
        before = (folder / "candidates.json").read_text()
        embedder, store = FakeEmbedder(), Path(directory) / "index.sqlite"
        assert main(["--run", str(folder)], embedder=embedder, store_path=store) == 0
        table = json.loads((folder / "semantic.json").read_text())
        assert table["model"] == "fake-embed" and len(table["scores"]) == 3 and table["anchorCount"] == len(json.loads((folder / "anchors.json").read_text()))
        assert all(len(item["best"]) == 3 and item["best"][0]["score"] >= item["best"][1]["score"] for item in table["scores"])
        assert table["scores"][0]["best"][0]["anchor_id"] == first_anchor.anchor_id and table["scores"][0]["best"][0]["score"] > 0.99
        assert (folder / "candidates.json").read_text() == before
        calls = len(embedder.calls)
        assert main(["--run", str(folder)], embedder=embedder, store_path=store) == 0 and len(embedder.calls) == calls


def test_embed_run_dry_run_calls_nothing_and_a_missing_run_is_an_error() -> None:
    with TemporaryDirectory() as directory:
        folder = _run_folder(Path(directory), [("A title", "A body")])
        embedder = FakeEmbedder()
        assert main(["--run", str(folder), "--dry-run"], embedder=embedder, store_path=Path(directory) / "i.sqlite") == 0
        assert embedder.calls == [] and not (folder / "semantic.json").exists()
        assert main(["--run", str(Path(directory) / "missing")], embedder=embedder, store_path=Path(directory) / "i.sqlite") == 1
    assert candidate_text({"title": "T", "text": "B"}) == "T\nB"


def test_filter_run_binds_keeps_a_decision_for_every_candidate_and_needs_the_cached_vectors() -> None:
    from grit.retrieval.filter_run import main as filter_main

    items = [("siemens", "Siemens wins a Smart Metro Systems order", "A customer is named, with a LOB keyword so the one-word name has a second cue."), ("metro", "Smart Metro Systems extend a line", ""), ("trams", "Orlando airport debuts upgraded trams", "Gate link trams")]
    with TemporaryDirectory() as directory:
        root = Path(directory)
        folder = root / "run"
        folder.mkdir()
        candidates = [{"url": f"https://{name}.com/a", "title": title, "text": body, "publisher": f"{name}.com", "published_at": "2026-10-05T10:00:00Z"} for name, title, body in items]
        (folder / "candidates.json").write_text(json.dumps({"runDate": "2026-10-06", "candidates": candidates}), encoding="utf-8")
        search = {"query_id": "q1", "query": "rail", "results": [{"rank": rank, "url": candidate["url"]} for rank, candidate in enumerate(candidates, start=1)]}
        (folder / "searches.jsonl").write_text(json.dumps(search) + "\n", encoding="utf-8")
        store = root / "index.sqlite"
        assert filter_main(["--run", str(folder)], store_path=store) == 1, "no semantic.json yet"
        assert main(["--run", str(folder)], embedder=FakeEmbedder(), store_path=store) == 0
        assert filter_main(["--run", str(folder)], store_path=store) == 0
        decisions = [json.loads(line) for line in (folder / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [item["url"] for item in decisions] == [candidate["url"] for candidate in candidates] and all(item["decision"] for item in decisions)
        signals = json.loads((folder / "signals.json").read_text(encoding="utf-8"))
        assert [item["title"] for item in signals["bound"]] == ["Siemens wins a Smart Metro Systems order", "Smart Metro Systems extend a line"]
        assert signals["bound"][0]["link_level"] == "ENTITY" and signals["bound"][1]["canonical_lob"] == "Mobility Rail"
        assert signals["bound"][1]["found_by"][0]["query"] == "rail" and signals["bound"][1]["best_anchors"][0]["anchor"]
        assert [item["title"] for item in signals["unbound"]] == ["Orlando airport debuts upgraded trams"]
        store.unlink()
        assert filter_main(["--run", str(folder)], store_path=store) == 1, "no cached vectors, and no API call"
