# 既知の制限事項

> **このドキュメントについて**
> Cynovelaは、AI基盤ツールのコンセプトを個人が手を動かして理解するために作った
> 完全非公式の学習ツールです。商用製品・公式実装ではありません。
> 実装はすべてオリジナルで、FastAPI / SQLite / ChromaDB / BGE-M3 / ローカルLLM
> という OSS スタックで構成されています。
> 会社・製品の公式見解を一切代表しません。

このドキュメントは、Cynovela の現時点で未実装・制限のある事項を整理したものです。学習目的のツールであり、商用同等の堅牢性を備えていない部分があります。利用前に必ずご確認ください。

---

## 1. 伏字（マスキング）でできないこと

**この節がいちばん重い節です。** 伏字は「個人情報を隠す仕組み」ですが、隠せる範囲には
はっきりした限界があります。**伏字が当たったからといって、資料の中の個人情報が
すべて消えたわけではありません。** 隠しきれないものが必ず残ります。

### 1.1 決まった形をした 13 種類しか、規則では取れない

`guardrail.py` の `PII_PATTERNS` に書かれている種別は、次の 13 種類だけです。

| 種別 | 何を指すか |
|------|-----------|
| `URL` | http / https で始まるアドレス |
| `EMAIL` | 電子メールアドレス |
| `PHONE_JP` | 携帯電話番号（070 / 080 / 090） |
| `PHONE_LAND` | 固定電話番号 |
| `CREDIT` | クレジットカード番号 |
| `MYNUMBER` | マイナンバー（個人番号） |
| `PASSPORT` | 旅券番号（英字 2 + 数字 7） |
| `IPV4` | IPv4 アドレス |
| `PASSWORD` | 「パスワード: ○○」のようなラベル付きの値 |
| `APIKEY` | API キー・アクセストークン |
| `PRIVATEKEY` | 秘密鍵のブロック（`-----BEGIN ... PRIVATE KEY-----`） |
| `SSN` | 米国の社会保障番号（3-2-4 の形） |
| `IBAN` | 国際銀行口座番号 |

これ以外のものは、規則では 1 件も伏字になりません。たとえば社員番号・顧客番号・
契約番号・口座番号（IBAN 以外）・車両番号・保険証番号などは、対象外です。

規則は「形」で当てています。形が崩れていれば当たりません。逆に、形が偶然一致した
無関係な数字は伏字になります（12 桁ちょうどの数値がマイナンバーとして伏字になるのは
その例で、漏れを防ぐほうを優先した結果として意図的にそうしてあります）。

### 1.2 氏名と住所は言語解析まかせ。`lite` にすると一切伏字にならない

氏名と住所は、上の 13 種類には入っていません。形が決まっていないので規則では取れず、
言語解析（NER）の側で扱っています。

- 設定は `cynovela.yaml` の `pii_mode` です。既定は `standard` です。
- `standard` のときだけ、`PERSON_JP`（氏名）と `ADDRESS_JP`（住所）が働きます。
- **`pii_mode` を `lite` にすると、氏名も住所も一切伏字になりません。**
  `lite` は正規表現だけで判定する道へ切り替わり、氏名・住所の認識器は
  そもそも組み立てられません（`utils/metadata/pii.py` の `get_active_recognizers()`
  と `detect_pii()` を参照）。

氏名の判定は、姓名として登録のある語しか当たりません。珍しい姓名、外国人名の
カタカナ表記、あだ名、役職と一体になった書き方などは取りこぼします。
住所も、都道府県から始まる書き方と郵便番号の形しか見ておらず、
「本社ビル 3 階」「◯◯支店」のような書き方は住所として扱いません。

### 1.3 組織名と地名は、名前だけあって中身がない

