> **このドキュメントについて**
> Cynovelaは、AI基盤ツールのコンセプトを個人が手を動かして理解するために作った
> 完全非公式の学習ツールです。商用製品・公式実装ではありません。
> 実装はすべてオリジナルで、FastAPI / SQLite / ChromaDB / BGE-M3 / ローカルLLM
> という OSS スタックで構成されています。
> 会社・製品の公式見解を一切代表しません。

# Cynovela RAG パイプライン

## 1. ハイブリッド検索（ベクター + BM25）

Cynovela の検索は、ベクター検索（意味的類似度ベース）と BM25（古典的なキーワード頻度ベースの検索アルゴリズム）の両方を実行し、その結果を統合する「ハイブリッド検索」を既定としています。実装は `rag.py:1994` の `rag_retrieve()`（非同期関数）にあります。

### 1.1 ベクター検索

- **モデル**: 既定では BGE-M3（多言語埋め込みモデル）。`--mode lite` / `lite-en` / `minimal` の切替は**未配線**で、どの指定でも実際には BAAI/bge-m3 が使われます（名目値は MiniLM-L12-v2 / MiniLM-L3-v2 / TF-IDF。2026-08-02 実測: server.py 起動時ログ「名目値 … は未配線」）。
- **保管庫**: ベクター側は Collection ID ごとに `{cid}__masked` の一組だけです（伏字前の層は撤去済み）。検索はロールによらず常にこの伏字済み層で行われ、admin にだけ、当たった箇所の原文を関係データベースの `tier='raw'` 行から復号して差し替えます。
- **多様性確保**: MMR（Maximal Marginal Relevance：関連性と多様性のバランスを取る再選別アルゴリズム）が `mmr_enabled=true` で有効になり、`mmr_fetch_k=20` で多めに取った候補から `mmr_lambda=0.7` の重みで再選別します（`rag.py:1654-1701`）。

### 1.2 BM25 検索

- **インデックス**: メモリ上に `BM25Okapi` を `(workspace_id, tier)` キーで保持します（`rag.py:101-107`）。Publish 完了時に `build_bm25_index()` で構築し、必要に応じて `rebuild_bm25_from_db()` で SQLite から再構築します。
- **トークン化**: 日本語は fugashi（MeCab ベースの形態素解析器）、英語はスペース区切り。`utils.tokenizer.tokenize()` に集約されています。
- **正規化**: スコアは [0, 1] に正規化されてからハイブリッド統合に渡されます。

### 1.3 ハイブリッド統合方式

`config.rag.hybrid_method` で 2 通りから選びます（`rag.py:2143-2174`）。

| 方式 | 計算式（概念） | 設定値の既定 |
|------|----------------|--------------|
| `rrf`（既定）| `score += 1.0 / (rrf_k + vector_rank) + 1.0 / (rrf_k + bm25_rank)` | `rrf_k=60` |
| `weighted` | `hybrid_score = vector_score * 0.7 + bm25_score * 0.3` | `vector_weight=0.7` `bm25_weight=0.3` |

RRF（Reciprocal Rank Fusion：相互順位融合）は順位の逆数を足し合わせる方式で、スケールの違うスコア（cosine 類似度と BM25 のスコア）を直接合算する必要がないため、既定として採用されています。

---

## 2. Reranker の役割

Reranker（再順位付け器）は、ハイブリッド検索が返した上位 N 件をクエリと chunk 本文のペアで再評価し、より精度の高い順序に並べ替える役割を持ちます。実装は `rag.py:2284-2296` で、`providers/reranker.py` の各クラスを差し替え可能です。

### 2.1 利用できる Reranker

| Provider | クラス | 動作 |
|----------|--------|------|
| `none`（既定） | `NoReranker` | 何もしない（素通し） |
| `cross_encoder` | `CrossEncoderReranker` | sentence-transformers の CrossEncoder で再評価 |
| `flashrank` | `FlashRankReranker` | FlashRank ライブラリで軽量に再評価 |
| `ollama` | `OllamaReranker` | Ollama サーバ経由で再評価 |
| `mlx` | `MLXRerankerProvider` | 骨格のみ（`NotImplementedError`） |
| `http` | （legacy 経路） | 任意の HTTP API で再評価 |

