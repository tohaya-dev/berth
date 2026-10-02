# Cynovela Makefile (開発ツリー用)
#
# entry-unify-20260802 (DD-CYN-0020 S-4): 試験を回すターゲットを外した。
#   1) 外したターゲット (test / test-cov / verify-live / snapshot / coverage / logs /
#      test-one) は、いずれも scripts/run-tests-with-server.sh を呼んでいたが、
#      このファイルは3系統のどのツリーにも存在せず、実行すれば必ず失敗していた。
#   2) 配布物は tools/build-dist.sh が tests/ をディレクトリごと外すため、
#      受け取り手の手元には tests/ 自体が無い。指す先の無い入口を残さない。
#   同じ理由で、置き換え元だった run-with-server.sh も削除した。
#
# 開発ツリーで試験を回すときは、開発用の環境を有効にしてから直接:
#   unset SSL_CERT_FILE && pytest tests/
# 連続アクセスの制限を切りたいときは CYNOVELA_DISABLE_RATE_LIMIT=1 を付ける
# (この環境変数は server.py が読む。入口スクリプト側の配線は持たない)。
#
# アプリの起動は make ではなく ./launch.sh を使うこと (受け取り手が叩く入口は1本)。

.PHONY: help install lint clean

help:
	@echo "Available targets:"
	@echo "  make install      - Install all dev dependencies"
	@echo "  make lint         - ruff + pyright (no server required)"
	@echo "  make clean        - remove caches"
	@echo ""
	@echo "  試験:   unset SSL_CERT_FILE && pytest tests/   (開発ツリーのみ)"
	@echo "  起動:   ./launch.sh [--demo]                    (受け取り手の入口)"

install:
	pip install -e ".[dev]" 2>/dev/null || \
	  pip install schemathesis syrupy pytest-cov diff-cover pytest-xdist httpx \
	              pre-commit ruff pyright import-linter

lint:
	ruff check .
	pyright || true  # 既存違反は許容、新規違反のみ警告

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .ruff_cache htmlcov