`get_active_recognizers()` は `ORG_JP`（組織名）と `LOC_JP`（地名）も返します。
名前は返りますが、**それを供給する認識器は 1 つも登録されていません**（ツリー全体で、
この 2 つの語はこの 1 行にしか出てきません。実際に登録されるのは `PERSON_JP` を出す
`GinzaNerRecognizer` と `ADDRESS_JP` を出す住所認識器の 2 つだけです）。

つまり **組織名も地名も伏字になりません。** 一覧に名前があるからといって、
働いていると思わないでください。

### 1.4 言語モデルが入っていないと、`standard` でも規則だけに退行する

氏名・住所の判定には spaCy / GiNZA の言語モデルが要ります。これが入っていない環境では、
`pii_mode` が `standard` のままでも、判定は正規表現だけに退行します。

起動時に `launch.sh` が存在を確かめ、無ければ次の警告を出します。

```
spaCy モデル '...' がありません (standard PII が正規表現に退きます)。
```

**この警告が出ているときは、氏名も住所も伏字になっていません。** 起動は止まらないので、
警告を読み飛ばすと、伏字が効いているつもりのまま使ってしまいます。

### 1.5 パスワードと API キーは、空白・日本語・改行で切れる

`PASSWORD` と `APIKEY` の規則は、値の部分を ASCII の図形文字だけで拾います
（`guardrail.py` の該当ブロックに「値は ASCII 図形文字 `[!-~]` のみ（空白・CJK で必ず切れる）」
と明記してあります）。このため、次のものは取れません。

- 値の途中に空白が入っているもの（`パスワード: abc def`）→ `abc` までで切れます
- 値の途中に日本語が入っているもの
- 改行をまたぐ値（ラベルと値の間の空白は タブと空白だけに限られ、改行はまたぎません）

また、ラベルと値の間に区切り（`:` `：` `=` `＝` または「は」）が無いものは
対象外です。`password protection` のような一般的な語を巻き込まないための設計で、
その代償として「パスワード　abcdefgh」のような空白区切りは取れません。

### 1.6 PDF から取り出した文字に空白が入り、電子メールが伏字を逃れることがある

PDF は文字の並びではなく、文字を置く位置の情報として組まれています。そこから文字を
取り出すとき、元の見た目には無かった空白が語の途中に入ることがあります。

伏字の規則は文字の並びを見ているので、空白が 1 個入るだけで当たらなくなります。
電子メールアドレスの場合、次のようになります。

- `taro.yamada@example.co.jp` → 伏字になる
- `taro.yamada @example.co.jp` → **伏字にならない**（`@` の前で切れる）
- `taro.yamada@ example.co.jp` → **伏字にならない**（`@` の後ろで切れる）
- `taro. yamada@example.co.jp` → `yamada@example.co.jp` の部分だけが伏字になり、
  `taro.` は残る

同じことは電話番号・カード番号など、他の種別でも起こり得ます。
**PDF を取り込んだときは、伏字が当たっているかどうかを目で確かめてください。**
取り込み後のチャンク一覧から実際の本文を見られます。

### 1.7 管理者が原文を見られるかどうかは、宛先と鍵しだい

まず前提として、**ベクター索引に入るのは伏字済みの本文だけです**（`{cid}__masked` の
一組のみ。伏字前の層 `{cid}__raw` は作成経路から撤去済みで、ロールによらず検索は常に
伏字済み層で行われます — `rag.py` の `rag_retrieve` 冒頭）。

原文は関係データベース（`chunks` / `parent_chunks` の `tier='raw'` 行。単体構成では
SQLite、データ層分離構成では Postgres）に暗号化して保管してあり、**役割が `admin` の
ときだけ**、検索で当たった箇所をそこから復号して差し替えます
（`rag.py` の `tier_for_role` と `_vault_substitute_raw`）。

ただし、次の 3 つの場合は管理者でも伏字済みのものしか出ません。

