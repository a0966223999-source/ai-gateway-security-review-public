---
name: redis-common-entry
description: "透過原平台建立、查詢、取消 Deer 子任務及拆解大任務；或提交、查回原 Gateway Source RPC、保存 Work 續點及使用共用 Redis 信箱。沿用原 MCP 與授權。"
---

# Redis 共同入口

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
2. 僅當宿主期限尚有效且簽章心跳已驗時才建立新任務。無工具回 `TOOL_NOT_FOUND`；無有效宿主回 `HOST_NOT_READY`，保留原始回件，不用離線測具代替。
3. 本流程不會因載入就自動登記、執行或續期。建立任務需使用者當前指示或既有未撤回的任務授權。

### 拆解與登記

由當前 GPT 按大任務內容拆成獨立工作，為每項提供唯一 `key`、`prompt` 及 `acceptance_criteria`。有依賴的項目分批登記，不假設此批次 API 具備依賴排程。使用全新 `operation_key` 呼叫 `batch_task`，帶當次 `parent_id`、`title`、`subagent_type: "general-purpose"` 與 `items`。

宿主版本如支援 `goal`，一併保存原大任務；僅送 goal 的 `AWAITING_CURRENT_GPT_DECOMPOSITION` 是要求當前 GPT 補 items，並非已登記成功。以當前工具及真實回件核對版本支援，不宣稱舊工具 schema 已更新。

這個動作只保存 pending 子任務，`automatic_dispatch: false`。不得把登記成功回報成 Codex 已啟動。保存原 operation key、request ID、batch ID、generation、原始回執與下一步。

### 查詢、取消及失回

- 用 `batch_status` 帶原 batch ID、parent 與全新讀取 operation key 取得最新快照；`include_result: true` 查已保存結果。支援時 `include_plan: true` 查原目標與分頁子任務。
- 建立或取消失回時，使用 `batch_receipt` 查同一 operation key 及原 operation_kind；不得換鍵重送 UNKNOWN。單次查詢的相同讀取鍵只恢復原快照。
- 用 `cancel_batch` 取消未開始項目；取消完成須以獨立查詢讀回。此工具不等於停止已啟動的 Codex 程序。
- 撤權、過期、帳號不符、宿主未綁定或簽章不符時停止相依操作，不切換帳號或沿用歷史 parent。

### Codex 執行邊界

已保存任務不會自行啟動 Codex。只有當前平台實際提供並已驗證的執行工具才可使用；不得自行拼裝 shell、SSH 或模擬執行回執。找不到執行工具，明確回 `EXECUTION_NOT_BOUND`。歷史宿主單次啟動成功只能證明該次執行，不能證明所有 Work 皆可透過平台啟動。分開回報登記、執行、結果保存及 GPT 查回狀態。
