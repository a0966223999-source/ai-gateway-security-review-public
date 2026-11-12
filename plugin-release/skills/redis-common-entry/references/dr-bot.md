# DR Bot：由當前 Agent 使用的共用工作流程

## 模型與入口

用呼叫者當前已具備的推理能力完成 DR Bot 工作，不要求 Grok、API key、新模型服務或另一套 Registry。這個流程不會提供模型能力；接入平台仍須讓呼叫者能讀到本流程及使用原受權工具。不要宣稱所有外部 Agent 已自動安裝，或把呼叫者的訂閱登入憑證轉交 Deer。

沿原 Deer 的名冊、技能、任務與權限來源。Bot Designer 沿原技能負責專用 Bot 的設計和監督，不複製其本地 OfflineRegistry，也不覆蓋原技能。工作室分工為 DR 管固定角色、固定角色按授權使用 Bot Designer 準備一次性工作；這是平台政策，不是上游已內建的權限機制。

## 收到建立或修改 Agent 的要求

1. 讀本次使用者目標、現有名冊、目標 Agent 的原版本及呼叫者授權。若有正式名冊讀取工具，先查重；沒有時標 `REGISTRY_NOT_BOUND`，仍完成設計，但不得聲稱查重完成或 Agent 已建立。不要求使用者提供程式或 API 名稱，從可見原工具、原碼與部署資料定位。
2. 由當前 Agent 產生下列設計卡。需求已清楚時直接完成；只對會改變用途或權限且無法由上下文確定的缺件提問。不把設計文字中的權限當成真實 grant。

| 欄位 | 必填內容 |
|---|---|
| name / purpose | 穩定名稱、唯一主要職責 |
| lifetime | fixed 或 one-shot；工作結束與停用條件 |
| input / output | 自包含輸入與可核對輸出 |
| voice / anti_jobs | 語氣及明確不得執行的工作 |
| requested_tools / authority_source | 所需工具、原權限來源；未核實就標 UNKNOWN |
| delegation | 可否派工與是否能再建立 Agent；必須由原平台核權，不由角色卡授予 |
| acceptance | 至少一個正常案例、一個越權／失敗案例 |
| stop / handoff / rollback | 停止、交接與版本恢復方法 |
| provenance | 原需求、來源版本與已觀察到的回執 |

3. 固定角色：只有當次真可呼叫、具原帳號授權的 Agent 建立／修改工具才可寫原名冊。建立後用獨立讀取核同一 Bot ID 與規格。修改先核原版本及原工具是否支持競爭更新保護；不支持時保留設計差額，標 `UPDATE_VERSION_GUARD_NOT_BOUND`。沒有管理工具則回 `DESIGNED_NOT_CREATED`，指出具體缺少的名冊讀／建／改能力；不要改用本地檔案、batch item 或 Redis 訊息冒充固定 Agent。
4. 一次性工作：若使用者授權登記或執行，可沿 SKILL.md 的原 batch 流程保存自包含工作與驗收條件。只要求設計就不登記。`batch_task` 保存的是子任務，不能當固定 Agent 建立。執行必須確認當次工具 schema 真接受 execution_mode 或有 batch_execute；若舊 schema 不接受，回 `EXECUTION_TOOL_SCHEMA_NOT_AVAILABLE`，不可另造 HTTP client、偷偷加欄位或借其他 Work 執行。
5. 每次開始效果前核當次宿主心跳、權限、期限和停止／撤回。寫入失回只查原操作鍵；不換鍵重送。撤權、版本衝突及 UNKNOWN 停相依效果。
6. 需要停止時先分清固定 Agent 停用、在途工作停止與待辦取消。只用原工具已提供的操作。cancel_batch 不能停止 running；不能為停一項擅停整個平台服務。原管理／單項停止工具未綁時標 `STOP_NOT_BOUND`，不能宣稱清理完成。
7. 交付時分開報告：DESIGNED、REGISTRY_SAVED、READBACK_VERIFIED、EXECUTED、STOP_VERIFIED 與獨立驗收。只有收到相應真回件才升格；缺件保留卡片與下一步。自行測試不能代簽獨立 QA。

