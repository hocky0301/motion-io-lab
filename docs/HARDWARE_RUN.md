# 実機で測る手順（現物の前で自分がやる）

このリポジトリのコードは、実機に通電・書込み・遠隔駆動をしたことが**一度もない**。この文書は、現物の前で 1 つの軸について「操作 → 保存 → 再生 → 観測」を通し、出力（校正、再現性、遅れなど）を実物の値で埋めるための手順。自動実行はしない。

実行前に必ず `docs/STATUS.md` の「実機で未検証」を読むこと。

## 0. 揃える物（手持ちの物だけ）

| 物 | 数量 | メモ |
|---|---|---|
| XIAO ESP32S3（通常版、ヘッダピン付き） | 1 | 既定のボード。ATOMS3 Lite のピン割当は未検証（`docs/BOARDS.md`） |
| PCA9685 搭載の 16ch サーボドライバ基板 | 1 | OE ピンの既定（プルダウンか）を基板ごとに確認 |
| MG90S サーボ | 1 | 可動範囲の機械的余裕を確保 |
| DC 可変アダプタ（3〜24 V / 3 A） | 1 | **5 V に設定**してテスターで確認してから接続 |
| DC ジャック端子 | 1 | |
| 非常停止ボタン（2NC） | 1 | 接点の定格電流・DC 定格を現物資料で確認（下の「通電してよい条件」） |
| ブレッドボード、ジャンパー線 | 一式 | 太いサーボ電流を流さない配線にする |
| テスター | 1 | 通電前後の電圧確認と OE の電位確認 |
| 緑色のマーカー（シール等）とサーボ固定具 | 少量 | マーカーはカメラの背景と区別できる色 |
| カメラ（Mac の内蔵、またはスマホのカメラ） | 1 | 取得できる経路だけ使う。固定する |

## 1. 論理配線

`docs/wiring_logical.svg`（端子の役割を示す論理図。現物のどの穴に挿すかを決めた図ではなく、写真つきの配線図でもない）。

- XIAO の 3V3 → PCA9685 の VCC（論理側 3.3 V）。
- 共通 GND。
- D4 (GPIO5) → SDA、D5 (GPIO6) → SCL、D0 (GPIO1) → OE（出典: Seeed の XIAO ESP32S3 のピン表、`docs/BOARDS.md`）。
- OE は物理的なプルアップで HIGH（出力無効）に保つ。ソフトの `digitalWrite(OE, HIGH)` はリセット直後から `setup()` までの間を守れない。
- サーボの動力: 外部 5 V → 定格確認済みの遮断（非常停止）→ PCA9685 の V+ 端子。**USB、3V3、信号線、ブレッドボードの細い線から取らない。**

## 2. 通電してよい条件（全部満たすまで通電しない）

1. 型番・端子・極性・定格を現物で確認した（PCA9685 基板の VCC と V+ が別端子であること、OE の既定の電位）。
2. 論理系（USB→XIAO→PCA9685 VCC）と動力系（外部 5 V→非常停止→V+）の電流経路を指で追って説明できる。
3. サーボを確実に固定し、身体が可動範囲に入らない。電源を切っても何も落ちない（電源を切ると保持力も失われる）。
4. 非常停止に手が届く。**非常停止の接点定格（電流・DC）、配線、復帰条件は自分で現物の資料を見て確認する。** 「2NC を 1 接点で電源遮断、もう 1 接点を状態入力」という案は、それだけでは認証された二重化安全回路ではない（故障検出、断線、停止後の機械動作、復帰手順が別に要る）。このリポジトリは根拠なしの直列接続を指示しない。
5. 外部 5 V をテスターで確認した（5 V ± 0.25 V 程度。XIAO の 3V3 や USB に繋がっていない）。
6. ソフトの停止（OE HIGH、STOP、DISARM）は非常停止の代わりではない。

## 3. 手順

作業ディレクトリはリポジトリのルート。ログは新しいセッションごとに新しいフォルダ（`outputs/hw-YYYYMMDD`）へ。**ログは追記も上書きもされない。**

### 3.1 通電しないで確認（USB だけ、サーボの動力は外したまま）

```bash
python -m pytest -q                                          # Mac 側が健全であること
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 code/firmware/guarded_servo   # docs/FIRMWARE_BUILD.md
arduino-cli upload -p <ポート> --fqbn esp32:esp32:XIAO_ESP32S3 code/firmware/guarded_servo   # 自分でやる。まだ HARDWARE_VERIFIED=false
python -m motion_io_lab console --port <ポート> --trial-id hw-console-00 --out outputs/hw-YYYYMMDD
# ファーム側の ARM 拒否を見るときだけ（通電前、動力は外したまま）。--allow-arm 無しだと ARM はホストが止める
python -m motion_io_lab console --port <ポート> --trial-id hw-console-01 --out outputs/hw-YYYYMMDD --allow-arm
```

