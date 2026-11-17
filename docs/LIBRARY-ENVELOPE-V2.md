# Deer × ChatGPT Library 信箱信封 v2（dle/2 + dlc/2）

- 版本：v2，2026-11-17 13:10（台北時間）。v1 原文保留於 `CONTRACT-v1.md`。
- 參考實作：`/workspace/deer/library_envelope_v2.py`（本機分支 `grokbot/library-envelope-v2`，基於 `origin/test/vercel-fastapi-protected-preview` 11d55e0；未 push）。測試：`tests/test_library_envelope_v2.py`。
- 傳輸：**只用原 Deer 正式信箱** `gateway_inbox_send / list / claim / ack / delivery_status` 於 `gateway_internal_v1`；簽章回覆仍走原 Source RPC／PR #62 machine ingress（未接線）。不新增信箱、Store、Worker、Issuer。
- 信封**不證明身分、不授予 Library 權限**：`validate()` 永遠回 `identity_verified:false`、`library_authorized:false`。可信身分只來自原宿主簽章（PR #61 AttestedOrigin：owner、client_id、thread_id）。未取得可信 Source 一律 DENY。

## 0. 已有的真實收發回執（只是傳輸層，未驗身分、無簽章）

| seq | 方向 | operation_key | event_id | sender_context |
|---|---|---|---|---|
| 93 | Grok → 信箱 | `grokbot-library-handshake-20261117-277529` | `4b95b5c7c1974c1b2db91ae3c8d28004ee8a65938279ad415ce93dafbaccff96` | `work_identity_verified:false`，`NO_PLATFORM_ORIGIN_ATTESTATION` |
| 94 | ChatGPT → 信箱 | `gpt-library-handshake-reply-20261117-277529-01` | `a47204cd91c8085548b55eeee09c391a96aef1921b1b33f6c606ba6999d5c5df` | `work_identity_verified:false`，`CONVERSATION_METADATA_ONLY_NOT_WORK_IDENTITY` |

兩則都是 `state:"available"`，未 claim／ack。保留原樣；`TRANSPORT_HANDSHAKE=PASS`，`TRUSTED_HANDSHAKE=NOT_RUN`。

## 1. 長度限制（第 1 項）

- 伺服器：`inbox.py` 要求 `1 <= len(message) <= 1024`（以字元計）。
- 硬上限 **1000 字元**（`HARD_LIMIT`），建議 **800**（`RECOMMENDED_LIMIT`，`encode()` 預設值）。都包含所有 ID 欄位；以 canonical JSON（`sort_keys`、`separators=(",",":")`、`ensure_ascii=False`）的 `len()` 計。
- 欄位上限的設計保證：**所有 ID 都填到上限的最壞情況 ≤ 1000**，並在測試中實測：

| 範例（實測） | 字元 |
|---|---|
| search 請求 | 292 |
| read 請求 | 285 |
| status 請求 | 263 |
| create 請求（含 61 字元 inline JSON） | 501 |
| update 請求（sealed PNG 引用） | 394 |
| committed 結果（含 64hex `re` 與 `h`） | 436 |
| **最壞情況 create 請求**（所有 ID 上限、80 字元 `nm`） | **770** |
| **最壞情況結果**（所有 ID 上限、`st=UNKNOWN_QUERY_ONLY`、`re`+`h`） | **914**（< 1000，> 800：只有極端長 ID 才會超過建議值） |
| chunk：3,014 字中英混合 → 9 段 | 每段 ≤ 747 |
| chunk：5,400 字 ASCII → 10 段 | 每段 ≤ 799 |

## 2. 統一信封 `dle/2`（第 2 項）

緊湊鍵（左）對應使用者要求的欄位名（右）。必填 12 個，選填 8 個；**多一個或少一個欄位都拒絕**。

