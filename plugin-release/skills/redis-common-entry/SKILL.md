---
name: redis-common-entry
description: "使用 DR Bot 共用流程設計固定 Agent、核對原管理能力；建立、查詢、取消 Deer 子任務及拆解大任務；或沿原 Gateway Source RPC 保存續點。沿用原模型、MCP 與授權。"
---

# Redis 共同入口

## 當次工具版本核對

所有操作先按當次工具 schema 核參數；下文是原服務能力說明，不代表本回合工具已刷新。goal、include_plan、execution_mode、pending_only、receipt 的 execute 類型只在當次 schema 接受時使用。缺 batch_host_status 時不得猜 parent 建立新任務；缺 batch_receipt 時遇寫入失回保存原鍵並停止，不用 batch_status 猜原 batch。舊 schema 不接受 goal 時，可依原需求只保存 items，並明示原 goal 快照未保存；不得回報完整目標保存 PASS。

## DR Bot 共用入口

使用者要求 DR Bot、建立／管理固定 Agent，或讓接入平台的 Agent 共用 Bot 設計能力時，讀取 [DR Bot 工作流程](references/dr-bot.md)，由當前 Agent 本身完成設計，不另要求 Grok API。沿原名冊與受權管理工具；固定 Agent 建立、啟用、執行與停止分別驗收。沒有管理工具時完成設計並明示 DESIGNED_NOT_CREATED，不能用子任務登記冒固定 Agent 建立。Bot Designer 保持其原技能與職責。


## 大任務拆解：平台共用工作流程

使用者說「把大任務拆成小任務」「拆解並保存」「把這件事分成工作包」時，直接走以下既有平台流程。不要求使用者自行拆解、提供 API 名稱或逐個 Work 綁定宿主。適用範圍為同一已授權帳號、已連接原外掛的 Work；每次仍先核當次宿主與原帳號權限。

1. 用 `batch_host_status` 發現有效宿主，沿下方「每次開始先發現宿主」核當代心跳與 lifecycle。
2. **由當前 GPT／Work 當回合完成拆解**，不是把大目標當成已執行，也不是另叫模型服務。保留使用者原大目標作為 `goal`，為每個獨立子任務產生穩定唯一 `key`、可直接交辦且自包含的 `prompt`、可查核的 `acceptance_criteria`。依賴鏈要合在同一工作包或先只登記當前可獨立完成的批次，不能冒稱已有 DAG 排程。
3. 核當次 schema 支援 `goal` 後，使用全新 `operation_key`，呼叫原 `batch_task`，在同一 request 提交 `parent_id`、`goal`、`title`、`subagent_type: "general-purpose"` 與 `items`。若不支援，按上方版本核對只保存 items 並分列目標快照缺件。原 Holder 把目標快照與子任務一次保存到原 Deer SQL，不另建目標庫。僅送 goal 的規劃契約尚未保存；若先取得它，再以相同 goal／parent／operation_key 加 items 提交一次。
4. 寫入失回時保留同鍵，僅用 `batch_receipt` 查原筆。取得 batch ID 後，以新讀鍵 `batch_status` 帶 `include_plan: true`、`item_offset: 0`、`item_limit: 25` 查回；逐項核原 goal、子項 key／prompt／驗收標準一致。多於25項時以新讀鍵分頁核對。每批最多100個獨立子項，且整個請求仍受64KiB限制。
5. 回覆使用者大目標、拆解清單與原 batch ID，清楚回報「已保存、尚未執行」。使用者只要求拆解／發布時，不呼叫 Codex 執行。只有另有明確執行授權時，才沿下方原單項執行流程，並保留每項獨立回執。
6. 測試批次在查回後沿原 cancel_batch 取消，再用新讀鍵確認每項 cancelled／attempt=0，保留原目標快照。不要刪除原歷史或用新任務冒舊任務的續辦。


使用本技能隨附的 `scripts/functions_producer_v2.mjs` 作為原正式 RPC 的共用 client。它是供 Work 主動呼叫的程式入口，不是常駐 worker、模型前置 hook 或自喚醒成功證明。不要因載入本技能就發事件、登記任務、建立或續訂訂閱。

## 固定原鏈

遵循原八步：Work 自己決定並主動提交 → 原 Redis 收信 → 原訂閱通知 → 原 Receiver 查回 → 原 Holder 核權 → 原 Caller 喚同 Work → 同 Work 續辦並保存查回 → 受權接收鏈 ACK。

核權、Source CAS、finalizer 的唯一業務排隊都在第一步的原宿主內執行。不要用合成 redis_test_write 冒正式提交，也不要先直接送業務續辦，再讓 finalizer 排第二筆。不要新增 MCP、Redis、連線、信箱、訂閱或排程來補接線。

