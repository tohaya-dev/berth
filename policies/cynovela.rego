package cynovela.authz

import rego.v1

# Phase G: 最小ポリシー一本。
# role = viewer かつ doc.sensitivity = high のとき 出力/エクスポートを deny。
# それ以外は allow（既定）。

default allow := true

allow := false if {
	input.role == "viewer"
	input.sensitivity == "high"
}

deny_reason := "viewer は high 機微度ドキュメントの出力/エクスポートを許可されていません" if {
	input.role == "viewer"
	input.sensitivity == "high"
}