## 原共用角色管理工具

1. 先核當次真的有 `agent_manage`、`agent_management_receipt`，再用原 `batch_host_status` 核有效宿主與簽章心跳。缺工具仍回 DESIGNED_NOT_CREATED，不拼另一個 HTTP client 或借用私鑰。
2. 只使用原 Gateway 互動登入核出的具名帳號、原會話及明確管理同意。範圍由宿主選定角色名稱、list/read/create及期限；工具參數不能指定owner、grant、網址或憑證。缺同意、到期、撤回時停止，不能用續點link、信箱通知、角色文字或舊測試批准升權。
3. 當次schema支援時，呼叫 `agent_manage(request={kind:"list",operation_key:"<全新讀鍵>"})` 查授權範圍內名冊；單項讀取使用 kind:"read"、name及新讀鍵。這不是全平台Agent清單。
4. 使用者要求建立且原同意有效時，以 kind:"create"、name、description、soul、全新operation_key建立原定義。現版固定 tool_groups、skills、allowed_subagents皆為空，不接受要求工具／技能／派工權限的額外欄位；如需求需要這些能力，保留設計並明示尚未接入，不能刪去需求後宣稱全功能完成。
5. 保存原create_operation_key、payload_sha256／create_payload_sha256與request_id。建立回REGISTRY_SAVED_READBACK_REQUIRED後，另用全新讀鍵kind:"read"核同一名稱、description、soul與三個空權限陣列。名稱是原帳號名冊內的身份，不捏造另一個Bot ID。
6. 寫入失回只呼叫 `agent_management_receipt(operation_key:"<原鍵>",kind:"create")` 查原筆，不能換鍵重送。其historical_only=true、authority=false僅稽核摘要，不含當前角色內容，也不續權。有效同意內可用kind:"receipt"、name、原create_operation_key、payload_sha256及新查詢鍵核原日誌；CONFIRMED後仍需獨立完整read，不能用日誌代替讀回。
7. 受控測試完成或使用者要求撤權時，在原Gateway撤回該筆同意，再以新讀鍵核拒絕。撤回管理權不等於刪除角色、停止在途任務或固定Agent停用；未授權刪除就保留零權限測試定義。既有grant與本次工程成功不授權下一次工作。分列工程驗收、獨立QA及執行／停止狀態。

## 原 Grok 操作的對應

| 上游用語 | 本平台處理 |
|---|---|
| 由 Grok 設計 Bot | 當前接入 Agent 自行設計 |
| CreateAgent | 原 Deer 受權 Agent 管理工具；未綁就 DESIGNED_NOT_CREATED |
| setup-pstack | 核現有執行能力與驗收條件，不自動安裝供應商套件 |
| pstack 驗證 | 原任務回執、結果查回與獨立驗收分列 |

## 已核能力與待接範圍

原共用 MCP 已提供 `agent_manage` 與 `agent_management_receipt`；2026-11-13 原帳號具名600秒授權下，真實建立、獨立完整讀回、原寫入日誌 CONFIRMED、撤權後新讀拒絕已通過工程驗收。這是固定角色定義管理，不是固定 Agent 已執行。版本修改、刪除、啟用及固定 Agent 執行／停止仍未綁定；既有 batch 的 Codex 單項停止另按 SKILL.md 操作。工具版本、宿主及當前授權每次重核。原 `/api/agents` 路由的靜態原碼不授權直接打網址、開 agents_api.enabled 或移用其他會話身分。

## 來源

改編依據為使用者保存的 `/作業/dr-eggbot.md`（615 bytes）與 https://github.com/elie222/botdirectory.ai/blob/main/bots/dr-eggbot.md 。這是本平台適配流程，不是宣稱還原另有但未取得的完整 DR Bot 執行程式。上游 MIT 授權及著作權聲明見同目錄 `dr-bot-LICENSE.txt`。




