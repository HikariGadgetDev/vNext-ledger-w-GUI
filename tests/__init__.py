# root/vnext-ledger/tests/__init__.py


# NOTE(vNext): security / startup-guard
# prod モード時に SESSION_SECRET の長さを検証したい。
# 最低 32 bytes 未満は起動時に例外。
# （Vercel/Turso デプロイ時の事故防止）