1. **回答用 LLM の宛先が外部を向いているとき。**
   `routers/chat.py` の `_effective_send_tier` は、送り先が自マシン内・
   コンテナのホスト側・私設アドレス帯・**クラスタ内 Service DNS（`*.svc` /
   `*.svc.cluster.local`）** のいずれでもない場合、役割によらず伏字済みへ落とします。
   宛先を判定できないときも伏字済みに倒します（`_is_local_send_endpoint`）。
2. **金庫（暗号化された保管）の鍵が合わないとき。**
   `_vault_substitute_raw` が復号できない行（`enc:` のまま残る行）は、
   伏字済みの本文をそのまま使います
   （画面に暗号文を出さないため、あえてそうしてあります）。
   鍵を入れ替えると、それ以前に取り込んだ資料は管理者でも原文を読めなくなります。
3. **質問文そのもの。**
   質問文への伏字は役割で分けていません（`rag.py` の `_mask_query_for_retrieval`）。
   管理者が質問文に IP アドレスを書いても、その IP は伏字になってから検索と LLM へ
   渡ります。

以前の版のこの文書には「管理者でも IP が伏字になる（調査中）」と書いてありましたが、
上の 3 つが理由です。不具合ではありません。

---

## 2. 外部への送出が止まる条件

これは「できないこと」というより「あえて止めていること」です。
判定できないときは必ず止める側に倒します。そのぶん、機能が働かないように見えます。

宛先が「外へ出ないか」の判定は `routers/chat.py` の `_is_local_send_endpoint` に
一本化されています。ローカル扱いになるのは loopback / コンテナの host-gateway /
RFC1918 私設アドレス / link-local / **クラスタ内 Service DNS（`*.svc` /
`*.svc.cluster.local`）** です。それ以外の DNS 名・公開 IP・判定不能はすべて
外部扱い（＝止める側）です。

- **CRAG の下読み。** 検索結果が質問に足りているかを LLM に下読みさせる処理は、
  宛先が外部のとき、および**宛先を判定できないとき**は実行しません（`rag.py`）。
  実行しない場合は「検索結果をそのまま採用」に落ちます。
  画面には `[CRAG] 非ローカル宛のため下読みをスキップします` と出ます。
- **会話の要約と、次の質問候補の生成。** どちらも同じ判定を使い、
  外部宛（判定不能を含む）では送らずに空を返します（`routers/chat.py` の
  `summarize_chat` と質問候補生成）。
- **役割による原文送出。** 宛先が外部なら、管理者であっても伏字済みのものだけを送ります
  （前述の `_effective_send_tier`）。
- **伏字が失敗したら止まる。** 質問文・回答のいずれも、
  伏字処理が例外で落ちた場合は 503 を返して処理を打ち切ります。
  伏字前のものを代わりに返すことはしません（`routers/chat.py`。SSE 経路では
  `type:error` を流して打ち切ります）。
- **伏字なし取り込みと外部埋め込みの組み合わせは拒否します。**
  古い版で作られた「伏字なし」（`raw_only`）のコレクションは、外部の埋め込みを
  有効にしている間は公開（publish）できません（`rag.py`。
  「raw_only コレクションは外部埋め込みプロバイダ有効時には publish できません」）。

---

## 3. 未実装の機能（NotImplementedError）

呼ぶと `NotImplementedError` になるもの、または抽象宣言だけのものです。以下のとおりです。

> 行番号は目安です（コードの編集ですぐにずれます）。確かめるときはファイル名と
> クラス・メソッド名で探してください。

### 3.1 Provider 抽象基底クラス（インターフェイスのみ）

| 場所 | メソッド | 状態 |
|------|--------|------|
| `providers/classifier.py:25` | `ClassifierProvider.classify` | 抽象（外部 Provider 差し替え用） |
| `providers/reranker.py:31` | `RerankerProvider.rerank` | 抽象 |
| `providers/reranker.py:34` | `RerankerProvider.test_connection` | 抽象 |
| `providers/embedding.py:24` | `EmbeddingProvider.embed` | 抽象 |
| `providers/embedding.py:32` | `EmbeddingProvider.test_connection` | 抽象 |
| `providers/vector_store.py:42-57` | `VectorStoreProvider`（add / search / delete_collection / export / import_data の 5 メソッド） | 抽象 |

