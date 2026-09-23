English | [日本語](model-policy.ja.md)

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

## Size, revisions and storage

**Size.** The pinned files listed in `locks/models.json` total **4,586,590,960 bytes** (about 4.59 GB / 4.27 GiB):

| Model | Pinned files | Largest file |
|---|---|---|
| BAAI/bge-m3 | 2,293,331,623 bytes | `pytorch_model.bin`, 2,271,145,830 bytes |
| BAAI/bge-reranker-v2-m3 | 2,293,259,337 bytes | `model.safetensors`, 2,271,071,852 bytes |

The downloader fetches only the files listed in `locks/models.json`. Other files in the upstream repositories (for example the ONNX export of bge-m3) are not downloaded.

**Pinned revisions.** Each model is fetched at the commit shown in the table above. Every file is checked against the byte size and SHA256 in `locks/models.json`; a mismatch stops the script with `FAIL: model checksum mismatch`. The script then writes the pinned revision to the cache's `refs/main`, so the runtime resolves the pinned snapshot.

**Download time.** It depends on your connection and disk. For reference, on the connection used to validate this release the step took about 4.5–6.5 minutes, including SHA256 verification of all files. The network transfer alone took about 2 minutes at roughly 35–40 MB/s. Plan for longer on slower links.

**Storage location.** On the Linux / WSL2 path the cache is `$BERTH_DATA_DIR/models` (for example `$HOME/.local/share/berth/models`), in the Hugging Face cache layout (`models--BAAI--bge-m3/`, `models--BAAI--bge-reranker-v2-m3/`). `ops/linux.sh install` mounts this directory read-only into the API and worker pods at `/app/store/models`. The pods run as uid 10001, so the files must stay world-readable. Models are not part of `ops/linux.sh backup`; keep your own copy or download them again.

**Offline runtime.** The image sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`, and no init container or startup step downloads models. Download the models before `ops/linux.sh install`: a pod cannot fetch a missing model. The spaCy models are installed as pinned wheels when the image is built, so the image build needs network access, but running pods do not.