| 鍵 | 欄位 | 必填 | 規則 |
|---|---|---|---|
| `s` | schema | ✔ | 固定 `dle/2` |
| `t` | test_id | ✔ | `[A-Za-z0-9_-]{1,64}`；本輪 `grokbot-library-handshake-20261117-277529` |
| `k` | operation_key | ✔ | `[A-Za-z0-9_-]{1,80}`（同 inbox identifier ∩ PR #61/62 KEY）。**一個操作一把鍵，結果與 status 都沿用原 k** |
| `f` | sender | ✔ | `agent[:binding]`，`[a-z0-9][a-z0-9_-]{0,15}(:[A-Za-z0-9_-]{1,48})?`。**自報**；可信寄件身分以 Deer `sender_context` 與宿主簽章為準 |
| `r` | receiver | ✔ | 同格式，例如 `gpt-work:<thread_ref>`；`thread_ref` 只能來自宿主簽章綁定，不得自填 |
| `a` | action | ✔ | `search` / `read` / `create` / `update` / `status`（PR #7 工具名；PR #61 對應 catalog/read/create/update/status，PR #62 對應 catalog_search/file_read/create/update/status） |
| `fr` | file_ref | ✔ | 見 §5；search=`catalog`，status=`status` |
| `v` | version | ✔ | search/status=`none`；read=與 `fr` 內版本相同；create=`create-only`；update=**原件目前版本**（必須等於 `fr` 的 `@version`） |
| `ia` | issued_at | ✔ | Unix 秒；超過現在 60 秒拒絕 |
| `ttl` | TTL | ✔ | 秒；讀類 ≤3600，寫入 ≤900；`now >= ia+ttl` 即過期拒絕。宿主 claim 另有 ≤120 秒 lease（PR #62） |
| `st` | status | ✔ | 請求固定 `req`；結果 `ok`／`committed`／`pending`／`denied`／`NOT_BOUND`／`UNKNOWN_QUERY_ONLY` |
| `pr` | payload_ref | ✔ | `-`（無）、`in:<sha256>`（內容在 `p`）、`ck:<sha256>:<total>`（分段）、或受控檔案引用（§5） |
| `p` | inline payload | 選 | 只限文字，`sha256(p)` 必須等於 `pr` |
| `m` | mime_type | 選 | create/update 必填；文字 MIME 同 PR #61 TEXT_TYPES；二進位只能配受控引用 |
| `nm` | name | 選 | create 必填；1–80 字，不得有 `/ \ NUL . ..` |
| `q` / `c` / `n` | query / cursor / limit | 選 | search 用；q ≤120、c ≤160、n 1–50（同 PR #7/#62） |
| `re` | in_reply_to_event_id | 結果必填 | 原請求在信箱的 64hex event_id |
| `h` | request_digest | 選 | 結果綁定原請求：`sha256(canonical(請求去掉 st/re/h))`（`request_digest()`） |

範例（read 請求，285 字元）：

```json
{"a":"read","f":"grok-bot","fr":"library:file_6f3a9c2e4b1d4e7f@v1","ia":1794890000,"k":"grokbot-library-handshake-20261117-277529-a3","pr":"-","r":"gpt-work:thr_0123456789abcdef0123456789abcdef","s":"dle/2","st":"req","t":"grokbot-library-handshake-20261117-277529","ttl":600,"v":"v1"}
```

解碼（`decode()`）要求：恰為 canonical JSON、無重複鍵、無 NaN、通過全部驗證。

## 3. 動作對應原系統（第 3 項）

| action | Grok 端（信箱） | 收件 Work 端 | 原生執行（未接線） | 正式回覆 |
|---|---|---|---|---|
| search | `gateway_inbox_send` | `gateway_inbox_list`→`claim` | PR #7 `library_catalog_search` / PR #61 `catalog` / PR #62 `catalog_search` | 原宿主簽章（Source RPC / PR #62 `validate_reply`） |
| read | 同上 | 同上 | `library_item_read` / `read` / `file_read` | 同上 |
| create | 同上 | 同上 | `library_item_create` / `create`（create-only） | 同上＋`deer-library-original-source-receipt-v1` |
| update | 同上 | 同上 | `library_item_update` / `update`（原生 CAS） | 同上 |
| status | 同上 | 同上 | `library_write_status(k)` / `status` | 同上 |

`to_pr61_request(env, content)` 把信封一對一轉成 PR #61 的 10 欄 `deer-library-original-request-v1`（測試已驗欄位集合完全一致）。每封信的**信箱 operation_key**：請求 = `k`；分段 = `k-pNN`；status 查詢 = `k-qNN`；結果 = `k-rNN`（`inbox_key_for()`）。信封內 `k` 永遠是原鍵。

## 4. 長文字分段 `dlc/2`（第 4 項）

每段一封信：

```json
{"h":"<sha256(x)>","i":<part_no>,"k":"<原 operation_key>","n":<total>,"s":"dlc/2","w":"<sha256(全文)>","x":"<本段文字>"}
```

- 主信封 `pr = ck:<全文 sha256>:<total>`，2 ≤ total ≤ 32；每段 ≤ 800 字元（含所有 ID 和雜湊）。
- 收件端 `Reassembler`：每段驗 `sha256(x)==h`、`k`/`w`/`n` 與主信封一致；重複段完全相同可容忍，內容不同即 `CHUNK_CONFLICT`。
- **收齊且全文 sha256 正確才處理**；只在記憶體中組合，不寫入任何 Store。
- 缺段：期限內回 `WAITING` 並列出缺的段號和要查的信箱鍵（`k-pNN`），以 `gateway_inbox_list` 查原鍵；過期回 `UNKNOWN_QUERY_ONLY`。**`resend:false`：不要求重送、不建新鍵。**
- 期限 = 主信封 `ia+ttl`；超過才組齊也一律 `UNKNOWN_QUERY_ONLY`。