### 2.2 切り替え方法

`cynovela.yaml` の `reranker.provider` で設定します。

```yaml
reranker:
  provider: cross_encoder
  model: BAAI/bge-reranker-v2-m3
  base_url: ""
  api_key: ""
  top_n: 5
```

再ランクの選び方は `cynovela.yaml` の `reranker` 設定に従います（以前あった `--mock` による強制指定は撤去済みです）。

### 2.3 計測

Reranker の推論時間（`rerank_latency_ms`）と各 chunk のスコア（`rerank_scores`）は `RetrievalResult` に記録され、`get_last_retrieval_metrics()` で取り出せます。

---

## 3. スコア 3 種の違い

`ChunkHit`（個々の検索結果）と `RetrievalResult`（検索全体）は次の 3 種のスコアを持ちます（`pipeline_types.py`）。

| スコア名 | 意味 | スケール | 用途 |
|----------|------|----------|------|
| `vector_score` | ベクター類似度（cosine） | 0〜1 | BGE-M3 埋め込みベースの意味的類似度。信頼度閾値の判定に使う |
| `bm25_score` | BM25 スコアを [0, 1] に正規化したもの | 0〜1 | キーワード一致の強さ |
| `rerank_score` | Reranker が付与した再評価スコア | Provider 依存（CrossEncoder は 0〜1 想定） | 上位 N 件の最終順位を決める。0 なら未適用 |

加えて、ハイブリッド統合後の暫定スコアとして `hybrid_score` が計算され、Reranker 未適用時はこれが最終順位を決めます。

**設計上の注意**: 低信頼度フォールバック（Abstention：根拠不足を理由に回答を保留する挙動）の判定には RRF スコアではなく `vector_score` を使う設計です。RRF スコアは順位の逆数和（最大 ≈ 0.033）であり cosine 類似度（0〜1）とは桁が違うため、混同するとしきい値判定が壊れます。

---

## 4. RAG プリセット

`routers/pipeline_config.py:24-60` に組み込みプリセットが 5 件定義されています。Smart Ingestion（取込時のチャンキング戦略 + 分類 + ガードレール）の組み合わせを 1 クリックで切り替えるためのものです。

| ID | 表示名 | チャンキング | RAG モード | ガードレール | 補足 |
|----|--------|--------------|-----------|---------------|------|
| `tech_doc` | 技術文書 | `tech_doc` | `standard` | `default` | マニュアル系想定 |
| `confidential` | 機密文書 | `general` | `standard` | `mask` | PII 含む社内文書向け |
| `personal_memo` | 個人メモ | `email_minutes` | `lite` | `log_only` | 議事録・メモ |
| `multimedia` | マルチメディア | `tech_doc` | `standard` | `default` | 画像・Office 混在、image_mode=caption |
| `quickstart` | クイックスタート | `tech_doc` | `standard` | `default` | 初心者向け全自動 |

### 4.1 RAG モード 3 種

`rag_mode` キーは検索パイプライン全体の挙動を切り替えます。

| モード | 動作 |
|--------|------|
| `lite` | 最小限の RAG。Multi-Query / HyDE / CRAG といったオプションを省略し、1 回の検索で済ませる |
| `standard`（既定） | BM25 ハイブリッド + Reranker（設定時）。一般的な業務利用想定 |
| `hq` | 高品質モード。CRAG・Multi-Query・HyDE をオンにして時間を掛けて精度を取りに行く |

---

## 5. 厳格度モード（システムプロンプト切替）

`rag.py:175-213` には 2 種類のシステムプロンプトが定義されており、検索結果が得られた場合と得られなかった場合とで切り替わります。