### 3.2 Qdrant ベクターストア（骨格のみ）

| 場所 | メソッド | 状態 |
|------|--------|------|
| `providers/vector_store.py:260` | `QdrantVectorStore.add` | 未実装 |
| `providers/vector_store.py:263` | `QdrantVectorStore.search` | 未実装 |
| `providers/vector_store.py:266` | `QdrantVectorStore.delete_collection` | 未実装 |
| `providers/vector_store.py:269` | `QdrantVectorStore.export` | 未実装 |
| `providers/vector_store.py:272` | `QdrantVectorStore.import_data` | 未実装 |

Qdrant サーバーの起動が前提となるため、現状は `chromadb` バックエンドのみで動作確認しています。

### 3.3 MLX 系（Apple Silicon 最適化、将来実装）

| 場所 | メソッド | 状態 |
|------|--------|------|
| `providers/embedding.py:105` | `MLXEmbeddingProvider.embed` | 将来実装予定 |
| `providers/reranker.py:216` | `MLXRerankerProvider.rerank` | 将来実装予定 |

### 3.4 ベクターストアバックエンドの初期化エラー

| 場所 | 内容 |
|------|------|
| `utils/vector_store.py:32` | Qdrant: `pip install qdrant-client` と `CYNOVELA_VECTOR_BACKEND=qdrant` の案内 |
| `utils/vector_store.py:34` | LanceDB: `pip install lancedb` と `CYNOVELA_VECTOR_BACKEND=lancedb` の案内 |
| `utils/vector_store.py:43, 80, 87, 97` | 未対応バックエンド指定時に `NotImplementedError` |

### 3.5 GraphRAG 戦略（将来実装）

| 場所 | メソッド | 状態 |
|------|--------|------|
| `services/rag_strategies.py:116` | `GraphRAGStrategy.retrieve` | 将来実装予定 |
| `services/rag_strategies.py:122` | `GraphRAGStrategy.build_graph` | 将来実装予定 |
| `services/rag_strategies.py:133` | `GraphRAGStrategy.traverse_with_acl` | 将来実装予定 |

### 3.6 エージェント実行基盤（抽象宣言のみ）

| 場所 | メソッド | 状態 |
|------|--------|------|
| `services/agent_runtime.py` | `AgentRuntime.run` | 抽象宣言のみ |
| `services/agent_runtime.py` | `AgentRuntime.call_tool` | 抽象宣言のみ |
| `services/agent_runtime.py` | `AgentRuntime.available_tools` | 抽象宣言のみ |

`services/agent_runtime.py` はファイル全体で 60 行しかなく、中身は `Tool` / `ToolCall` /
`ToolResult` / `AgentResult` の型定義と、抽象基底クラス `AgentRuntime` の宣言だけです。
**`AgentRuntime` を実装したクラスは 1 つもありません。** つまり
**エージェントに作業をさせることはできません。**

---

## 4. MCP（Model Context Protocol）の制限

MCP サーバー（`mcp_server.py`）は 11 ツールを提供しますが、現時点では実行環境に制限があります。

- MCP サーバーの実行は **conda 環境前提**です。
- MCP 実行用の Python パスは環境変数 `CYNOVELA_MCP_PYTHON` で指定する想定です。
- MCP サーバーは内部で Cynovela の REST API に対して認証付きリクエストを送る構成です。

<!-- BACKLOG: MCP が conda 限定である技術的な理由は spec-raw に明示されていないため未確認 -->

---

## 5. JWT 認証は未実装

認証は `--demo` 起動でも強制されます（旧 `Bearer demo-token-<user_id>` 形式の固定トークンは 2026-07-29 に廃止）。

