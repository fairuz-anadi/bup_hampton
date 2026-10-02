"""Unit and integration tests for FuelGuard RAG ingestion, retrieval, and API."""
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.rag.ingestion.chunker import DocumentChunk, TextChunker, estimate_tokens
from app.rag.ingestion.embeddings import EmbeddingGenerator, cosine_similarity
from app.rag.ingestion.loaders import DocumentLoader, LoadedDocument
from app.rag.pipeline import RAGPipeline, get_rag_pipeline
from app.rag.retrieval.filters import MetadataFilter, apply_filters
from app.rag.retrieval.reranker import ResultReranker
from app.rag.retrieval.retriever import HybridRetriever, RetrievalResult
from app.rag.store import RAGStore


@pytest.fixture
def sample_doc(tmp_path):
    doc_path = tmp_path / "test_policy.md"
    content = """---
document_id: TEST-POL-001
filename: test_policy.md
category: rules_policies
document_type: policy
year: 2026
section: depot_limits
version: 1.0.0
---

# Depot Operational Policy

## Minimum Reserve Rules
Depots must maintain at least 10% of total fuel capacity as an emergency strategic reserve.
Never draw Gazipur or Patiya below this reserve floor.

## Dispatch Limits
Gazipur depot dispatch capacity is capped at 12000 L per tick.
Patiya depot dispatch capacity is capped at 11000 L per tick.
"""
    doc_path.write_text(content, encoding="utf-8")
    return doc_path


def test_document_loader_markdown(sample_doc):
    docs = DocumentLoader.load(sample_doc)
    assert len(docs) == 1
    doc = docs[0]
    assert doc.document_id == "TEST-POL-001"
    assert doc.filename == "test_policy.md"
    assert doc.category == "rules_policies"
    assert doc.document_type == "policy"
    assert doc.year == 2026
    assert doc.section == "depot_limits"
    assert "Minimum Reserve Rules" in doc.text
    assert "---" not in doc.text  # frontmatter stripped from clean text


def test_document_loader_json_and_txt(tmp_path):
    txt_path = tmp_path / "sample.txt"
    txt_path.write_text("Fuel dispatch instructions: always check in-transit fuel.", encoding="utf-8")
    txt_docs = DocumentLoader.load(txt_path, default_category="guidelines")
    assert len(txt_docs) == 1
    assert txt_docs[0].category == "guidelines"
    assert "in-transit" in txt_docs[0].text

    json_path = tmp_path / "sample.json"
    json_path.write_text('{"depot": "gazipur", "max_capacity": 90000}', encoding="utf-8")
    json_docs = DocumentLoader.load(json_path)
    assert len(json_docs) == 1
    assert json_docs[0].document_type == "structured_json"


def test_text_chunker(sample_doc):
    docs = DocumentLoader.load(sample_doc)
    chunker = TextChunker(min_chunk_tokens=20, target_chunk_tokens=50, max_chunk_tokens=100, overlap_tokens=10)
    chunks = chunker.chunk_document(docs[0])

    assert len(chunks) >= 1
    chunk = chunks[0]
    assert chunk.document_id == "TEST-POL-001"
    assert chunk.filename == "test_policy.md"
    assert chunk.category == "rules_policies"
    assert chunk.chunk_index == 0
    assert chunk.token_count > 0
    assert "emergency strategic reserve" in chunk.content


def test_embedding_generator_and_cosine():
    embedder = EmbeddingGenerator(dimension=64)
    v1 = embedder.embed_query("minimum depot reserve threshold")
    v2 = embedder.embed_query("minimum depot reserve threshold")
    v3 = embedder.embed_query("weather forecast monsoon rain")

    # Identical queries should produce similarity ~1.0
    sim_identical = cosine_similarity(v1, v2)
    assert pytest.approx(sim_identical, 0.001) == 1.0

    # Relevant query should have higher similarity than unrelated query
    sim_unrelated = cosine_similarity(v1, v3)
    assert sim_identical > sim_unrelated


