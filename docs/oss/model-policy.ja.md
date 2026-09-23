[English](model-policy.md) | 日本語

# 必須モデルのポリシー

ランタイムには、検索用とガードレール用のモデルが必要です。別途ダウンロードする場合でも、これらのモデルは機能の受け入れ条件に含まれます。

| コンポーネント | 固定したバージョン/リビジョン | 上流のライセンス | 提供方法 |
|---|---|---|---|
| BAAI/bge-m3 | 5617a9f61b028005a4858fdac845db406aefb181 | MIT | 明示的に新規ダウンロード。ファイルの SHA256 は locks/models.json |
| BAAI/bge-reranker-v2-m3 | 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e | Apache-2.0 | 明示的に新規ダウンロード。ファイルの SHA256 は locks/models.json |
| en_core_web_sm | 3.8.0 | MIT | イメージの Python 依存関係 |
| ja_core_news_sm | 3.8.0 | CC BY-SA 4.0 | イメージの Python 依存関係。上流の帰属表示と継承(share-alike)の条件を守る |

出典: https://huggingface.co/BAAI/bge-m3 , https://huggingface.co/BAAI/bge-reranker-v2-m3 , https://github.com/explosion/spacy-models 。ダウンロードしたモデルカードと、インストールされたパッケージのメタデータを確認しています。回答生成用モデルの重みは、同梱もダウンロードもしません。

`tools/download-rag-models.py --cache PATH` は固定リビジョンを使い、期待するハッシュをすべて検証します。実行時はキャッシュを読み取り専用でマウントしてください。2 つの BGE モデルの ID は、固定したローカルの参照(ref)を通じて解決されます。他の人が稼働させている環境からモデルキャッシュを入手してはいけません。

合成の HTTP プロバイダーは、プロトコルの動作を確かめるためだけのものです。利用者が選んだ実際の LLM での回答品質、そのライセンス、ホスティングの方針、外部への通信は、別途評価してください。

## サイズ・リビジョン・保存場所

**サイズ。** `locks/models.json` に載っている固定ファイルの合計は **4,586,590,960 バイト**(約 4.59 GB / 4.27 GiB)です。

| モデル | 固定ファイルの合計 | 最大のファイル |
|---|---|---|
| BAAI/bge-m3 | 2,293,331,623 バイト | `pytorch_model.bin`、2,271,145,830 バイト |
| BAAI/bge-reranker-v2-m3 | 2,293,259,337 バイト | `model.safetensors`、2,271,071,852 バイト |

ダウンロードスクリプトが取得するのは `locks/models.json` に載っているファイルだけです。上流リポジトリにあるそれ以外のファイル(たとえば bge-m3 の ONNX 版)はダウンロードしません。

**固定リビジョン。** 各モデルは上の表のコミットで取得します。すべてのファイルを `locks/models.json` のバイト数と SHA256 と照合し、一致しなければ `FAIL: model checksum mismatch` でスクリプトが止まります。照合後、スクリプトは固定リビジョンをキャッシュの `refs/main` に書き込むので、実行時にはその固定スナップショットが使われます。

**ダウンロード時間。** 回線とディスクによって変わります。参考として、このリリースの検証に使った回線では、全ファイルの SHA256 検証を含めて約 4.5〜6.5 分かかりました。ネットワーク転送だけなら約 2 分(毎秒およそ 35〜40 MB)です。遅い回線ではもっと長く見込んでください。

**保存場所。** Linux / WSL2 の構成では、キャッシュは `$BERTH_DATA_DIR/models`(例: `$HOME/.local/share/berth/models`)に Hugging Face のキャッシュ形式(`models--BAAI--bge-m3/`、`models--BAAI--bge-reranker-v2-m3/`)で置かれます。`ops/linux.sh install` は、このディレクトリを API と worker の Pod の `/app/store/models` に読み取り専用でマウントします。Pod は uid 10001 で動くので、ファイルは誰でも読める権限のままにしておいてください。モデルは `ops/linux.sh backup` の対象外です。自分で写しを保管するか、改めてダウンロードしてください。

**実行時はオフライン。** イメージは `HF_HUB_OFFLINE=1` と `TRANSFORMERS_OFFLINE=1` を設定していて、init コンテナや起動処理がモデルをダウンロードすることはありません。モデルは `ops/linux.sh install` の前にダウンロードしておいてください。足りないモデルを Pod が取りに行くことはできません。spaCy モデルはイメージのビルド時に固定版の wheel として入るので、イメージのビルドにはネットワーク接続が必要ですが、動作中の Pod には必要ありません。