コンソールで次を打つ。期待する返事:

| 打つ | 期待 |
|---|---|
| `HELLO` | `HELLO proto=2 fw=0.1.0 board=XIAO_ESP32S3 ... hw_verified=0 fault=0`、`epoch=` が毎回変わる |
| `ARM <epoch>`（2 本目の `--allow-arm` つきのコンソールでだけ打てる） | `REJECTED ARM reason=hardware_verified_false` |
| `STATUS` | `armed=0 enabled=0` |

D0（OE）の電位をテスターで測り、**電源投入直後と USB 抜き差し直後も HIGH（3.3 V）**であることを確認する。これが確認できなければ先へ進まない。

### 3.2 `HARDWARE_VERIFIED` を `true` にする

チェックリスト（上の 2.）を終えたら、`code/firmware/guarded_servo/guarded_servo.ino` の `constexpr bool HARDWARE_VERIFIED = false;` を**手で** `true` にして書込む。コミットしない（公開リポジトリは `false` のまま）。自動で変えるコードはない。

### 3.3 校正: パルス → 角度

サーボ動力を接続し、ホーンの可動範囲に物が無い状態で、各パルスを 2 秒保持して角度を分度器（または固定したカメラ画像）で読む。

```bash
for us in 1450 1475 1500 1525 1550; do
  python -m motion_io_lab probe --execute --port <ポート> --i-checked-hardware \
      --pulse-us $us --seconds 2 --out outputs/hw-YYYYMMDD --session-id hw1 --trial-id cal-$us
done
```

端末で `ARM <port>` の文字入力を求められる。読んだ角度を `calibration_points.csv`（`pulse_us,angle_deg`）に書き、

```bash
python -m motion_io_lab calibrate --points calibration_points.csv --out outputs/hw-YYYYMMDD/calibration.json --source hardware
```

出る `deg_per_us`、`rms_residual_deg`、`version`（ハッシュ先頭 8 桁）が校正の結果（下の 4 の表の「パルス→角度の校正」の行）。校正が無いと度に換算した解析はできない。

**校正の不確かさ**: 可動範囲は約 ±5°（±50 us）しかないので、分度器の読み誤差 0.5〜1° は `deg_per_us` を 10〜20 % ずらす。度で表す指標（4 の表の再現性・録音と再生の差・遅れ以外）にはこの系統誤差が乗る。`rms_residual_deg` は当てはまりの悪さで、読み誤差そのものではない。読み誤差の見積り（例: 同じ点を 3 回読んだばらつき）を書き添え、度の値は「±○ %」つきで報告する。差を**比べる**指標（録音と再生の差、同じ校正の中の SD）は系統誤差の影響を受けにくい。

### 3.4 カメラ

マーカー（緑）をホーンに付け、カメラを固定する。

```bash
python -m motion_io_lab camera-probe --list                       # 開けるカメラ番号（OS のカメラ許可が出る）
python -m motion_io_lab camera-probe --source 0 --frames 120 --session-id hw1 --trial-id cam-fps --out outputs/hw-YYYYMMDD
```

`examples/camera_config.json` をコピーして、`source`、`hsv_lower/upper`、`pivot_px`（ホーンの回転中心の画素）、`reference_angle_rad`（角度 0 のときのマーカーの方向）、`angle_scale_rad` を現物に合わせる。`camera-probe` のログで実際のフレーム間隔を見る（`host_after_read_ns` の差）。コンテナの fps は撮影レートの証拠ではない。赤いマーカーを使うときは、`hsv_lower` の H を `hsv_upper` の H より大きくして、色相が 0 をまたぐ範囲を指定できる（例 `[170,100,100]` 〜 `[10,255,255]`）。スマホのカメラは、OS の正規の設定で Mac が映像を取得できる場合だけ使う。

### 3.5 1 軸 × 3 動作 × 5 回: 記録 → 再生 → 観測

動作は `sweep`、`step_hold`、`look_wait_nod`（`motion_io_lab/motions.py`）。**試行 ID は `<動作>-rec` と `<動作>-rep01`〜`05`** にする（集計が名前で探す）。