def test_metadata_filtering():
    c1 = DocumentChunk(
        chunk_id="c1", document_id="d1", filename="f1.md", category="rules_policies",
        document_type="policy", version="1.0", year=2026, section="reserves",
        page=1, chunk_index=0, content="10% reserve", token_count=10
    )
    c2 = DocumentChunk(
        chunk_id="c2", document_id="d2", filename="f2.md", category="project_documents",
        document_type="architecture", version="1.0", year=2026, section="fastapi",
        page=1, chunk_index=0, content="FastAPI backend", token_count=10
    )

    chunks = [c1, c2]
    filtered_rules = apply_filters(chunks, MetadataFilter(category="rules_policies"))
    assert len(filtered_rules) == 1
    assert filtered_rules[0].chunk_id == "c1"

    filtered_arch = apply_filters(chunks, MetadataFilter(category="project_documents"))
    assert len(filtered_arch) == 1
    assert filtered_arch[0].chunk_id == "c2"

    filtered_none = apply_filters(chunks, MetadataFilter(category="external_data"))
    assert len(filtered_none) == 0


def test_retriever_and_reranker(tmp_path):
    store = RAGStore(buffer_path=tmp_path / "rag_buffer.jsonl")
    embedder = EmbeddingGenerator(dimension=128)

    c1 = DocumentChunk(
        chunk_id="c1", document_id="d1", filename="reserve_policy.md", category="rules_policies",
        document_type="policy", version="1.0", year=2026, section="rules",
        page=1, chunk_index=0, content="Mandatory depot reserve policy requires 10% reserve for diesel.",
        token_count=15, embedding=embedder.embed_query("Mandatory depot reserve policy requires 10% reserve for diesel.")
    )
    c2 = DocumentChunk(
        chunk_id="c2", document_id="d2", filename="architecture.md", category="project_documents",
        document_type="architecture", version="1.0", year=2026, section="overview",
        page=1, chunk_index=0, content="FuelGuard architecture uses FastAPI and React.",
        token_count=10, embedding=embedder.embed_query("FuelGuard architecture uses FastAPI and React.")
    )

    store.save_documents_and_chunks([], [c1, c2])

    retriever = HybridRetriever(store=store, embedder=embedder)
    results = retriever.retrieve("depot reserve policy 10%", top_k=2)

    assert len(results) == 2
    assert results[0].source == "reserve_policy.md"
    assert results[0].score > results[1].score

    reranker = ResultReranker()
    reranked = reranker.rerank("What is the 10% depot reserve policy?", results)
    assert reranked[0].source == "reserve_policy.md"
    assert reranked[0].category == "rules_policies"


def test_rag_pipeline_end_to_end(tmp_path):
    # Setup test directory with 2 files
    rag_dir = tmp_path / "rag_data"
    rules_dir = rag_dir / "rules_policies"
    rules_dir.mkdir(parents=True)

    rule_file = rules_dir / "fuel_rules.md"
    rule_file.write_text("""---
document_id: DOC-R-01
category: rules_policies
---
# Fuel Dispatch Rules
Never dispatch fuel over a route with status DISRUPTED.
Failed dispatches will not refund depot inventory.
""", encoding="utf-8")

    store = RAGStore(dsn=None, buffer_path=tmp_path / "test_store.jsonl")
    pipeline = RAGPipeline(data_dir=rag_dir, store=store)
    res = pipeline.ingest(data_dir=rag_dir, force=True)
    assert res["status"] == "success"
    assert res["documents_indexed"] == 1

    search_res = pipeline.search("What happens if a route is disrupted?", category="rules_policies")
    assert len(search_res) >= 1
    assert "DISRUPTED" in search_res[0].content
    assert search_res[0].source == "fuel_rules.md"

    ask_res = pipeline.ask("What happens if a route is disrupted?")
    assert len(ask_res["sources"]) >= 1
    assert ask_res["sources"][0]["source"] == "fuel_rules.md"
    assert "fuel_rules.md" in ask_res["answer"]


def test_rag_api_endpoints(tmp_path):
    settings = Settings(database_url="", sse_enabled=False)
    app = create_app(settings)

    with TestClient(app) as client:
        # 1. Stats endpoint
        r_stats = client.get("/api/rag/stats")
        assert r_stats.status_code == 200
        data = r_stats.json()
        assert "counts" in data
        assert "offline_mode" in data

        # 2. Search endpoint
        r_search = client.post(
            "/api/rag/search",
            json={"query": "minimum depot reserve", "top_k": 3},
        )
        assert r_search.status_code == 200
        assert "results" in r_search.json()

        # 3. Ask endpoint
        r_ask = client.post(
            "/api/rag/ask",
            json={"query": "What policy applies to minimum depot reserve?", "top_k": 3},
        )
        assert r_ask.status_code == 200
        ans_data = r_ask.json()
        assert "answer" in ans_data
        assert "sources" in ans_data
