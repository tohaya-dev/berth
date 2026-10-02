> **このドキュメントについて**
> Cynovelaは、AI基盤ツールのコンセプトを個人が手を動かして理解するために作った
> 完全非公式の学習ツールです。商用製品・公式実装ではありません。
> 実装はすべてオリジナルで、FastAPI / SQLite / ChromaDB / BGE-M3 / ローカルLLM
> という OSS スタックで構成されています。
> 会社・製品の公式見解を一切代表しません。

# よくある質問（FAQ）

## Q1. 参照元の AI 基盤ツールとの違いは何ですか

Cynovela は、参照元の AI 基盤ツールが解こうとしているコンセプト（社内ドキュメントを安全にローカル LLM へつなぐ RAG 基盤）を、個人が手を動かして理解するために OSS だけで再実装した学習用プロジェクトです。実装はすべてオリジナルで、ソースコード・API 仕様・データモデルに参照元との互換性はありません。商用機能・サポート・SLA は提供しません。

<!-- BACKLOG: 参照元ツールの具体的機能との対照表は spec-raw に根拠なし。書かない。 -->

## Q2. データが外に出ないとはどういう意味ですか

Cynovela の既定構成では、以下のすべてがローカル環境（127.0.0.1 にバインドされた FastAPI サーバー）で完結します。

- **文書本文**: SQLite（`~/.cynovela/db/cynovela.db` 等）と ChromaDB（`~/.cynovela/vector/default/chroma` 等）に保存（A-1）。
- **Embedding 生成**: 既定モードでは BGE-M3 をローカルで実行（A-1 `_MODE_MODELS`）。
- **LLM 推論**: `--lmstudio-url`（既定 `http://localhost:1234`）で指定したローカル LLM に対して OpenAI 互換 /v1 API で送信（A-1, A-5）。

外部送信が発生し得るのは以下の場合のみで、いずれも明示的な設定が必要です。

- `--lan` / `--allow-tailscale` / `--allow-subnet` で他ホストからのアクセスを許可した場合（A-5）。
- `reranker.provider` を `cohere` / `jina` / `voyage` などの外部 API に設定した場合（A-1 `reranker` セクション）。
- `execution.llm_provider` を `openrouter` / `claude_api` に設定した場合（A-1 `execution` セクション）。

なお、宛先が外部と判定された場合は送出そのものを止めるか、伏字済みのものだけを送ります。止まる条件の一覧は `known-limitations.md` §2「外部への送出が止まる条件」にあります。

既定では IP アローリストミドルウェアにより loopback 以外からのアクセスは 403 で拒否されます（A-5）。

## Q3. 使えるファイル種別は何ですか

`rag.py` の `extract_text()` がテキスト抽出を担当します（A-3 行 431）。画像については OCR による抽出経路（`_extract_image_text()`、A-3 行 375）が用意されており、`multimedia` プリセットでは画像・Office 混在に対応する記述があります（A-3 プリセット表）。

<!-- BACKLOG: extract_text の対応拡張子一覧は spec-raw に列挙がない。確認後追記。 -->

## Q4. スペック要件はどれくらい必要ですか

**起動モード（`--mode`）を変えても、必要なモデルの大きさは変わりません。** 軽量モデルへの切替は未配線で、実際に読み込まれる Embedding はどのモードでも `BAAI/bge-m3` です。`--mode` が変えるのは起動前のモデル存在確認（Preflight）とログの表示だけです。

| モード | Preflight が要求するモデル | 実際に読み込まれる Embedding | 実サイズ |
|--------|--------------------------|---------------------------|--------|
| `full` | BAAI/bge-m3 + bge-reranker-v2-m3 | BAAI/bge-m3 | 約 4.4GB（再ランク込み） |
| `text`（既定） | BAAI/bge-m3 | BAAI/bge-m3 | 約 2.3GB（+ 再ランクで約 2.1GB） |
| `lite` | MiniLM-L12-v2（約 470MB。読み込まれません） | BAAI/bge-m3 | 約 2.3GB |
| `lite-en` | MiniLM-L3-v2（約 22MB。読み込まれません） | BAAI/bge-m3 | 約 2.3GB |
| `minimal` | なし（Preflight スキップ） | BAAI/bge-m3 | 約 2.3GB |
| `worker` | `worker.embedding_mode`（既定 `text`）に従う | BAAI/bge-m3 | 約 2.3GB |