**先に「動かさない試行」を取る（検出器の雑音の床）。** サーボは ARM せず、マーカーを付けたまま動かさずに、`observe` だけを 3 本回す。できればホーンの角度を変えて（0° と数度）もう 3 本（`still-04`〜`06`）。再生どうしのばらつきがこの床より大きくなければ、サーボの再現性は読めない。

```bash
for i in 01 02 03; do
  python -m motion_io_lab observe --config camera.json --out outputs/hw-YYYYMMDD --session-id hw1 \
      --trial-id still-$i --mode DRY_RUN --source recorded --seconds 10
done
```

**順番**: 録音 1 本 → 再生 5 本を動作ごとに続けると、温まり・保持位置の変化が動作と交絡する。3 動作を交互に回し（sweep → step_hold → look_wait_nod → sweep ...）、順番はくじで決めて記録する。各試行の前に、サーボを中央（1500 us）に戻して数秒待つ（開始の状態をそろえる。合成デモでは、前の試行の終わりの位置を持ち越すと観測の段が約 2 倍になる: `reference_results/ablation.json`）。

**観測の起動のしかた**: `play` / `record` は、`SerialLink` の待ち（約 1 秒）、HELLO、端末での `ARM <port>` の文字入力を経てから動き始める。人が打つ時間も含まれるので、`observe` の窓は動作より**十分に長く**（`--seconds 45` 以上）して、**先に起動**する。解析は最初の指令の時刻を 0 として切り出すので、窓の前後が余っていてよい（動作の途中で窓が閉じるほうが失敗）。確認の入力は `observe` が動き出してから打つ。

録音（人が操作。キーボードなら `--source keyboard`、スマホなら `--source touch`）:

```bash
# 別の端末で先に（録音・再生と同じ trial-id、同じ --out）
python -m motion_io_lab observe --config camera.json --out outputs/hw-YYYYMMDD --session-id hw1 \
    --trial-id sweep-rec --mode HARDWARE_ARMED --source hardware --seconds 45
# 観測が動いてから
python -m motion_io_lab record --target hardware --port <ポート> --i-checked-hardware \
    --source keyboard --out outputs/hw-YYYYMMDD --session-id hw1 --trial-id sweep-rec
```

再生（5 回。毎回 HELLO からやり直す＝毎回新しい epoch）。1 回ごとに、別の端末で `observe` を起動してから `play` を打つ:

```bash
python -m motion_io_lab observe --config camera.json --out outputs/hw-YYYYMMDD --session-id hw1 \
    --trial-id sweep-rep01 --mode REPLAY --source hardware --seconds 45
python -m motion_io_lab play --target hardware --port <ポート> --i-checked-hardware \
    --motion outputs/hw-YYYYMMDD/sweep-rec --out outputs/hw-YYYYMMDD --session-id hw1 --trial-id sweep-rep01
```

`rep02`〜`rep05` も同じ。集計:

```bash
python -m motion_io_lab summarize-session --root outputs/hw-YYYYMMDD --calibration outputs/hw-YYYYMMDD/calibration.json \
    --repeats 5 --still-trials still-01,still-02,still-03 --out outputs/hw-YYYYMMDD/session_summary.json
python -m motion_io_lab validate outputs/hw-YYYYMMDD
```

`summarize-session` はデモと同じコードで、`source` をログのヘッダから読む（`hardware`）。合成の結果と同じフォルダに混ぜない。出力の `observation_noise_floor` が動かさない試行の床、`repeat_sd_deg.observed_deg.pooled_sd_minus_floor` が床を引いた値。5 回の SD には大きな不確かさがある（相対で約 35 %）ので、区間か範囲を添えて読む。

### 3.6 切断 → 再開

再生中に **XIAO の USB ケーブル（Mac 側）**を抜く。**サーボの動力線には触らない**（それは 3.7 の状況で、別の試験）。XIAO は USB 給電なので、抜くとボードごとリセットされ、再接続すると DISARMED で起動する。

```bash
python -m motion_io_lab play --target hardware --port <ポート> --i-checked-hardware --resume continue \
    --motion outputs/hw-YYYYMMDD/sweep-rec --out outputs/hw-YYYYMMDD --session-id hw1 --trial-id sweep-resume-continue
```

中断したら、USB を挿し直してポートが現れてから Enter を押す。再開のたびに端末で `ARM <port>` の文字入力をもう一度求められる。ラッチした場合だけ `--allow-clear`（人が CLEAR を決めた）が要る。ボードがリセットされず電源だけ残った場合は、デッドマンの期限切れでラッチするので `--allow-clear` が要る。