## 5. 受控檔案引用（第 5 項）

| 形式 | 用途 |
|---|---|
| `library:<id>@<version>` | ChatGPT Library 原件（id ≤96、版本 ≤48） |
| `drive:<fileId>@<sha256>` | Google Drive 檔（內容雜湊鎖定） |
| `dropbox:id:<id>@<sha256>` | Dropbox 檔 |
| `sealed:<handle>@<sha256>` | 來源端預備的不透明 handle（PR #61 `prepared`）；圖片、ZIP、神經圖寫入只能用這個 |
| `catalog` / `status` / `-` | 特殊目標／無 |

- 圖片、ZIP、PDF、神經圖：只傳引用，`p` 不可放二進位；寫入轉 PR #61 時只接受 `sealed:`（Drive／Dropbox 引用須先由持權來源轉成 sealed handle）。
- 全部字串欄位（含 `p`、分段 `x`）掃描並拒絕：任何 URL（`scheme://`）、`data:` URI、`Bearer`、JWT、`sk-`/`ghp_`/`xox*-`/`AKIA`/`ya29.` 類金鑰、私鑰 PEM、`password=`/`cookie:`/`access_token=` 等、≥200 字元 base64 串、控制字元。路徑（`/`、`\`）也不能出現在 id。

## 6. 寫入規則（第 7 項）

1. **原件版本比對**：update 的 `v` 必須等於 `fr` 中的 `@version`，且原生來源要做 CAS（PR #61 `atomic_version_write=true`，否則 `LIBRARY_SOURCE_ATOMIC_WRITE_UNAVAILABLE` → DENY）。create 必須 `create-only`。
2. **單次操作鍵**：`OperationJournal.begin()` 拒絕同一把鍵第二次送出；同一意圖（a、fr、v、pr）還在 sent／UNKNOWN 時也拒絕換新鍵。
3. **撤權檢查**：在宿主端執行前後各檢查一次（PR #61 `check_current`、PR #62 `verify_port` before/after）；信封不能代替。
4. **獨立讀回**：`committed` 後由寄件方另發 `read`（新 k）讀回 `@新版本`，內容 sha256 與 `pr` 相同才 PASS。

## 7. 遺失與 UNKNOWN（第 8 項）

- 結果遺失、逾時、`pending`、`UNKNOWN_QUERY_ONLY`：`OperationJournal.next_step()` 回 `QUERY_ORIGINAL_KEY`。做法：`gateway_inbox_list` 找 `k-rNN`；寫入另發 `status` 請求（envelope `k` = 原鍵，信箱鍵 `k-qNN`）或 `library_write_status(k)`。
- 永不以新鍵重送同一寫入；永不重送不確定的寫入。
- 失敗／UNKNOWN 原樣記錄，不覆蓋歷史 PASS。

## 8. 第 6、9 項尚缺（誰要給什麼）

| # | 缺件 | 由誰 | 給什麼 |
|---|---|---|---|
| 1 | 可信收件者身分 | 原 Gateway 工程 Owner（使用者核准）在原 Codespace | 宿主簽章的 AttestedOrigin（owner、client_id、**thread_id**），經 `library-original-bridge-v1` signed-machine mode 下發；目前 NOT_WIRED |
| 2 | 通知訂閱 | 那位 ChatGPT Work（在其對話內） | 對 `gateway_internal_v1` 執行 `events/subscribe`（webhook）；目前 count=0。訂閱只是喚醒，不是身分 |
| 3 | 寄件者 client_id | Deer 程式碼（需使用者核准 PR + 部署） | 讓 `sender_context` 記錄已驗證的 azp/client_id 指紋；目前只看得到 conversation fingerprint |
| 4 | 簽章回覆 | 原宿主 + Worker | Source RPC 加 library mode 及宿主簽章回覆；目前 `host_reply_verified:false`、`worker_heartbeat_verified:false` |
| 5 | Library 讀寫權 | ChatGPT Work 自身 Files 權限 + 原 SourceHolder | Reader/Writer Source 綁定（PR #8 `original_library_host_binding`）；ChatGPT Files producer NOT_BOUND |
| 6 | 原生 CAS 與同鍵回執 | 原 Writer | 證明 create-if-absent、版本 CAS、同鍵持久回執；未證明前 update DENY |
| 7 | Library 工具上線 | 使用者核准部署時段 | alias 由 `dpl_9JhifDgs…`（5bab77a）改到含 PR #7/#8 的部署並綁定 resolvers；本輪禁止，維持不動 |

在 1、4、5 補齊前，所有 Library 動作 = **DENY / NOT_BOUND**。