LLM 側の要件は別途必要です（以前あった `--mock`＝LLM を呼ばずに動かす指定は撤去済みです）。本リポジトリの検証は Apple シリコン搭載のノート PC（メモリ 128GB）で行っています。

## Q5. 個人情報が入った文書はどう扱われますか

Cynovela は二段構えで PII（Personally Identifiable Information: 個人情報）を扱います（A-2）。

**Tier1（取込時マスキング）**: Publish のタイミングで `rag.py` の `_mtws_publish`（= `guardrail.mask_text_with_spans`）が走り、各チャンクから `tier="raw"`（生本文）と `tier="masked"`（マスク済み）の両系統を生成します。関係データベースの `chunks` テーブルには両方（`tier='raw'` は Fernet 暗号化）が保存されますが、ベクター側に作られる Collection は `{cid}__masked` の一組だけです（伏字前の層は撤去済み）。

**Tier2（回答時マスキング）**: チャットの回答 LLM 出力に対して `_mask_for_viewer` が動き、`admin` 以外のロールでは強制的にマスクを適用します（A-2 §7、`routers/chat.py:128-162`）。検索の引き先 Collection も `tier_for_role(role)` でロール別に切り替わります（`rag.py:1726`）。

**検出される PII 種別**: 一次系（正規表現）は URL / EMAIL / PHONE_JP / PHONE_LAND / CREDIT / MYNUMBER / PASSPORT / IPV4 の 8 種類。二次系（presidio + GiNZA）でさらに PERSON_JP / ORG_JP / LOC_JP / ADDRESS_JP / DATE_TIME などを追加検出します（A-2 §4）。

**保管庫暗号化**: `raw` tier の本文は `vault_enc.enc_raw()` を経由して Fernet で暗号化されてから SQLite / ChromaDB に保存されます（A-2 §8）。`masked` 側は検索性能のため暗号化しません（二重防御不要）。

**PII 検出モード**: `cynovela.yaml` の `pii_mode` キーで `lite`（正規表現のみ）/ `standard`（既定、Regex + GiNZA NER）/ `quality`（全機能）から選べます（A-1 §3）。

## Q6. 動かない機能はありますか

正直に書きます。スケルトン（インターフェイスのみで実体は `NotImplementedError` を投げる）の機能が以下に存在します（A-6 §1）。

| 機能 | ファイル | 状態 |
|------|---------|------|
| MLX Embedding | `providers/embedding.py:105` | 将来実装予定 |
| MLX Reranker | `providers/reranker.py:216` | 将来実装予定 |
| Qdrant VectorStore | `providers/vector_store.py:260-272` | 骨格のみ（add / search / delete / export / import すべて未実装） |
| LanceDB バックエンド | `utils/vector_store.py:34` | パッケージ未導入時に拒否 |
| GraphRAG 戦略 | `services/rag_strategies.py:116` | 将来実装予定 |
| エージェント実行基盤 | `services/agent_runtime.py` | 抽象宣言のみ（`AgentRuntime`）。実装したクラスは 1 つもありません |

明示的に廃止された機能（A-6 §2）:

- `/chat-popup` ルート（410 Gone を返却）
- `user_id` 単独ログイン（401 を返却、`username/password` 必須に変更）
- `/api/auth/users` のデモモード未認証許可（`admin` 認証必須に変更）

設定としては定義されているが、検索パイプラインへの統合が部分的な機能（A-3 §6, §11）:

- `confidence_threshold`（既定 0.50）は config に定義済みだが、低信頼度結果の除外ロジックは未統合。
- 構造化回答テンプレート（JSON 形式・タグ強制など）は未実装。回答は自由形式。

認証強制は `--demo` 起動でも掛かります（`Bearer demo-token-<user_id>` 形式の固定トークンは 2026-07-29 に廃止）。

## Q7. 今後の方向性は何ですか

RAG 品質の向上（Reranker の実体テスト、チャンク戦略の調整）が次の主要マイルストーンです。

<!-- BACKLOG: それ以外のロードマップ詳細（RAG 品質・JWT 認証・MCP 公開等）は CLAUDE.md にあるが、spec-raw では確認できない箇所がある。FAQ では一行のみとする。 -->

---
最終更新: 2026-05-26 / Alpha GA 対応版