- トークン形式: `Bearer demo-token-{user_id}`
- JWT を用いた本番向け認証は未実装です。

このため、現時点では **本番運用を想定したアクセス制御は行えません**。次のタスク優先順では「JWT 認証導入」として位置付けられています。

---

## 6. BGE-M3 初回ダウンロードサイズと、`--mode` が切り替えないもの

初回起動時に Embedding モデル **BAAI/bge-m3（約 2.3GB）** のダウンロードが発生します。
同梱の `cynovela.yaml` は Reranker を有効（`rag.reranker_enabled: true` /
`reranker.provider: cross_encoder`）にしているため、実際に再ランクが走る場面では
追加で **BAAI/bge-reranker-v2-m3（約 2.1GB）** も要ります。

回線状況によっては数分から十数分かかります。

**`--mode` は、実際には切り替わらないものがあります。**
`--mode` の説明文（`server.py` の `add_argument("--mode", ...)`）にそのまま書いてあります。

- `lite` と `lite-en` は軽量モデルへの切り替えが**未配線**で、現状は既定の `text` と
  同じ `bge-m3` で動きます。切替を行う `create_embedding_provider()` は
  起動経路から呼ばれていません（呼んでいるのはテストだけです）。
- `minimal` は TF-IDF が未統合で、こちらも `bge-m3` が要ります。
  `minimal` は起動前のモデル存在確認（Preflight）を丸ごと飛ばすため、
  モデルを置いていない環境でも起動はしますが、最初の埋め込みで失敗します。
- **どのモードを選んでも、必要なモデルの大きさは変わりません。**

| `--mode` | Preflight が要求するモデル | 実際に読み込まれる Embedding | 実サイズ |
|--------|--------------------------|---------------------------|--------|
| `full` | bge-m3 + bge-reranker-v2-m3 | BAAI/bge-m3 | 約 2.3GB（+ 再ランクで約 2.1GB） |
| `text`（既定） | bge-m3 | BAAI/bge-m3 | 約 2.3GB（+ 再ランクで約 2.1GB） |
| `lite` | MiniLM-L12-v2（約 470MB。**読み込まれません**） | BAAI/bge-m3 | 約 2.3GB |
| `lite-en` | MiniLM-L3-v2（約 22MB。**読み込まれません**） | BAAI/bge-m3 | 約 2.3GB |
| `minimal` | なし（Preflight スキップ） | BAAI/bge-m3 | 約 2.3GB |

> **Preflight と実配線がずれている点に注意してください。** `lite` を指定すると
> 起動前に MiniLM の存在を確かめますが、そのモデルは一度も読み込まれません。
> 逆に既定の `text` は Reranker を Preflight しませんが、
> 同梱設定のままだと最初の再ランクで bge-reranker-v2-m3 を要求します。

起動時のログにも「`--mode <x>` の名目値 `<y>` は未配線」と出ます。

なお `--mode` にはもう 1 つ **`worker`** があります。これは埋め込みの階層ではなく役割の指定で、
uvicorn を起動せず Redis キューのジョブを消費します（埋め込みの階層は
`cynovela.yaml` の `worker.embedding_mode`、既定 `text` に従います）。

---

## 7. 横断検索は未実装

MCP ツールには `search_across_collections`（複数コレクションを横断する RAG 検索）が定義されていますが、UI 上での「ワークスペースをまたいだ横断検索」や「全社的なフリーテキスト検索」相当の機能は提供していません。

<!-- BACKLOG: GUI 上での横断検索 UI / API の有無は spec-raw に記載がないため未確認 -->

---

## 8. デモ DB の残骸

`--demo` を付けると、同梱のダミー資料が載ったデモのデータベース（`store/db/demo.db`）で
起動します。何も付けなければ本番＝**空のデータベース**（`store/db/cynovela.db`）です。

