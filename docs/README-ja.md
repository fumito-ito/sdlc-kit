# sdlc-kit

[English](../README.md) | 日本語

Anthropic の [AI-native SDLC playbook](https://claude.com/resources/articles/the-ai-native-sdlc-playbook) を、ローカル開発で再現性高く回すためのプラグインです。Claude Code と Codex の両方に、このリポジトリ1つから同じものを配ります。

- 変更ごとに `intent.md` → `spec.md` → `plan.md` → 実装 → `review.md` を `docs/changes/<日付>-<slug>/` にコミットし、各段階は前段の成果物を読んで始めます。
- スキル（指示）は「守られやすくする」層、hook・git hook・CI は「破れなくする」層として分けています。
- レビューは書いたエージェントとは別のエージェントが行います（Claude Code の作業は Codex が、Codex の作業は Claude Code がレビュー）。

## 構成

```
sdlc-kit/
├── .claude-plugin/marketplace.json     # Claude Code 用カタログ
├── .agents/plugins/marketplace.json    # Codex 用カタログ
└── plugins/sdlc/
    ├── .claude-plugin/plugin.json      # Claude Code 用マニフェスト
    ├── .codex-plugin/plugin.json       # Codex 用マニフェスト（name/version は同一）
    ├── skills/                         # 両ツール共通（SKILL.md）
    │   ├── sdlc-init/   sdlc-intent/  sdlc-spec/
    │   └── sdlc-plan/   sdlc-fix/     sdlc-review/
    ├── hooks/hooks.json                # 両ツール共通の hook 定義
    ├── agents/verifier.md              # Claude Code 用サブエージェント
    ├── bin/sdlc.py                     # CLI・ガード・ゲートの本体（Python 3.9+ 標準ライブラリのみ、1ファイル）
    └── templates/                      # 成果物テンプレと、各リポジトリに展開するファイルの雛形
```

## インストール

**Claude Code**

```
/plugin marketplace add fumito-ito/sdlc-kit
/plugin install sdlc@sdlc-kit
```

**Codex**

```sh
codex plugin marketplace add fumito-ito/sdlc-kit
codex plugin add sdlc@sdlc-kit
```

Codex は plugin 同梱の hook を信頼するまで実行しません。インストール後（と plugin 更新のたび）に Codex で `/hooks` を開き、sdlc の hook を trust してください。

`python3`（3.9 以上。macOS 標準の `/usr/bin/python3` で可）と `git` が必要です。クロスレビューには `claude` と `codex` の両 CLI を使います。

## リポジトリへの導入

対象リポジトリで Claude Code か Codex を開き、「このリポジトリに sdlc-kit を導入して」と頼むか、`sdlc-init` スキルを呼びます（Claude Code では `/sdlc:sdlc-init`）。スキルは3段階で進みます。

1. **展開（決定的）** — plugin 同梱の `sdlc.py init` が雛形を置きます。何度実行しても同じ結果になり、既存ファイルは上書きしません。
2. **調整（判断）** — エージェントがスタックを調べ、`verify` コマンドや保護パスを `sdlc.py config set` で設定し、AGENTS.md のプロジェクト節を書きます。
3. **確認** — `sdlc.py doctor --run-verify` で、仕組みが実際に動くことを確かめます。

展開されるもの:

| パス | 所有 | 内容 |
|---|---|---|
| `.sdlc/config.json` | リポジトリ | repo 固有値の単一ソース（下記） |
| `.sdlc/bin/sdlc.py` | kit（上書き更新） | plugin と同じ CLI のコピー。CI と git hook は plugin なしでこれを使う |
| `.sdlc/templates/*.md` | リポジトリ | 成果物テンプレ（編集可） |
| `.sdlc/githooks/pre-commit`, `pre-push` | kit | `core.hooksPath` で有効化 |
| `docs/changes/` | リポジトリ | 成果物の置き場 |
| `AGENTS.md` | リポジトリ（マーカー区間のみ kit） | 共通の指示。Codex はそのまま、Claude Code は CLAUDE.md の `@AGENTS.md` 経由で読む |
| `CLAUDE.md` | リポジトリ（マーカー区間のみ kit） | `@AGENTS.md` と Claude Code 固有の補足 |
| `REVIEW.md` | リポジトリ | レビュー方針 |
| `.github/workflows/sdlc-gate.yml` | kit | CI ゲート |
| `.github/workflows/sdlc-verify.yml` | リポジトリ | `verify` を CI で実行（Xcode なら `macos-latest` に） |
| `.claude/settings.json` | マージ | marketplace と plugin の宣言、sdlc.py の許可、`.env` 読み取り拒否 |

kit 所有のファイルは `managed-by: sdlc-kit` マーカー付きで、plugin 更新後に `sdlc-init` を再実行すると最新化されます。マーカーのない同名ファイルがあれば触らずに `skipped` と報告します。lefthook / husky / pre-commit を使っているリポジトリでは `core.hooksPath` を変えないので、それぞれの設定から `python3 .sdlc/bin/sdlc.py check --staged`（pre-commit）と `check --pre-push`（pre-push）を呼んでください。

## 日々の流れ

| ステージ | スキル | 成果物 | 決めるのは |
|---|---|---|---|
| Plan | `sdlc-intent` | `intent.md` | ユーザー（`approve` → accepted） |
| Design | `sdlc-spec` | `spec.md`（懸念点を先頭に） | ユーザー（accepted） |
| Build | `sdlc-plan` | `plan.md` → 実装 → `verify` | ユーザー（approved）。承認前は実装しない |
| Test | `sdlc-fix`（バグ時） | 失敗するテストを先にコミット、テストをロックして修正 | ロック解除はユーザー |
| Deploy | `sdlc-review` | `review.md`（もう一方のエージェントが read-only で作成） | マージはユーザー（PR） |

承認はユーザーが端末で行います。

```sh
python3 .sdlc/bin/sdlc.py approve            # 現在の変更で、次に draft の成果物を承認
python3 .sdlc/bin/sdlc.py approve --spec-skipped "1行の修正"   # spec を省いて plan を承認
```

エージェントが `approve` を実行したり、frontmatter の status を accepted/approved に書き換えたりすると hook がブロックします。

コミットには `SDLC-Change: <id>` トレーラーを付けます。`plan_required_paths` に触れる変更は、承認済み plan.md を参照していないと pre-push と CI で落ちます。typo 修正など本当に小さな変更は `SDLC-Exempt: <理由>` で通せます（理由は履歴に残ります）。

## CLI

`python3 .sdlc/bin/sdlc.py <command>`（`--help` で詳細）

| コマンド | 用途 |
|---|---|
| `new-change <slug> --title "…" --source idea\|bug\|alert\|scan\|incident` | 変更ディレクトリと intent.md を作る（日付は config のタイムゾーン、重複時は `-2`） |
| `add [id] spec\|plan\|review` | テンプレから成果物を追加 |
| `approve [id] [--reject] [--spec-skipped "…"]` | 次の draft を承認（人間用） |
| `set-status <id> <artifact> <status>` | status 変更（前段との整合をチェック） |
| `status [--json]` / `use <id>` | 一覧 / 現在の変更を切り替え |
| `verify` | 設定した verify コマンドを実行し結果を記録 |
| `review [id] --reviewer codex\|claude [--prompt-only]` | diff を固定して、もう一方のエージェントに read-only でレビューさせる |
| `test-lock on\|off\|status` | バグ修正中のテスト編集ロック |
| `check [--staged \| --pre-push \| --base REF]` | 成果物 lint・plan ゲート・シークレット検査（git hook / CI 用） |
| `config get\|set <key> <json>` | `.sdlc/config.json` の読み書き |
| `doctor [--run-verify]` | セットアップが本当に動いているかを確認 |

## ガードとゲート

| 守ること | エージェント hook（早期） | git hook | CI |
|---|---|---|---|
| 承認済み plan なしで本番コードを変えない | — | pre-push | `sdlc-gate` |
| 成果物の形式・status の整合 | — | pre-commit / pre-push | `sdlc-gate` |
| シークレットを書かない | 編集内容を検査 | pre-commit（staged） | `sdlc-gate`（diff） |
| 生成物・ロックファイル・kit 管理ファイルを手編集しない | ブロック | — | — |
| 承認は人間だけ | `approve` / status 書き換えをブロック | — | — |
| バグ修正中にテストを書き換えない | テストロック | — | — |
| `--no-verify` や `core.hooksPath` 変更で git hook を外さない | ブロック | — | — |
| `.env` を読まない | ブロック（Claude Code は settings の deny でも） | — | — |

エージェント hook は便利な早期フィードバックであって、完全な境界ではありません（シェル経由の書き込みなどは素通りします）。最終的な保証は、どのツールで書いても手で書いても効く git hook と CI に置いています。hook 自体が不正な入力や内部エラーに当たった場合は、作業を止めないよう許可側に倒します。

hook 定義は `hooks/hooks.json` の1つを両ツールで共有しています。どちらもツール呼び出しを stdin の JSON で渡し、exit 2 をブロックとして扱い、Codex も互換のために `CLAUDE_PLUGIN_ROOT` を設定するためです。差分はファイル編集の表現だけで（Claude Code は `Edit`/`Write` の `file_path`、Codex は `apply_patch` のパッチ本文）、`sdlc.py` 側で同じ形に正規化しています。両ツールの入力例は `tests/fixtures/` にあります。

## 設定（`.sdlc/config.json`）

| キー | 既定 | 意味 |
|---|---|---|
| `verify` | `make verify` | build + test + lint を1コマンドで。失敗時は非0で終了 |
| `plan_required_paths` | `src/**` `Sources/**` `lib/**` `app/**` | 承認済み plan が必要な本番コード |
| `protected_paths` | 生成物・ロックファイル | エージェントが手編集してはいけないパス |
| `test_globs` | 一般的なテスト配置 | テストロックの対象 |
| `format_command` | `""` | 編集後に1ファイルずつ実行（`{file}`） |
| `timezone` | 導入時のシステム設定 | 変更 ID の日付 |
| `base_branch` | 自動検出 | plan ゲートの比較先 |
| `language` | `ja` | 成果物とレビューの言語 |
| `reviewer` | `codex` | `review --reviewer auto` を端末から使ったときの既定 |
| `human_approval_only` | `true` | 承認系操作をエージェントに許さない |
| `kit_min_version` | 導入時の版 | これより古い sdlc.py では check が失敗する |
| `tool_min_versions` | claude 2.1.0 / codex 0.128.0 | doctor が警告する最低版 |

パスのパターンは gitignore 風です（`/` を含むものはルート基準、含まないものはどの階層でも一致、`**` 対応、親ディレクトリの一致も含む）。

## 開発

```sh
python3 -m unittest discover -s tests -v   # Python 3.9 / 3.13、Linux / macOS で CI 実行
claude plugin validate . && claude plugin validate plugins/sdlc
python3 scripts/release.py 0.2.0           # sdlc.py・両マニフェスト・marketplace の版を一括更新
```

CHANGELOG を書いてタグ（`v0.2.0`）を打ち、push します。利用側は Claude Code では marketplace の更新、Codex では `codex plugin marketplace upgrade` で取り込み、各リポジトリで `sdlc-init` を再実行すると repo 側のファイルが更新されます（成果物の frontmatter には作成時の `kit: sdlc@<version>` が残ります）。
