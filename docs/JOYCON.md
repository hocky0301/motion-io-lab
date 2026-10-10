# Joy-Con（GameController）: 何を確かめる道具か、何がまだ分かっていないか

`examples/joycon_probe.swift` は macOS の GameController が**何を見せるか**を調べるプローブで、モーターは動かさない。この Mac では **Joy-Con を接続して動かしていない**。2026-10-09 にこの Mac で実行したときの接続数は 0 台だった（`controllers_at_start: 0`）。

## 確認できたこと（この Mac、コンパイルと実行）

- `swiftc -O examples/joycon_probe.swift` が警告なしで通る（古い `hasAttitudeAndRotationRate` は macOS 11 で非推奨なので `hasAttitude` と `hasRotationRate` に分けた）。
- `--clock-check` が出す `host_monotonic_ns` は、同時に測った Python の `time.monotonic_ns()` の区間（呼び出しの前後）に入った。両者とも `CLOCK_UPTIME_RAW` 系で、Python 側のログと同じ時計として扱える。
- 出力は 1 行 1 JSON を毎回フラッシュする（バッファに溜めない。ファイルへのリダイレクトでも、行が出た時点で読める）。パイプで記録器へ渡せる。
- 接続 0 台のとき、`waiting` と `done` が出て正常終了する。

## プローブが調べる項目（Joy-Con で埋める表）

| 項目 | プローブの出力キー | 結果 |
|---|---|---|
| 認識されるか | `connected` が出るか | （未確認） |
| `productCategory` | `category` | （未確認） |
| 拡張ゲームパッド / マイクロゲームパッド | `has_extended_gamepad` / `has_micro_gamepad` | （未確認） |
| ボタン・軸・方向パッドの名前 | `button_names` / `axis_names` / `dpad_names` | （未確認） |
| モーションセンサーのオブジェクト | `has_motion` | （未確認） |
| センサーを手動で有効化する必要 | `motion_sensors_require_manual_activation`（真ならプローブが `sensorsActive = true` にする） | （未確認） |
| 姿勢 / 回転速度 | `motion_has_attitude` / `motion_has_rotation_rate` | （未確認） |
| 重力 / ユーザー加速度 | `motion_has_gravity_and_user_acceleration` | （未確認） |
| ウィンドウ無しのコマンドラインで入力が届くか | `input` イベントが出るか（`shouldMonitorBackgroundEvents = true` を設定済み） | （未確認） |
| 単体の Joy-Con（L / R）の見え方 | 上の項目を L・R それぞれで | （未確認） |

「Bluetooth で接続できた」ことと「姿勢が取れた」ことは別。表を埋めて、取れた項目だけを結果として書く。

## 使い方（Joy-Con を Mac のシステム設定でペアリングしてから）

```bash
swiftc -O examples/joycon_probe.swift -o joycon_probe
./joycon_probe --seconds 30 | tee outputs/joycon-probe-YYYYMMDD.jsonl      # 能力表と入力の確認
./joycon_probe --seconds 60 | python -m motion_io_lab record --source "joycon:Left Thumbstick" \
    --out outputs/jc --session-id jc1 --trial-id jc-rec-00                  # 要素名は上の出力の localizedName
```

標準入力のパイプは端末ではないので、`--target hardware`（実機。端末での確認入力が要る）とは組み合わせられない。**録音は `--target dry`（既定）または `loopback` で行い、実機へは録音した試行を `play --target hardware` で再生する。**

`--source joycon:<要素名>` は、その要素の値（スティックなら x 軸、ボタン・軸ならそのまま）を [-1, 1] に丸めて 1 本の軸として記録する。要素名は `input` イベントの `element` の綴りと一致させる。**この経路は合成の行（`tests/test_inputs.py`）でしか確認していない。**

## まだ言えないこと

- Joy-Con の姿勢が取れた、スティックで操作できた、など、上の表の「結果」が空の項目。
- ESP32-S3 へ Joy-Con を直接つなぐ構成（この構成は検討・検証していない。Joy-Con は Mac が受ける）。
