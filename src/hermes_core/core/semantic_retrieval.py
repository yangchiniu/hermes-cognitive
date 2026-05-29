"""
Semantic Memory Retrieval — Lightweight TF-IDF based retrieval system.

Pure Python stdlib (math, collections, re, string). No external dependencies.
Provides singleton SemanticRetrieval class for memory search and integration
with Hermes core MemoryManager.
"""

import math
import re
import string
from collections import defaultdict

# ---------------------------------------------------------------------------
# Stopwords
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset({
    "a", "an", "the", "is", "was", "were", "are", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "shall", "should", "may", "might", "must", "can", "could",
    "this", "that", "these", "those", "it", "its", "they", "them",
    "their", "we", "our", "you", "your", "he", "she", "him", "her",
    "his", "not", "no", "nor", "and", "or", "but", "if", "then",
    "else", "when", "where", "why", "how", "all", "each", "every",
    "both", "few", "more", "most", "some", "any", "such", "only",
    "own", "same", "so", "than", "too", "very", "just", "because",
    "as", "until", "while", "of", "at", "by", "for", "with", "about",
    "against", "between", "into", "through", "during", "before",
    "after", "above", "below", "to", "from", "up", "down", "in",
    "out", "on", "off", "over", "under", "again", "further", "once",
    "here", "there",
    # common tech / programming words
    "api", "function", "method", "class", "module", "file", "data",
    "code", "value", "key", "set", "get", "use", "using", "used",
    "like", "make", "made", "need", "want", "way", "things", "thing",
    "also", "well", "back", "still", "even", "much", "many", "one",
    "two", "three", "first", "second", "new", "old", "know", "see",
    "go", "take", "let", "put", "say", "tell", "ask", "work",
    "find", "give", "show", "try", "call", "run", "look", "help",
})

# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_TOKEN_PATTERN = re.compile(r"[{}]+".format(re.escape(string.punctuation)))


def _tokenize(text: str) -> list[str]:
    """Split text on whitespace and punctuation, lowercase, filter stopwords."""
    if not text:
        return []
    # Replace punctuation with spaces, then split on whitespace
    cleaned = _TOKEN_PATTERN.sub(" ", text)
    tokens = cleaned.lower().split()
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------

_INSTANCE = None


class _SingletonMeta(type):
    """Simple metaclass for singleton pattern."""
    _instance = None

    def __call__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__call__(*args, **kwargs)
        return cls._instance


# ---------------------------------------------------------------------------
# SemanticRetrieval
# ---------------------------------------------------------------------------