## 準備

1. 核當前工具的 request/query schema。使用原 `gateway_source_rpc_request`、`gateway_source_rpc_query`，固定 `gateway_internal_v1`。保持原部署核權及 Source／once fences。
2. 先核本回合工具 schema。若工具要求 `link_id`，使用 `connectionBinding: "explicit-link"` 並傳既有已選定連線的 `linkId`；若工具不接受 `link_id`，使用 `connectionBinding: "host-bound"`，完全不傳 `linkId`，由平台原綁定選擇連線。必須在呼叫前按真實 schema 決定，不得在拒絕後切換模式或帳戶。多連線且無原設定綁定時不猜測。
3. 在本 Work 的執行環境，只讀 `CODEX_THREAD_ID` 與可選 `CODEX_ENVIRONMENT_ID`，不要列印完整環境或密鑰。這些只是線索，不是官方 reference 或授權。
4. 取得 Work 自己在當回合準備的原 canonical `request_json`，包含原 `v/id/source/method/params`。不要由本技能補造 Source 權限、USER 訊息 ID、PLAN、路由或宿主簽章。新請求與既有 UNKNOWN 必須分開；UNKNOWN 只能查原筆。

## 呼叫同一入口

在支援 JavaScript module 的原執行環境使用：

```js
import {createCommonWorkEntry} from './scripts/functions_producer_v2.mjs';
// 本例對應當前不接受 link_id 的平台綁定工具。
const entry = createCommonWorkEntry({tools, connectionBinding: "host-bound", runtimeIdentifiers});
const ticket = entry.prepare({requestJson: originalCanonicalRequestJson});
```

以實際技能根路徑載入檔案，不依賴當前目錄。若使用 functions.exec，沿原相容流程載入同一 helper 宣告並移除 export；保留原函式內容、工具名稱及 bytes。不得在 prepare 階段偷偷 submit。

`prepare` 不呼叫工具、不產生權威。register 沿原 self-report 格式提交；read/save 不附登記 sideband，原 Work 內容保持原 bytes。原始請求、內容摘要及原宿主要求的當回合完整 PLAN/REQUEST 必須對位。工具存在、格式通過、舊版簽章或其他 Work 的證據都不能代替本次核權。

在既有授權及原證據契約滿足後，由 Work 明確呼叫：

```js
const reply = await entry.submit(ticket);
const readback = await entry.query(ticket);
```

submit 只呼叫原 request 工具，不新增第二個傳輸或業務事件。query 只查原 request ID。原工具回件原樣保留，不把入箱、HTTP 200、verified_host_response 名稱或 Source 回執單獨當作喚回成功。

## 失回與恢復

- 同一入口實例對同 nonce 只准一次 submit；含併發、拒絕或拋錯。後續只 query 原鍵。
- 跨回合保留精確 request_json、nonce、Source、選定連線、回件及下一步，不另建權威記憶庫。
- 重新載入時，用留存的原 bytes prepare，然後只 query。不要以重建 client 或換 nonce 重送 UNKNOWN。持久防重仍由原服務負責。
- 撤權、逾期、FORBIDDEN、CONFLICT 或目標不符時停止相依效果；不切路由、環境、Work 或帳戶重試。
- 缺 Source/原生 provider/宿主能力時具體標 NOT_BOUND，不用 fixture 或離線身份補過。

完成交付時區分：程式載入、Work 真提交與同鍵查回、Source 保存、唯一排隊、同 Work 新 turn、結果保存、transport ACK 與 person ACK。只有實際收到並核驗相應回件才標通過。不得替使用者或其他人代簽。

## Deer 平台共用入口

本節適用於使用者要求拆解大任務、登記子任務、查詢結果或取消未執行項目。直接使用原平台 batch 工具，不轉成 Source RPC，也不要求使用者提供函式或程式名稱。相同已授權 OAuth 帳號且已連接本外掛的 Work 共用平台帳號範圍；不是每個 Work 各自持有一張派工權限，也不授權其他帳號。

### 每次開始先發現宿主

1. 發現並呼叫原 `batch_host_status`，從當次回件取得 `parent_id`、`generation`、`expires_at` 與 `worker_heartbeat_verified`。不得硬編碼其他回合的 parent 或期限。
2. 僅當宿主仍有效且簽章心跳已驗時才建立新任務。有數字 expires_at 時核其期限；只有 lifecycle=owner-platform-service-v1 且 expires_at=null 時，代表帳號平台服務持續至明確停止／撤回，不能由 Work 自行把過期的測試宿主改成此模式。無工具回 `TOOL_NOT_FOUND`；無有效宿主回 `HOST_NOT_READY`，保留原始回件，不用離線測具代替。
3. 本流程不會因載入就自動登記、執行或續期。建立任務需使用者當前指示或既有未撤回的任務授權。