### 3.7（任意）非常停止を押したときのログ

再生中にサーボの動力側の非常停止を押す。MCU は動いたままなので、ファームは `EXECUTED` と `applied_us` を返し続け、カメラの観測は動かない。「適用値は位置ではない」を実物で見せる材料になる。必ず手を離せる安全な状態で、ゆっくりした動作で行う。

**復帰に注意**: 動力を切っても PCA9685 は論理側の電源で生きていて、最後の適用値の PWM を出し続ける。非常停止を戻すと、サーボはその時点の適用値へ**一気に**動く（再生が進んでいれば、押した位置から離れている）。戻す前に次を守る。

1. 再生を止める（Ctrl-C）。`DISARM` して OE を HIGH にする（`console` で `DISARM`、基板の OE が HIGH になっていることをテスターで確認）。
2. ホーンに負荷が無く、人が可動範囲に触れない位置であることを確認する。
3. 戻した直後の跳びを想定して、ゆっくりした動作（`step_hold` を避ける）でだけ試す。

ソフトの `ESTOP`（`console` のコマンド）は、実物の非常停止ボタンとは別物。ソフトの名前に引きずられて混同しない。

## 4. 出力が何を埋めるか

下の表の「値」は**空欄**。測定するまで埋めない。合成デモの数値（`reference_results/`）を入れない。

| 空欄 | 何を入れるか | 出どころ | 値 |
|---|---|---|---|
| パルス→角度の校正 | `deg_per_us`、残差 RMS、校正版 | `calibration.json`（`calibrate`） | （空欄: 実機測定後） |
| 同じ指令の繰り返しで、どれだけ同じ場所へ動くか | 動作ごとの `repeat_sd_deg.observed_deg.pooled_sd`、動かさない試行の床（`observation_noise_floor.pooled_sd_deg`）を引いた `pooled_sd_minus_floor`、`return_error_deg_observed`。床と同じ大きさなら「再現性は検出雑音の下に隠れている」と書く | `session_summary.json` | （空欄） |
| 録音と再生の差 | `rms_deg_replay_vs_recording_{commanded,applied,observed}` | `session_summary.json` | （空欄） |
| 指令から観測までの総遅れ | `lag_ms_commanded_to_observed`（カメラ遅延とは書かない） | `session_summary.json` | （空欄） |
| 再生の時刻の守り方 | 締切からの遅れの p50 / p95 / 最大、ドリフト | `play` の標準出力 `timing` と `run_manifest.json` | （空欄） |
| 切断→再開 | 再開の可否、再送した古い行が拒否された数、再開後の最大速度 | `sweep-resume-*` の `command.jsonl` | （空欄） |
| 欠測の理由 | `observation_invalid_reasons` | `session_summary.json` | （空欄） |
| 写真 | 配線、固定、マーカー | 自分で選んだ写真だけ（顔・部屋・シリアル番号・位置情報つき EXIF を含めない） | （空欄） |

## 5. 公開してよい物 / しない物

- 公開してよい: `session_summary.json`、`calibration.json`、`validate` の出力、選んだ写真。
- 公開しない: カメラの生映像（顔・部屋・機器のシリアル・位置情報が写り得る）、`outputs/` の中のそのままのログ（公開前に目で確認する）。`.gitignore` は `outputs/` と、生映像・写真の拡張子（`*.mp4` `*.mov` `*.jpg` `*.heic` など）を除外している。
- `run_manifest.json` と各ログのヘッダ（`config.camera.source` など）には、`--out` に渡したパスがそのまま入ることがある（絶対パスだとユーザー名が入る）。上げる前に、ホームフォルダの絶対パスが残っていないか `grep -rn "$HOME" <フォルダ>` で確認する。
- 実測の結果は `results/` に置き、`reference_results/`（合成）と混ぜない。

## 6. 止まったとき

| 状況 | すること |
|---|---|
| 何か変だ | まず非常停止（電源の線）。次に USB。ソフトの `ESTOP` コマンドと `DISARM` はその後（ソフトの `ESTOP` は非常停止ボタンではない） |
| `STOP lease_expired`、`fault=1` | ホストが期限内に送れなかった。原因を調べてから、人が `CLEAR`（`console`）。自動では戻さない |
| `REJECTED ... reason=epoch` | 古い接続の行が届いた、または別の `HELLO` が入った。新しく `HELLO` からやり直す |
| ポートが開けない | 他のプロセスが握っていないか、ケーブルがデータ線つきか |