| 定数名 | 用途 |
|--------|------|
| `DEFAULT_SYSTEM_PROMPT`（`SYSTEM_PROMPT`） | RAG 有効時。検索結果（context）を根拠に回答することを LLM に指示 |
| `GENERAL_KNOWLEDGE_SYSTEM_PROMPT` | 一般知識モード。context が提供されないことを前提に、知らないことは「分かりません」と返すよう指示 |

加えて、ロール別の前置きが `apply_role_prefix()`（`rag.py:215-231`）で適用されます。

- **admin**: 技術的詳細・設定値・内部構造を含む完全な情報を提供
- **reader**: 要点を絞った分かりやすい説明、専門用語を避ける

<!-- BACKLOG: 「STRICT モード」相当の独立したプロンプト切替や、ガードレール強度を段階的に変える厳格度ダイヤルは spec-raw で確認できなかったため、ここではシステムプロンプトの 2 種類切替を「厳格度モード」として扱う -->

---

## 6. 信頼度閾値（confidence_threshold）

低信頼度フォールバック（Abstention：根拠不足のときに回答を保留・「分かりません」と返す挙動）の判定に使うしきい値です。

### 6.1 設定値

`config.py:131-135`：

```python
# 低信頼度フォールバック: hits の最大 vector_score で判定
# BGE-M3 のノイズフロアは 0.35-0.45 (架空クエリでもこの程度の score が出る)
# 実存クエリは 0.55-0.75 程度のため 0.50 を境界に設定
"confidence_threshold": 0.50,
```

### 6.2 値の根拠

- **BGE-M3 ノイズフロア**: 0.35〜0.45（無関係なクエリでもこの程度の score が出る）
- **実存クエリの典型範囲**: 0.55〜0.75
- **判定境界**: 0.50。これを下回ると「根拠不足」と判断し、回答保留や一般知識モードへの切替の候補となる

### 6.3 スケールに関する重要な注意

判定指標は必ず `vector_score`（cosine 類似度・0〜1 スケール）を使います。RRF スコア（順位の逆数和、最大 ≈ 0.033）と桁が違うため、RRF スコアでしきい値判定を行うと全クエリで Abstention が暴発します。`config.rag.confidence_threshold` の値は cosine スケール前提で解釈してください。

<!-- BACKLOG: confidence_threshold は config に定義済みだが、実際の Abstention 除外ロジックは検索パイプラインに部分統合のみ。全段への統合状況は spec-raw に「パイプラインに部分統合」とだけあり、どの分岐で実際にハジくかまでは確認できなかった -->

---

## 7. 高度な検索オプション（Advanced RAG）

`rag.py` には次のオプションが実装されており、`cynovela.yaml` の `rag` セクションで有効化します。

| オプション | 設定キー | 動作 | 既定 |
|------------|----------|------|------|
| Multi-Query RAG | `multi_query_enabled` / `multi_query_count` | LLM でクエリを N-1 個の言い換えに展開し、各々で検索 → RRF 統合 | on / 3 |
| CRAG（Corrective RAG） | `crag_enabled` / `crag_max_loops` | LLM が検索結果の質を評価し、不十分なら追加検索 | on / 1 |
| HyDE | `hyde_enabled` | クエリから仮想回答を生成し、その埋め込みで検索 | off |
| Adaptive RAG | `adaptive_enabled` / `adaptive_threshold` / `agentic_max_loops` | 複雑度スコアが閾値以上なら Agentic ループに切替 | on / 2.0 / 3 |
| Parent-Child | `parent_child_enabled` / `child_chunk_size` / `parent_chunk_size` | 小さな child chunk で検索ヒットし、LLM に渡すときは大きな parent chunk に差し替える | on / 256 / 1000 |

Parent-Child の差し替えロジックは、`retrieval_detail.hits` には child の preview が入る一方で、LLM プロンプト内 context には parent の長文が入る、という非対称設計です。動作確認の際は LLM プロンプト内 context の文字数（500 文字超になるか）で判断してください。

---

最終更新: 2026-05-26 / Alpha GA 対応版