### 拆解與登記

由當前 GPT 按大任務內容拆成獨立工作，為每項提供唯一 `key`、`prompt` 及 `acceptance_criteria`。有依賴的項目分批登記，不假設此批次 API 具備依賴排程。使用全新 `operation_key` 呼叫 `batch_task`，帶當次 `parent_id`、`title`、`subagent_type: "general-purpose"` 與 `items`。

宿主版本如支援 `goal`，一併保存原大任務；僅送 goal 的 `AWAITING_CURRENT_GPT_DECOMPOSITION` 是要求當前 GPT 補 items，並非已登記成功。以當前工具及真實回件核對版本支援，不宣稱舊工具 schema 已更新。

這個動作只保存 pending 子任務，`automatic_dispatch: false`。不得把登記成功回報成 Codex 已啟動。保存原 operation key、request ID、batch ID、generation、原始回執與下一步。

### 查詢、取消及失回

- 用 `batch_status` 帶原 batch ID、parent 與全新讀取 operation key 取得最新快照；`include_result: true` 查已保存結果；要求項目頁或結果時同時帶 `item_offset: 0` 與 `item_limit: 25`，避免原 Holder 拒絕未指定分頁的請求。支援時 `include_plan: true` 查原目標與分頁子任務。
- 建立或取消失回時，使用 `batch_receipt` 查同一 operation key 及原 operation_kind；不得換鍵重送 UNKNOWN。單次查詢的相同讀取鍵只恢復原快照。
- 原 `cancel_batch` 預設只接受整批都未開始的批次。若同批已有執行紀錄，要取消其餘待辦，須明確傳 `pending_only: true`；原宿主只取消 status=pending、attempt=0、無租約／啟動時間且不在原 native attempt fence 的項目。已完成結果與執行中項目保留。取消後以全新讀鍵獨立查回，不能將它當作停止已啟動的 Codex。
- 撤權、過期、帳號不符、宿主未綁定或簽章不符時停止相依操作，不切換帳號或沿用歷史 parent。

### 經平台明確執行單項 Codex

1. 宿主回件的 `execution_available` 必須為 true，且心跳有效。先以 batch_status 分頁取得原 batch ID 與原 item ID；只執行使用者授權、尚未開始的 pending 項目。
2. 使用全新執行 operation_key，呼叫 `batch_execute`；若本回合尚未載入此新增工具，先核當次 schema 接受 execution_mode／batch_id／item_id，再使用**同一原平台** `batch_task` 的明確執行模式。不接受則回 EXECUTION_TOOL_SCHEMA_NOT_AVAILABLE，不嘗試傳未知欄位。兩者支援時的 request 相同：

```json
{
  "parent_id": "<當次宿主 parent>",
  "operation_key": "<全新執行鍵>",
  "execution_mode": "codex_once",
  "batch_id": "<原 batch ID>",
  "item_id": "<原 item ID>",
  "timeout_seconds": 90
}
```

執行模式不接受新 prompt、items、shell、路徑或可執行檔；宿主從原 Deer 讀取已保存內容，核原完整 binding 與 payloadDigest，沿原 repository 預留一次後才啟動。普通 goal/items 登記仍不派工。不要因看到執行能力就自動把所有 pending 項目啟動。

3. 如回 PENDING_OR_UNKNOWN，保存原鍵，只用 `batch_receipt` 加 `operation_kind: "execute"` 查回；不可換鍵、換 item 或重建 batch 來重送同一次未知執行。執行開始後的崩潰或失回保留原防重紀錄與原 SQL 狀態。
4. 成功回件應有 `original_result_saved: true` 與 `observed_execution_verified: true`。再用獨立 batch_status，帶新讀鍵、`item_offset: 0`、`item_limit: 25`、`include_result: true`，核原 item succeeded、attempt=1 及原結果。外層 native_execution_verified=false 表示管理端不提供原生程序即時狀態；不要因此忽略已保存的執行回執，也不把只入隊當成執行成功。
5. 目前宿主使用既有 Codex CLI 登入、ephemeral/read-only 設定、空工作目錄與最長90秒的單項執行。這不是 AIO 容器沙盒；不能宣稱檔案寫入、任意長時間執行、全平台沙盒或24小時可用。Codespace 停止／休眠時宿主也不能接單。cancel_batch 只取消未開始項目；宿主 STOP／撤回會使受管 Codex 程序停止。