- **どちらも再起動で消えません。** 起動のたびに初期化されることはなく、
  デモで足した資料も次の起動に残ります。消したい場合は自分でファイルを消してください。
  投入処理はすべて「無ければ入れる」（`INSERT OR IGNORE`）で、
  データベースを消す処理はコードのどこにもありません（`db.py` の `init_db`）。
  管理者パスワードも、一度変えたあとは起動のたびに戻されることはありません。
- 分かれているのは**関係データベースのファイルだけ**です。
  ベクター索引の場所（`CYNOVELA_CHROMA`）は `--demo` の有無にかかわらず
  `store/vector/demo/chroma` に解決されます（`server.py` のパス解決が
  `paths.vector.demo` を固定で読むため、`cynovela.yaml` の `paths.vector.default` は
  どこからも読まれていません）。**デモと本番でベクター索引は共用されます。**
- デモ DB が見つからない状態でテストが回ると、テストが本来の検証になりません。
- セッション開始時には DB 実在確認（`audit_logs` 件数とベクター件数）を必ず行ってください。

---

## 9. RAG 設計上の留意点

### 構造化回答テンプレートは未実装

回答は自由形式（必要に応じて Markdown）で返ります。JSON や独自タグでの構造化応答は提供していません。引用機能（`[1][2]` 形式）は実装済みです。

### 信頼度しきい値（`confidence_threshold`）

config に `0.50` として定義済みですが、低信頼度時の自動フォールバック（一般知識モードへの切替など）は完全には統合されていません。

### Adaptive RAG の自己評価はルールベース

`evaluate_answer_quality()` は「回答が空 / 60 文字未満 / 否定表現がある」などの単純ルールで判定します。LLM ベースの自己評価ではありません。

### 取込時マスキング（Tier1）と回答時マスキング（Tier2）

Tier1 と Tier2 の二重防御が実装されていますが、PII 検出の網羅性は正規表現 + GiNZA NER（presidio フォールバック）の範囲に限定されます。

---

## 10. 廃止された機能

| 機能 | 状態 |
|------|------|
| `--mock` 起動 | 撤去済み。起動の選択肢に存在しません。モデル不要の起動もできません |
| `/chat-popup` ルート | 410 Gone を返す。フルスクリーンのチャット画面に移行 |
| `user_id` 単独のレガシーログイン | 撤去済み。`username`/`password` 必須 |
| `/api/auth/users` のデモ未認証許可 | 撤去済み。常時 admin 認証必須 |
| `--pii-mode` CLI 引数 | 廃止。`cynovela.yaml` の `pii_mode` キーで管理 |
| `curator` ロール | 撤去済み。有効ロールは `admin` / `viewer` の 2 値のみ（`rbac.md` を参照） |
| 伏字前ベクター層（`{cid}__raw`） | 撤去済み。ベクター索引は伏字済み一組のみ |

---

## 11. その他の補足

- ChromaDB の `PersistentClient` は誤ったパスを与えてもエラーにならず、空の DB を自動作成します。バックアップ復元時や環境変数設定時に注意してください。
- LM Studio API には `max_tokens` を渡さないでください。Reasoning モデルで思考用トークン予算が枯渇する原因となります。
- セッション開始時には DB 実在確認を必ず行ってください（`audit_logs` 件数と ChromaDB 件数の確認）。

---

## 12. 今後の優先タスク

参照元の仕様調査では、以下が次のタスク優先順として整理されています。

1. RAG 品質向上（Reranker の実体テスト、チャンク戦略の調整）
2. 安定性基盤（Embedding / Reranker 設定の YAML 永続化、エラー回復経路の堅牢化）
3. KnowledgeCatalog（Chunks ビューアの拡張、メタデータ検索、出典追跡）
4. JWT 認証導入（全モードで RBAC 強制）
5. MCP サーバー化（RAG Chat / Source 操作を外部公開）

---
最終更新: 2026-08-16 / 実装との突き合わせ反映版
