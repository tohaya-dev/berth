# Required model policy

The runtime requires retrieval and guardrail models. These remain part of functional acceptance even when downloaded separately.

| Component | Pinned version/revision | Upstream license | Delivery |
|---|---|---|---|
| BAAI/bge-m3 | 5617a9f61b028005a4858fdac845db406aefb181 | MIT | Explicit fresh download, file SHA256 in locks/models.json |
| BAAI/bge-reranker-v2-m3 | 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e | Apache-2.0 | Explicit fresh download, file SHA256 in locks/models.json |
| en_core_web_sm | 3.8.0 | MIT | Python image dependency |
| ja_core_news_sm | 3.8.0 | CC BY-SA 4.0 | Python image dependency; retain upstream attribution/share-alike terms |

Sources: https://huggingface.co/BAAI/bge-m3 , https://huggingface.co/BAAI/bge-reranker-v2-m3 , https://github.com/explosion/spacy-models . The downloaded model cards and installed package metadata were inspected. No generative answer-model weights are bundled or downloaded.

`tools/download-rag-models.py --cache PATH` uses pinned revisions and verifies all expected hashes. Mount the cache read-only at runtime. The two BGE model IDs resolve through pinned local refs. Never obtain model caches from another person's live deployment.

The synthetic HTTP provider only validates protocol behavior. Answer quality with a user-selected real LLM, its license, hosting policy and external egress must be evaluated separately.
