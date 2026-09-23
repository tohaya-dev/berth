"""Cynovela DB migrations パッケージ。

Stage R2 で新設。各 migration は migrations/{番号}_{名前}.py に置き、
`apply(conn)` と `rollback(conn)` を持つこと。
"""