不得把歷史單次成功冒成本 Work 或新 Work 驗收。沒有實際執行能力時回 EXECUTION_NOT_BOUND；工具缺失回 TOOL_NOT_FOUND，不拼裝另一個 HTTP client、服務或模擬回執。接受標準與獨立 QA 判定另外保存，不以 CLI 正常完成代簽 QA。



## 多項子任務受控執行與取消剩餘待辦（v1.0.7）

使用者明確授權執行整批時，在同一平台 batch 保存獨立子項後，由當前 Work 逐項沿上節 codex_once 執行。每項使用獨立執行鍵，先查原項目 pending；完成後查原回執及以新讀鍵查 SQL 結果，再執行下一項。任一項 UNKNOWN、拒絕或撤權時停止相依啟動，只查原筆；不得換鍵重跑。這是當前 Work 的有界逐項協調，未提供背景自動派工、相依 DAG、Router 動態選路或 AIO 容器隔離。

同一批次部分成功後，若使用者要求取消剩餘待辦，使用原 cancel_batch：

```json
{"parent_id":"<當次宿主parent>","batch_id":"<原batch>","operation_key":"<新取消鍵>","pending_only":true}
```

本版原入口已支持 pending_only。舊工具 schema 若拒絕此欄位，回報版本未刷新；不刪欄位改作整批取消、不另造 HTTP client。回件必須有 cancelled_pending_item_ids 與原 counts；running_items_stopped=false。再以獨立 batch_status(include_result=true,item_offset=0,item_limit=25) 核取消項 cancelled/attempt=0、已完成項結果及 attempt=1 全部保留。取消失回只以 batch_receipt(operation_kind=cancel) 查同鍵。舊取消 UNKNOWN 保史，不冒作本新模式成功。

單項執行中停止使用下節的原共用入口；取消剩餘待辦與停止在途項目是兩個明確操作，不能為停止一項而擅停整個帳號平台服務。



## 停止單一執行中 Codex 子任務（v1.0.9）

只在使用者明確授權停止指定項目時操作。先核當次宿主、原 batch/item 與原帳號範圍。優先使用原 batch_stop；若該新增工具尚未載入，本版原 MCP 的 batch_task 已發布 Submit/Execute/Stop 聯集，停止模式固定如下。不另建 HTTP client 或傳送任意 PID。

```json
{"parent_id":"<當次宿主parent>","batch_id":"<原batch>","item_id":"<指定在途item>","operation_key":"<全新停止鍵>","execution_mode":"stop_codex_once"}
```

只接受原項目ID，沒有 prompt、owner、PID、命令、路徑或新目標參數。若當次工具前置 schema 拒絕本模式，回 STOP_TOOL_SCHEMA_NOT_AVAILABLE，不能刪欄位換成取消、換帳號或其他通道。

1. 原 stop 與執行共用 execute 動作的核權和防重，所以失回用 batch_receipt(operation_kind="execute") 查同一停止鍵；不換鍵重送。保存原執行鍵及停止鍵的對應。
2. 成功必須有 status=STOPPED、process_group_stopped=true、observed_descendants_stopped=true、original_terminal_state_saved=true、stopped_by_this_request=true；僅接收／入隊或 exit_code=0 都不是停止成功證明。Codex 接 SIGTERM 可正常退出0，仍按停止回執和SQL終態判斷，不冒作任務完成。
3. 再用新讀鍵 batch_status(include_result=true,item_offset=0,item_limit=25)，核指定項目 cancelled、attempt=1、stop_reason=stopped_by_request；沒有捏造模型結果，原結果保持null。對照同批其他項目仍在執行，或其後正常成功保存。未開始的取消項則是 cancelled/attempt=0。
4. ALREADY_TERMINAL_NOT_STOPPED／ALREADY_EXITED_QUERY_RESULT 表示已終態或完成競態，查原結果，不能說本次殺停。STOP_NOT_OWNED_QUERY_ONLY 表示本宿主沒有當前受管程序；保留原SQL與once，不能用舊PID找回或重建程序。STOP_ALREADY_REQUESTED_QUERY_ONLY 只查原停止鍵。
5. 原宿主保留單一收信worker，允許執行時處理查詢／停止；最多兩項原生執行，仍受原batch max_live_items/max_running_items限制。要同時執行兩個獨立項目，使用者須有明確執行授權且建立時原batch容量允許。這不是背景自動派工、DAG或Router自動選路。

停止僅使用本程序保留的 Popen 與已觀測的後代程序身份，沿原SQL writer鎖及完整binding/intent保存終態；不關整個宿主、不撤其他任務、不清once fence、不代簽QA。此功能不是惡意程式隔離或AIO真沙盒上線證明。