class SemanticRetrieval(metaclass=_SingletonMeta):
    """Lightweight TF-IDF based memory retrieval system.

    Builds an in-memory TF-IDF index over memory dicts and provides
    search, category-filtered search, and similarity lookup without
    any external dependencies.
    """

    def __init__(self):
        self._memories: dict[str, dict] = {}       # doc_id -> memory dict
        self._tfidf_index: dict[str, dict[str, float]] = {}  # term -> {doc_id: score}
        self._idf: dict[str, float] = {}           # term -> idf score
        self._doc_norms: dict[str, float] = {}     # doc_id -> L2 norm (for cosine)
        self._total_docs: int = 0
        self._categories: set[str] = set()
        self._cache_path = None  # set by save_index/load_index

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for testing or config change."""
        _SingletonMeta._instance = None

    # ------------------------------------------------------------------
    # Index persistence (pickle cache for fast reload)
    # ------------------------------------------------------------------

    def save_index(self, path: str = None) -> None:
        """Serialize TF-IDF index to disk for fast reload."""
        import pickle, pathlib
        if path is None:
            path = str(pathlib.Path.home() / ".hermes" / "core" / "data" / "semantic_index.pkl")
        self._cache_path = path
        data = {
            "memories": self._memories,
            "tfidf_index": dict(self._tfidf_index),
            "idf": self._idf,
            "doc_norms": self._doc_norms,
            "total_docs": self._total_docs,
            "categories": self._categories,
        }
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(data, f, protocol=4)
        import os
        os.replace(tmp, path)

    def load_index(self, path: str = None) -> bool:
        """Load TF-IDF index from disk cache. Returns True if successful."""
        import pickle, pathlib
        if path is None:
            path = str(pathlib.Path.home() / ".hermes" / "core" / "data" / "semantic_index.pkl")
        self._cache_path = path
        if not pathlib.Path(path).exists():
            return False
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
            self._memories = data["memories"]
            idx = data["tfidf_index"]
            self._tfidf_index = defaultdict(lambda: defaultdict(float))
            for term, doc_scores in idx.items():
                for doc_id, score in doc_scores.items():
                    self._tfidf_index[term][doc_id] = score
            self._idf = data["idf"]
            self._doc_norms = data["doc_norms"]
            self._total_docs = data["total_docs"]
            self._categories = data["categories"]
            return self._total_docs > 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_memories(self, memories: list[dict]) -> None:
        """Build TF-IDF index from a list of memory dicts.

        Each dict should have keys: ``text``, ``category``, ``memory_id``.
        If ``memory_id`` is absent, falls back to ``id`` or an auto-counter.
        """
        if not memories:
            return

        # Reset state
        self._memories = {}
        self._tfidf_index = defaultdict(lambda: defaultdict(float))
        self._idf = {}
        self._doc_norms = {}
        self._categories = set()

        # ---- Step 1: extract documents ----
        doc_texts: dict[str, str] = {}
        for i, mem in enumerate(memories):
            doc_id = mem.get("memory_id") or mem.get("id") or f"doc_{i}"
            text = mem.get("text", "")

            self._memories[doc_id] = {
                "memory_id": doc_id,
                "text": text,
                "category": mem.get("category", "general"),
            }
            doc_texts[doc_id] = text
            self._categories.add(mem.get("category", "general"))

        self._total_docs = len(doc_texts)
        if self._total_docs == 0:
            return

        # ---- Step 2: tokenize & compute term frequencies ----
        # tf[doc_id][term] = raw_count
        tf: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        doc_lengths: dict[str, int] = {}

        for doc_id, text in doc_texts.items():
            tokens = _tokenize(text)
            doc_lengths[doc_id] = len(tokens)
            for token in tokens:
                tf[doc_id][token] += 1

        # ---- Step 3: compute IDF ----
        # df[term] = number of documents containing term
        df: dict[str, int] = defaultdict(int)
        for doc_id, term_counts in tf.items():
            for term in term_counts:
                df[term] += 1

        N = self._total_docs
        for term, doc_freq in df.items():
            self._idf[term] = math.log(N / doc_freq) if doc_freq > 0 else 0.0

        # ---- Step 4: compute TF-IDF scores ----
        for doc_id in doc_texts:
            length = doc_lengths[doc_id]
            if length == 0:
                continue
            for term, count in tf[doc_id].items():
                tfidf = (count / length) * self._idf.get(term, 0.0)
                self._tfidf_index[term][doc_id] = tfidf

        # ---- Step 5: compute document L2 norms (for cosine similarity) ----
        sq_sums: dict[str, float] = defaultdict(float)
        for term, doc_scores in self._tfidf_index.items():
            for doc_id, score in doc_scores.items():
                sq_sums[doc_id] += score * score

        for doc_id, sq in sq_sums.items():
            self._doc_norms[doc_id] = math.sqrt(sq) if sq > 0 else 0.0

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Vector-free semantic search using TF-IDF.

        Returns ``top_k`` results (descending score), each as::

            {memory_id, text, category, score}
        """
        if not query or self._total_docs == 0:
            return []

        query_terms = _tokenize(query)
        if not query_terms:
            return []

        # Score every document by summing TF-IDF of matching query terms
        scores: dict[str, float] = defaultdict(float)
        for term in query_terms:
            if term in self._tfidf_index:
                for doc_id, tfidf_val in self._tfidf_index[term].items():
                    scores[doc_id] += tfidf_val

        if not scores:
            return []

        # Sort descending by score
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

        results = []
        for doc_id, score in ranked[:top_k]:
            mem = self._memories.get(doc_id, {})
            results.append({
                "memory_id": doc_id,
                "text": mem.get("text", ""),
                "category": mem.get("category", "general"),
                "score": round(score, 6),
            })

        return results

    # ------------------------------------------------------------------
    # Category-filtered search
    # ------------------------------------------------------------------

    def search_by_category(
        self, query: str, category: str, top_k: int = 3
    ) -> list[dict]:
        """Search within a specific memory category."""
        if not query or self._total_docs == 0:
            return []

        query_terms = _tokenize(query)
        if not query_terms:
            return []

        # Filter docs belonging to the requested category
        candidate_ids = {
            doc_id
            for doc_id, mem in self._memories.items()
            if mem.get("category") == category
        }
        if not candidate_ids:
            return []

        scores: dict[str, float] = defaultdict(float)
        for term in query_terms:
            if term in self._tfidf_index:
                for doc_id, tfidf_val in self._tfidf_index[term].items():
                    if doc_id in candidate_ids:
                        scores[doc_id] += tfidf_val

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

        results = []
        for doc_id, score in ranked[:top_k]:
            mem = self._memories.get(doc_id, {})
            results.append({
                "memory_id": doc_id,
                "text": mem.get("text", ""),
                "category": category,
                "score": round(score, 6),
            })

        return results

    # ------------------------------------------------------------------
    # Find similar
    # ------------------------------------------------------------------

    def find_similar(self, text: str, top_k: int = 5) -> list[dict]:
        """Find memories similar to the given text using TF-IDF cosine.

        This treats ``text`` as a pseudo-document, builds its TF vector,
        and scores all indexed documents by cosine similarity.
        """
        if not text or self._total_docs == 0:
            return []

        tokens = _tokenize(text)
        if not tokens:
            return []

        # Compute TF for the query text
        query_tf: dict[str, int] = defaultdict(int)
        for t in tokens:
            query_tf[t] += 1
        query_len = len(tokens)

        # Build query TF-IDF vector
        query_vec: dict[str, float] = {}
        for term, count in query_tf.items():
            if term in self._idf:
                query_vec[term] = (count / query_len) * self._idf[term]

        if not query_vec:
            return []

        # Compute query L2 norm
        query_norm = math.sqrt(sum(v * v for v in query_vec.values()))
        if query_norm == 0.0:
            return []

        # Cosine similarity against every document
        scores: dict[str, float] = {}
        for term, qval in query_vec.items():
            if term in self._tfidf_index:
                for doc_id, dval in self._tfidf_index[term].items():
                    scores[doc_id] = scores.get(doc_id, 0.0) + qval * dval

        # Normalise by L2 norms
        for doc_id in scores:
            doc_norm = self._doc_norms.get(doc_id, 0.0)
            if doc_norm > 0:
                scores[doc_id] = scores[doc_id] / (query_norm * doc_norm)
            else:
                scores[doc_id] = 0.0

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

        results = []
        for doc_id, score in ranked[:top_k]:
            mem = self._memories.get(doc_id, {})
            results.append({
                "memory_id": doc_id,
                "text": mem.get("text", ""),
                "category": mem.get("category", "general"),
                "score": round(score, 6),
            })

        return results

    # ------------------------------------------------------------------
    # Stats / Clear
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return index statistics."""
        return {
            "indexed_count": self._total_docs,
            "term_count": len(self._tfidf_index),
            "categories": sorted(self._categories),
        }

    def clear_index(self) -> None:
        """Reset the entire index."""
        self._memories.clear()
        self._tfidf_index.clear()
        self._idf.clear()
        self._doc_norms.clear()
        self._total_docs = 0
        self._categories.clear()

    # ------------------------------------------------------------------
    # Integration helpers
    # ------------------------------------------------------------------

    def index_from_memory_manager(self, memory_manager) -> None:
        """Pull ALL memories from a MemoryManager and index them.

        Expects ``memory_manager`` to have a ``get_all_memories()`` method
        that returns a list of dicts with at least ``text``, ``category``,
        and ``memory_id`` (or ``id``).
        """
        if not hasattr(memory_manager, "get_all_memories"):
            raise AttributeError(
                "memory_manager must have a 'get_all_memories()' method"
            )
        memories = memory_manager.get_all_memories()
        self.index_memories(memories)

    def retrieve_relevant(
        self,
        task_description: str,
        memory_manager=None,
        top_k: int = 5,
    ) -> dict:
        """ONE-CALL retrieval.

        If a ``memory_manager`` is provided and the index is empty,
        it will be auto-populated via ``index_from_memory_manager``.

        Returns categorised results::

            {
                "episodic": [...],
                "semantic": [...],
                "procedural": [...],
                "environment": [...],
            }
        """
        if self._total_docs == 0 and memory_manager is not None:
            self.index_from_memory_manager(memory_manager)

        known_categories = {"episodic", "semantic", "procedural", "environment"}
        results: dict[str, list[dict]] = {
            cat: [] for cat in known_categories
        }

        if self._total_docs == 0:
            return results

        # Get overall top_k * 4 to have enough candidates for distribution
        top_results = self.search(task_description, top_k=top_k * 4)

        # Distribute into categories
        for r in top_results:
            cat = r["category"]
            if cat in known_categories:
                if len(results[cat]) < top_k:
                    results[cat].append(r)

        return results


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def get_retrieval() -> SemanticRetrieval:
    """Return the singleton SemanticRetrieval instance."""
    return SemanticRetrieval()


def retrieve(task_description: str, top_k: int = 5) -> dict:
    """One-liner retrieval using the global singleton.

    Returns categorised results::

        {
            "episodic": [...],
            "semantic": [...],
            "procedural": [...],
            "environment": [...],
        }
    """
    sr = get_retrieval()
    return sr.retrieve_relevant(task_description, top_k=top_k)
